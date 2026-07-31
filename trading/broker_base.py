"""Broker adapter ABC, typed results, retry wrapper, and live-gate (Phase 10).

Every external broker (paper engine, Alpaca, …) implements
:class:`BrokerAdapter`. Callers never touch a vendor SDK directly; the
adapter surface is:

* ``submit`` / ``cancel`` / ``replace`` — order lifecycle
* ``positions`` / ``orders`` / ``account`` — read-only snapshots
* ``cancel_all`` / ``flatten`` — kill-switch primitives
* ``resume`` — token-confirmed human resume after a kill

Every network-bound call is wrapped by :func:`with_retry` (exponential
backoff + jitter + per-call timeout). Time and sleep are injectable so
tests prove attempt counts, caps, and timeouts without real sleeping.

Alpaca is activated **only** when ``broker.name`` demands it **and** the
full live gate in :func:`evaluate_live_gate` passes. Default config is
fail-closed (paper_only, paper metrics unmet). Gateway remains the sole
order-transmission path: adapters expose low-level ``submit`` only to
:meth:`RiskGateway.transmit`.
"""
from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, TypeVar

from risk.position_limits import OrderRequest
from utils.config import AppConfig, load_config
from utils.constants import LIVE_TRADING_AUTH_PHRASE
from utils.logger import get_logger

__all__ = [
    "BrokerError",
    "RetryableBrokerError",
    "TerminalBrokerError",
    "BrokerTimeoutError",
    "LiveGateDenied",
    "OrderResult",
    "PositionSnapshot",
    "AccountSnapshot",
    "LiveGateResult",
    "LiveGateEvidence",
    "BrokerAdapter",
    "with_retry",
    "evaluate_live_gate",
    "build_broker",
]

_log = get_logger("trading")

T = TypeVar("T")


# =============================================================================
# Error taxonomy — retryable vs terminal
# =============================================================================


class BrokerError(Exception):
    """Base class for every broker-adapter failure."""

    retryable: bool = False

    def __init__(self, message: str, *, cause: Optional[BaseException] = None) -> None:
        super().__init__(message)
        self.cause = cause


class RetryableBrokerError(BrokerError):
    """Transient failure (timeout, 429, 5xx, connection drop). Retryable."""

    retryable = True


class TerminalBrokerError(BrokerError):
    """Permanent failure (auth, reject, bad request, gate denial). Never retry."""

    retryable = False


class BrokerTimeoutError(RetryableBrokerError):
    """Per-call timeout exceeded."""


class LiveGateDenied(TerminalBrokerError):
    """Live/Alpaca activation blocked by the live-trading gate."""


# =============================================================================
# Typed results
# =============================================================================


class OrderStatus(str, Enum):
    """Normalized order status returned by adapters."""

    ACCEPTED = "accepted"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    REPLACED = "replaced"
    PENDING = "pending"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class OrderResult:
    """Normalized outcome of submit / cancel / replace."""

    order_id: str
    client_id: str
    symbol: str
    side: str
    quantity: float
    status: OrderStatus
    filled_quantity: float = 0.0
    filled_price: Optional[float] = None
    reject_reason: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PositionSnapshot:
    """One open position as reported by the broker."""

    symbol: str
    quantity: float
    side: str  # "long" | "short"
    avg_entry_price: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    raw: Mapping[str, Any] = field(default_factory=dict)

    def signed_quantity(self) -> float:
        """Signed quantity: long positive, short negative."""
        qty = abs(float(self.quantity))
        return qty if str(self.side).lower() in {"long", "buy"} else -qty


@dataclass(frozen=True)
class AccountSnapshot:
    """Broker account equity / cash / buying power."""

    equity: float
    cash: float
    buying_power: float = 0.0
    currency: str = "USD"
    status: str = "ACTIVE"
    raw: Mapping[str, Any] = field(default_factory=dict)


# =============================================================================
# Live gate — fail-closed by default
# =============================================================================


@dataclass(frozen=True)
class LiveGateEvidence:
    """Operator-supplied evidence that the paper track record qualifies."""

    paper_days: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 1.0  # fraction, e.g. 0.10 = 10%
    win_rate: float = 0.0
    breakers_tested: bool = False
    human_authorized: bool = False
    auth_phrase: str = ""


@dataclass(frozen=True)
class LiveGateResult:
    """Outcome of :func:`evaluate_live_gate`."""

    allowed: bool
    reasons: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()

    def raise_if_denied(self) -> None:
        if not self.allowed:
            raise LiveGateDenied("live gate denied: " + "; ".join(self.reasons))


def evaluate_live_gate(
    config: AppConfig,
    evidence: LiveGateEvidence,
    *,
    broker_name: Optional[str] = None,
) -> LiveGateResult:
    """Evaluate the full live-trading gate. Fail-closed by default.

    This is the **live-gate evaluation** function. Every criterion is
    independent and config-driven; the gate only opens when ALL pass:

    1. ``broker.name`` is a live-capable name (``alpaca`` / ``ibkr``)
    2. paper track record ``>= paper_trading.min_days_before_live`` (default 90)
    3. Sharpe ``>= paper_trading.required_sharpe`` (default 1.0)
    4. max drawdown ``<= paper_trading.required_max_drawdown`` (default 0.15)
    5. win rate ``>= paper_trading.required_win_rate`` (default 0.50)
    6. breakers have been exercised (``breakers_tested``)
    7. explicit human authorization (``human_authorized`` AND the
       config/auth phrase ``I-UNDERSTAND-LIVE-TRADING-RISK``)

    Default config (``broker.name=paper_only``) always fails closed.
    """
    pt = config.paper_trading
    name = (broker_name if broker_name is not None else config.broker.name).lower()
    reasons: list[str] = []
    checks = (
        "broker_name",
        "paper_days",
        "sharpe",
        "max_drawdown",
        "win_rate",
        "breakers_tested",
        "human_authorization",
    )

    if name not in {"alpaca", "ibkr"}:
        reasons.append(f"broker_name:{name}:not_live_capable")

    min_days = float(pt.min_days_before_live)
    if float(evidence.paper_days) < min_days:
        reasons.append(
            f"paper_days:{evidence.paper_days}<{min_days}"
        )

    req_sharpe = float(pt.required_sharpe)
    if float(evidence.sharpe) < req_sharpe:
        reasons.append(f"sharpe:{evidence.sharpe}<{req_sharpe}")

    req_dd = float(pt.required_max_drawdown)
    # max_drawdown is a positive fraction of peak; smaller is better.
    if float(evidence.max_drawdown) > req_dd:
        reasons.append(
            f"max_drawdown:{evidence.max_drawdown}>{req_dd}"
        )

    req_wr = float(pt.required_win_rate)
    if float(evidence.win_rate) < req_wr:
        reasons.append(f"win_rate:{evidence.win_rate}<{req_wr}")

    if not evidence.breakers_tested:
        reasons.append("breakers_tested:false")

    phrase_ok = (
        bool(evidence.human_authorized)
        and str(evidence.auth_phrase) == LIVE_TRADING_AUTH_PHRASE
    )
    if not phrase_ok:
        reasons.append("human_authorization:missing_or_invalid")

    return LiveGateResult(allowed=not reasons, reasons=tuple(reasons), checks=checks)


# =============================================================================
# Retry wrapper — exponential backoff + jitter + per-call timeout
# =============================================================================


def with_retry(
    func: Callable[[], T],
    *,
    config: Optional[AppConfig] = None,
    attempts: Optional[int] = None,
    base_delay: Optional[float] = None,
    backoff: float = 2.0,
    max_delay: Optional[float] = None,
    timeout: Optional[float] = None,
    sleeper: Optional[Callable[[float], None]] = None,
    rng: Optional[random.Random] = None,
    clock: Optional[Callable[[], float]] = None,
    retry_on: tuple[type[BaseException], ...] = (RetryableBrokerError,),
    give_up_on: tuple[type[BaseException], ...] = (TerminalBrokerError,),
    label: str = "broker_call",
) -> T:
    """Execute ``func`` with exponential backoff + jitter + per-call timeout.

    This is the **retry wrapper**. ALL timing knobs are config-driven
    (``broker.max_retries``, ``broker.retry_delay_seconds``,
    ``broker.request_timeout_seconds``) and overridable per call. The
    sleeper is injectable so tests prove attempt counts, the delay cap,
    and the timeout path without real sleeping.

    Args:
        func: zero-arg callable performing one broker operation.
        config: master config (loaded if omitted).
        attempts: total tries (default ``broker.max_retries``).
        base_delay: seconds before first retry (default ``broker.retry_delay_seconds``).
        backoff: multiplicative growth per retry (default 2.0).
        max_delay: ceiling on computed delay (default ``base_delay * 8``).
        timeout: per-attempt wall-clock seconds (default
            ``broker.request_timeout_seconds``). ``0``/``None`` disables.
        sleeper: injectable sleep (default ``time.sleep``).
        rng: injectable RNG for jitter (default ``random.Random()``).
        clock: injectable monotonic clock (default ``time.monotonic``).
        retry_on: exception classes that trigger another attempt.
        give_up_on: subclasses that abort immediately (terminal).
        label: log label for diagnostics.

    Returns:
        The value returned by ``func`` on the first successful attempt.

    Raises:
        The last retryable error after attempts are exhausted, or the first
        terminal / non-retryable error immediately. A per-call timeout is
        raised as :class:`BrokerTimeoutError` (retryable).
    """
    cfg = config or load_config()
    total_attempts = int(attempts if attempts is not None else cfg.broker.max_retries)
    if total_attempts < 1:
        raise ValueError("attempts must be >= 1")
    delay0 = float(base_delay if base_delay is not None else cfg.broker.retry_delay_seconds)
    cap = float(max_delay if max_delay is not None else max(delay0 * 8.0, delay0))
    per_call = float(timeout if timeout is not None else cfg.broker.request_timeout_seconds)
    sleep_fn = sleeper if sleeper is not None else time.sleep
    prng = rng if rng is not None else random.Random()
    now_fn = clock if clock is not None else time.monotonic

    delay = delay0
    last_exc: Optional[BaseException] = None

    for attempt in range(1, total_attempts + 1):
        try:
            if per_call and per_call > 0:
                return _call_with_timeout(func, per_call, clock=now_fn, label=label)
            return func()
        except give_up_on as exc:
            _log.error("{} aborted (terminal): {}", label, exc)
            raise
        except retry_on as exc:
            last_exc = exc
            if attempt >= total_attempts:
                break
            # Jitter in [-20%, +20%] of the current delay, then clamp to cap.
            jitter = delay * 0.2 * (2.0 * prng.random() - 1.0)
            sleep_for = max(0.0, min(delay + jitter, cap))
            _log.warning(
                "{} attempt {}/{} failed: {}; retrying in {:.3f}s",
                label, attempt, total_attempts, exc, sleep_for,
            )
            sleep_fn(sleep_for)
            delay = min(delay * backoff, cap)
        except Exception as exc:
            # Unknown exceptions are treated as terminal (never silently retry
            # a programming error as if it were a network blip).
            _log.error("{} unexpected non-retryable error: {}", label, exc)
            raise TerminalBrokerError(str(exc), cause=exc) from exc

    assert last_exc is not None
    _log.error("{} failed after {} attempt(s): {}", label, total_attempts, last_exc)
    raise last_exc


def _call_with_timeout(
    func: Callable[[], T],
    timeout: float,
    *,
    clock: Callable[[], float],
    label: str,
) -> T:
    """Run ``func`` with a hard wall-clock timeout.

    Uses a one-shot worker thread so a hung vendor SDK cannot block the
    trading loop forever. The ``clock`` argument is accepted for API
    symmetry with :func:`with_retry` (tests inject a fake sleeper/clock
    pair; the timeout itself is enforced by the executor).
    """
    del clock  # wall-clock enforcement is via the executor future
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(func)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeout as exc:
            future.cancel()
            raise BrokerTimeoutError(
                f"{label} exceeded per-call timeout of {timeout}s", cause=exc,
            ) from exc


# =============================================================================
# Adapter ABC
# =============================================================================


class BrokerAdapter(ABC):
    """Unified broker adapter contract (paper + live).

    Public strategy code never calls these methods to place orders
    directly for risk-gated paths — :meth:`RiskGateway.transmit` is the
    sole caller of :meth:`submit`. Cancel / flatten / resume are exposed
    for the kill-switch path, which is operator-driven.
    """

    name: str = "abstract"

    # ------------------------------------------------------------------
    # Order lifecycle
    # ------------------------------------------------------------------
    @abstractmethod
    def submit(self, request: OrderRequest) -> OrderResult:
        """Transmit one order. Reachable only via RiskGateway.transmit."""

    @abstractmethod
    def cancel(self, order_id: str) -> OrderResult:
        """Cancel one resting order by broker id (or client id)."""

    @abstractmethod
    def replace(
        self,
        order_id: str,
        *,
        quantity: Optional[float] = None,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
    ) -> OrderResult:
        """Replace quantity and/or prices on a resting order."""

    # ------------------------------------------------------------------
    # Read-only snapshots
    # ------------------------------------------------------------------
    @abstractmethod
    def positions(self) -> List[PositionSnapshot]:
        """Current open positions."""

    @abstractmethod
    def orders(self, *, status: str = "open") -> List[OrderResult]:
        """Orders filtered by status (``open`` / ``closed`` / ``all``)."""

    @abstractmethod
    def account(self) -> AccountSnapshot:
        """Account equity / cash / buying power."""

    # ------------------------------------------------------------------
    # Kill switch primitives
    # ------------------------------------------------------------------
    @abstractmethod
    def cancel_all(self) -> List[OrderResult]:
        """Cancel every resting order. Kill-switch step 1."""

    @abstractmethod
    def flatten(self) -> List[OrderResult]:
        """Market-close every open position. Kill-switch step 2."""

    def engage_kill_switch(self, reason: str) -> Dict[str, Any]:
        """Cancel-all + flatten. Returns a structured audit payload.

        This is the **kill-switch-through-adapter** path. Resume is a
        separate, token-confirmed human action (:meth:`resume`).
        """
        _log.error("KILL SWITCH via adapter {}: {}", self.name, reason)
        cancelled = self.cancel_all()
        flattened = self.flatten()
        return {
            "adapter": self.name,
            "reason": reason,
            "cancelled": [r.order_id for r in cancelled],
            "flattened": [r.order_id for r in flattened],
            "cancelled_count": len(cancelled),
            "flattened_count": len(flattened),
        }

    def resume(self, *, token: str, breaker: Any, reason: str = "operator resume") -> None:
        """Token-confirmed human resume after a kill / halt.

        Delegates to the circuit-breaker manager so the state machine and
        recovery ramp stay authoritative. A missing/invalid token raises
        :class:`~risk.circuit_breakers.ManualOverrideRequired`.
        """
        if not breaker.confirm_override(token):
            from risk.circuit_breakers import ManualOverrideRequired
            raise ManualOverrideRequired(
                "resume requires a confirmed override token from "
                "request_override + confirm_override"
            )
        breaker.resume(reason, token=token)


# =============================================================================
# Factory — fail-closed; Alpaca only behind name + live gate
# =============================================================================


def build_broker(
    config: Optional[AppConfig] = None,
    *,
    evidence: Optional[LiveGateEvidence] = None,
    paper: Any = None,
    alpaca_client: Any = None,
    gateway: Any = None,
    **paper_kwargs: Any,
) -> BrokerAdapter:
    """Construct the configured broker adapter.

    * ``broker.name == "paper_only"`` (default) → :class:`PaperBrokerAdapter`
    * ``broker.name == "alpaca"`` → requires a fully-passing live gate and
      either an injected ``alpaca_client`` (tests) or a live ``alpaca-py``
      client built from config credentials. Gate failure raises
      :class:`LiveGateDenied` (fail-closed).
    * Any other name → :class:`TerminalBrokerError`.
    """
    cfg = config or load_config()
    name = str(cfg.broker.name).lower()

    if name in {"paper_only", "paper"}:
        from trading.paper_adapter import PaperBrokerAdapter
        return PaperBrokerAdapter(
            paper=paper, config=cfg, gateway=gateway, **paper_kwargs,
        )

    if name == "alpaca":
        gate = evaluate_live_gate(cfg, evidence or LiveGateEvidence(), broker_name=name)
        gate.raise_if_denied()
        from trading.alpaca_adapter import AlpacaBrokerAdapter
        return AlpacaBrokerAdapter(
            config=cfg, client=alpaca_client, gateway=gateway,
        )

    raise TerminalBrokerError(
        f"unsupported broker.name={name!r}; use paper_only | alpaca"
    )
