"""Post-halt graduated size ramp.

The ramp throttles new-entry sizing back up after a trading halt, exactly per
the ``recovery.*`` config:

* day 1–3   -> ``day1_3_size_pct``   (default 0.25 / 25%)
* day 4–7   -> ``day4_7_size_pct``   (default 0.50 / 50%)
* week 2    -> ``week2_size_pct``    (default 0.75 / 75%)
* week 3+   -> ``week3_plus_size_pct``(default 1.00 / 100%)
* plus a mandatory ``cooling_off_days`` pause after a level-4 drawdown halt.

Integration contract
--------------------
* :meth:`RecoveryRamp.mark_halted` is called when the circuit breaker enters a
  HALTED/EMERGENCY/SUSPENDED state — it **freezes** the ramp timeline (the
  elapsed-days clock stops). :meth:`RecoveryRamp.resume` is called only after a
  human-approved resume on the breaker; it **restarts** the timeline at day 0.
* :meth:`RecoveryRamp.observe_breaker` reads the live :class:`CircuitBreakerManager`
  state and latches ``mark_halted`` automatically, so the ramp and the breaker
  cannot drift apart across a restart (the breaker state is itself persisted).
* :meth:`RecoveryRamp.size_order` is the **single** quantity-sizing authority
  for the automation path: it caps an intended quantity by the ramp multiplier,
  and the result is then placed through the **real** :class:`RiskGateway`. The
  Phase-9 integration test proves a 100-share intent fills only 25 shares on
  day 1 — the ramp flows through ``RiskGateway.transmit -> broker.submit`` into
  the real ledger, with NO parallel limit logic.

ALL time is read from an injected clock (``now_fn``); zero wall-clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from data.database import DatabaseManager
from utils.config import AppConfig, load_config
from utils.constants import (
    CircuitBreakerState,
    RecoveryPhase,
    STATE_SEVERITY,
)
from utils.helpers import to_iso_z, utc_now
from utils.logger import get_logger

__all__ = ["RecoveryRamp", "RecoveryError", "ramp_multiplier"]

_log = get_logger("automation")

SECONDS_PER_DAY = 86_400.0

#: Breaker states that constitute a "halt" for ramp purposes.
_HALTED_STATES = frozenset({
    CircuitBreakerState.HALTED,
    CircuitBreakerState.EMERGENCY,
    CircuitBreakerState.SUSPENDED,
})


class RecoveryError(Exception):
    """Raised on an illegal recovery-ramp operation (e.g. resume in cooling-off)."""


def ramp_multiplier(elapsed_days: float, *, config: AppConfig) -> float:
    """Pure graduated multiplier for ``elapsed_days`` since (re)start.

    This is the **ramp-calculation** function — the single expression of the
    ``recovery.*`` config ladder. It is pure w.r.t. its arguments so the full
    timeline (day1-3 / day4-7 / week2 / week3+) is deterministically testable.

    Tiers (boundaries are half-open [lo, hi) in days):

    * [0, 3)   -> day1_3_size_pct
    * [3, 7)   -> day4_7_size_pct
    * [7, 14)  -> week2_size_pct
    * [14, +)  -> week3_plus_size_pct
    """
    rec = config.recovery
    if elapsed_days < 3.0:
        return float(rec.day1_3_size_pct)
    if elapsed_days < 7.0:
        return float(rec.day4_7_size_pct)
    if elapsed_days < 14.0:
        return float(rec.week2_size_pct)
    return float(rec.week3_plus_size_pct)


@dataclass
class RecoveryState:
    """Serializable snapshot of the ramp (persisted across restarts)."""

    frozen: bool = True
    halted: bool = False
    recovery_start: Optional[str] = None
    cooling_off_until: Optional[str] = None
    recovery_anchor: Optional[float] = None


class RecoveryRamp:
    """Config-driven post-halt size ramp with circuit-breaker integration."""

    _KV_KEY = "recovery_ramp"

    def __init__(
        self,
        config: Optional[AppConfig] = None,
        *,
        now_fn: Optional[Callable[[], datetime]] = None,
        db: Optional[DatabaseManager] = None,
    ) -> None:
        self.config = config or load_config()
        self._now = now_fn or utc_now
        self.db = db
        self._frozen: bool = True
        self._halted: bool = False
        self._recovery_start: Optional[datetime] = None
        self._cooling_off_until: Optional[datetime] = None
        self._recovery_anchor: Optional[float] = None
        self._load()

    # ------------------------------------------------------------------
    # Breaker integration: HALTED freezes, human resume restarts
    # ------------------------------------------------------------------
    def mark_halted(self, *, cooling_off: bool = False, now: Optional[datetime] = None) -> None:
        """Latch a halt: freeze the ramp timeline (elapsed-days clock stops).

        When ``cooling_off`` is True (e.g. a level-4 drawdown flatten), a
        mandatory ``recovery.cooling_off_days`` window is recorded; no entries
        are admitted until it elapses AND a human resumes.
        """
        now = now or self._now()
        self._halted = True
        self._frozen = True
        if cooling_off:
            self._cooling_off_until = now + timedelta(days=int(self.config.recovery.cooling_off_days))
        self._persist(action="mark_halted",
                      extra={"cooling_off": cooling_off,
                             "cooling_off_until": to_iso_z(self._cooling_off_until)
                             if self._cooling_off_until else None})
        _log.info("recovery ramp frozen (halted); cooling_off={}", cooling_off)

    def resume(
        self,
        *,
        now: Optional[datetime] = None,
        equity: Optional[float] = None,
    ) -> None:
        """Human-approved resume: RESTART the ramp timeline at day 0.

        Raises :class:`RecoveryError` if a cooling-off window is still active
        (mirrors the breaker's ``locked_until`` gate so the two cannot disagree).
        """
        now = now or self._now()
        if self._cooling_off_until is not None and now < self._cooling_off_until:
            raise RecoveryError(
                f"cannot resume: cooling-off active until {to_iso_z(self._cooling_off_until)}")
        self._halted = False
        self._frozen = False
        self._recovery_start = now
        self._recovery_anchor = equity
        self._cooling_off_until = None
        self._persist(action="resume", extra={"equity": equity})
        _log.info("recovery ramp restarted at day 0 (equity anchor={})", equity)

    def observe_breaker(self, breaker: Any, *, now: Optional[datetime] = None) -> None:
        """Latch a halt automatically from the live breaker state.

        Called by the orchestrator each cycle. If the breaker is in any halt
        state (HALTED/EMERGENCY/SUSPENDED) the ramp freezes; this keeps the
        ramp consistent with the persisted breaker state across a restart
        without requiring the orchestrator to call :meth:`mark_halted` manually.
        """
        state = breaker.state if hasattr(breaker, "state") else breaker
        try:
            state = CircuitBreakerState(state)
        except Exception:
            return
        if state in _HALTED_STATES and not self._halted:
            self.mark_halted(now=now)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def frozen(self) -> bool:
        return self._frozen

    def in_cooling_off(self, now: Optional[datetime] = None) -> bool:
        now = now or self._now()
        return self._cooling_off_until is not None and now < self._cooling_off_until

    def elapsed_days(self, now: Optional[datetime] = None) -> Optional[float]:
        """Trading days elapsed since the last resume, or None if not resumed."""
        if self._recovery_start is None:
            return None
        now = now or self._now()
        return (now - self._recovery_start).total_seconds() / SECONDS_PER_DAY

    def phase(self, now: Optional[datetime] = None) -> RecoveryPhase:
        """Map the current elapsed-days to a :class:`RecoveryPhase`."""
        if self._frozen or self._recovery_start is None:
            return RecoveryPhase.NONE
        elapsed = self.elapsed_days(now) or 0.0
        if elapsed < 3.0:
            return RecoveryPhase.DAYS_1_3
        if elapsed < 7.0:
            return RecoveryPhase.DAYS_4_7
        if elapsed < 14.0:
            return RecoveryPhase.WEEK_2
        return RecoveryPhase.WEEK_3_PLUS

    def multiplier(self, now: Optional[datetime] = None,
                   equity: Optional[float] = None) -> float:
        """The effective size multiplier in [0, 1].

        * frozen / never resumed / cooling-off active -> ``0.0`` (no entries)
        * otherwise the graduated tier from :func:`ramp_multiplier`, with the
          ``require_positive_performance`` gate holding at the week-2 tier when
          equity has not recovered to its resume anchor.
        """
        now = now or self._now()
        if self._frozen or self._recovery_start is None:
            return 0.0
        if self.in_cooling_off(now):
            return 0.0
        elapsed = self.elapsed_days(now) or 0.0
        mult = ramp_multiplier(elapsed, config=self.config)
        rec = self.config.recovery
        if (rec.require_positive_performance
                and mult >= rec.week3_plus_size_pct
                and self._recovery_anchor is not None
                and equity is not None
                and equity < self._recovery_anchor):
            _log.info("recovery: holding at week-2 sizing until equity recovers")
            return float(rec.week2_size_pct)
        return float(mult)

    def size_order(self, intended_quantity: float, *, now: Optional[datetime] = None,
                   equity: Optional[float] = None) -> float:
        """Cap an intended order quantity by the ramp multiplier.

        The returned quantity is then placed through the **real**
        :class:`RiskGateway`; this is the single sizing authority for the
        automation path (no parallel limit logic). Returns 0 when the ramp
        blocks entries (frozen/cooling-off), so callers know to skip the order.
        """
        if intended_quantity <= 0:
            return 0.0
        mult = self.multiplier(now=now, equity=equity)
        return max(0.0, intended_quantity * mult)

    # ------------------------------------------------------------------
    # Persistence (survives restart)
    # ------------------------------------------------------------------
    def snapshot(self) -> RecoveryState:
        return RecoveryState(
            frozen=self._frozen,
            halted=self._halted,
            recovery_start=to_iso_z(self._recovery_start) if self._recovery_start else None,
            cooling_off_until=to_iso_z(self._cooling_off_until) if self._cooling_off_until else None,
            recovery_anchor=self._recovery_anchor,
        )

    def _load(self) -> None:
        if self.db is None:
            return
        try:
            from utils.helpers import parse_datetime
            raw = self.db.kv_get(self._KV_KEY, default=None)
            if not raw:
                return
            self._frozen = bool(raw.get("frozen", True))
            self._halted = bool(raw.get("halted", False))
            self._recovery_start = parse_datetime(raw["recovery_start"]) if raw.get("recovery_start") else None
            self._cooling_off_until = parse_datetime(raw["cooling_off_until"]) if raw.get("cooling_off_until") else None
            self._recovery_anchor = raw.get("recovery_anchor")
            _log.info("recovery ramp restored: frozen={} halted={}", self._frozen, self._halted)
        except Exception as exc:
            _log.warning("could not restore recovery ramp: {}", exc)

    def _persist(self, *, action: str, extra: Optional[dict[str, Any]] = None) -> None:
        if self.db is None:
            return
        snap = self.snapshot()
        try:
            self.db.kv_set(self._KV_KEY, snap.__dict__)
            details = dict(snap.__dict__)
            if extra:
                details.update(extra)
            self.db.log_automation("recovery", action, "ok", details=details)
        except Exception as exc:
            _log.warning("could not persist recovery ramp: {}", exc)
