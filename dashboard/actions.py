"""Phase 11 dashboard — PURE python mutation handlers.

The two dashboard mutation paths. Both are pure python (no Streamlit import)
so they are unit-tested in the clean CORE environment exactly like the rest
of the safety core; the Streamlit UI merely calls these functions.

The dashboard is **read-mostly**. The ONLY mutation paths are:

(a) **Kill switch** — wired through the Phase-10 broker adapter
    ``engage_kill_switch`` (cancel-all + flatten) + the circuit-breaker
    ``activate_kill_switch`` (EMERGENCY latch). It is gated by the Phase-10
    **double-confirmation token flow**: the operator must first mint an
    override token (:func:`request_kill_token`) and then re-enter it. A
    token-less / invalid / expired attempt is REJECTED by
    :func:`engage_kill_switch` and performs no action.

(b) **Approve / reject** on the Phase-9 approval queue
    (:func:`approve_signal` / :func:`reject_signal`) — human oversight of the
    queued signal lifecycle (BUILD_PLAN self-check item 7). Every decision is
    persisted to the ``system_state`` KV table + ``automation_log`` by the
    queue itself, so it survives restarts and is fully auditable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from automation.approval_queue import ApprovalQueue, QueuedSignal
from risk.circuit_breakers import CircuitBreakerManager
from trading.broker_base import BrokerAdapter
from utils.config import AppConfig
from utils.logger import get_logger

__all__ = [
    "KillSwitchResult",
    "request_kill_token",
    "engage_kill_switch",
    "approve_signal",
    "reject_signal",
    "build_control",
]

_log = get_logger("dashboard")

#: The override action label used by the kill-switch token flow.
KILL_SWITCH_ACTION = "kill_switch"


@dataclass
class KillSwitchResult:
    """Outcome of a kill-switch attempt (engaged OR rejected)."""

    ok: bool
    rejected: bool = False
    reason: str = ""
    operator: str = "operator"
    payload: dict[str, Any] = field(default_factory=dict)
    trigger: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def rejected(cls, reason: str, *, operator: str = "operator") -> "KillSwitchResult":
        return cls(ok=False, rejected=True, reason=reason, operator=operator)

    @classmethod
    def engaged(
        cls, payload: dict[str, Any], trigger: dict[str, Any], *,
        reason: str, operator: str = "operator",
    ) -> "KillSwitchResult":
        return cls(ok=True, rejected=False, reason=reason, operator=operator,
                   payload=payload, trigger=trigger)


# =============================================================================
# (a) Kill switch — Phase-10 token-confirmation flow
# =============================================================================

def request_kill_token(
    breaker: CircuitBreakerManager, *, reason: str = "operator kill switch",
) -> str:
    """Step 1 of the kill-switch confirmation flow.

    Mints a short-lived override token via the circuit-breaker's
    ``request_override``. The dashboard surfaces this token to the operator,
    who must re-enter it in :func:`engage_kill_switch` to actually fire.
    """
    token = breaker.request_override(KILL_SWITCH_ACTION, reason=reason)
    _log.warning("kill-switch override token minted (reason={!r})", reason)
    return token


def engage_kill_switch(
    broker: BrokerAdapter,
    breaker: CircuitBreakerManager,
    *,
    token: Optional[str],
    reason: str = "operator kill switch",
    operator: str = "operator",
) -> KillSwitchResult:
    """Step 2 (the kill-switch handler): cancel-all + flatten + latch EMERGENCY.

    This is gated by the Phase-10 double-confirmation token flow. The token
    must have been minted by :func:`request_kill_token` and still be valid
    (confirmed via ``breaker.confirm_override``). **A missing, invalid, or
    expired token REJECTS the attempt and performs no action** — a stray UI
    click cannot flatten the book.

    Only after the token is confirmed does it:

    1. call ``broker.engage_kill_switch`` (cancel-all + flatten through the
       adapter), and
    2. latch ``breaker.activate_kill_switch`` (EMERGENCY sticky state, so the
       halt survives restarts and requires a separate token-confirmed resume).

    Returns a :class:`KillSwitchResult` describing the outcome.
    """
    if not token:
        msg = "token required: request an override token before engaging the kill switch"
        _log.warning("kill switch REJECTED (no token) by {}", operator)
        return KillSwitchResult.rejected(msg, operator=operator)
    if not breaker.confirm_override(token):
        msg = "invalid or expired override token; kill switch rejected"
        _log.warning("kill switch REJECTED (bad token) by {}", operator)
        return KillSwitchResult.rejected(msg, operator=operator)

    # Token confirmed — fire the Phase-10 kill-switch-through-adapter path.
    payload = broker.engage_kill_switch(reason)
    trigger = breaker.activate_kill_switch(reason, flatten=True)
    _log.error("KILL SWITCH engaged by {} ({}): cancelled={} flattened={}",
               operator, reason, payload.get("cancelled_count"),
               payload.get("flattened_count"))
    return KillSwitchResult.engaged(
        payload=dict(payload),
        trigger=trigger.to_dict(),
        reason=reason, operator=operator,
    )


# =============================================================================
# (b) Approval queue — Phase-9 human oversight
# =============================================================================

def approve_signal(
    queue: ApprovalQueue, signal_id: str, *, operator: str = "operator",
) -> dict[str, Any]:
    """Approve a queued signal (Phase-9 lifecycle, persisted + audited)."""
    signal = queue.approve(signal_id, by=operator)
    _log.info("signal {} APPROVED by {}", signal_id, operator)
    return signal.to_dict()


def reject_signal(
    queue: ApprovalQueue, signal_id: str, *,
    operator: str = "operator", reason: str = "",
) -> dict[str, Any]:
    """Reject a queued signal (Phase-9 lifecycle, persisted + audited)."""
    signal = queue.reject(signal_id, by=operator, reason=reason)
    _log.info("signal {} REJECTED by {} ({})", signal_id, operator, reason)
    return signal.to_dict()


def pending_signals(queue: ApprovalQueue) -> list[dict[str, Any]]:
    """Read-only list of PENDING queued signals for the approval panel."""
    return [s.to_dict() for s in queue.pending()]


# =============================================================================
# Control-component factory
# =============================================================================

def build_control(config: AppConfig, db: Any):
    """Build the broker adapter + breaker manager + approval queue.

    Pure construction against the LOCAL database only (paper broker; no
    network, no live gate). The returned triple wires the two mutation paths
    to the persisted safety core so a dashboard restart restores breaker
    state and the queued signals.
    """
    from trading.paper_adapter import PaperBrokerAdapter
    from trading.paper_broker import PaperBroker

    breaker = CircuitBreakerManager(config, db)  # restores persisted state
    paper = PaperBroker(config=config, clock=lambda: 0.0,
                        fee_bps=0.0, slippage_bps=0.0)
    broker = PaperBrokerAdapter(paper=paper, config=config)
    queue = ApprovalQueue(config=config, db=db)
    return broker, breaker, queue
