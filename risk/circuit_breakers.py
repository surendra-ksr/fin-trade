"""Circuit breakers & speed breakers — the safety core of the fin-trade agent.

This module implements Part 9 of the spec end-to-end:

* **Layer 1** position-level breakers (hard/ATR/volatility/time stops + the
  max-loss-per-trade hard cap, exposed via :meth:`CircuitBreakerManager.evaluate_position`).
* **Layer 2** daily loss speed breakers (YELLOW/ORANGE/RED/EMERGENCY).
* **Layer 3** weekly and **Layer 4** monthly loss breakers.
* **Layer 5** portfolio drawdown breakers (from peak equity).
* **Layer 6** market-wide breakers (VIX ladder, market crash, flash crash,
  sector crash, liquidity).
* **Layer 7** technical-failure breakers (data feed staleness, API failure
  escalation, model-failure fallback, runaway-order prevention, position
  mismatch detector).
* A **state machine** (NORMAL .. SUSPENDED) with validated transitions,
  sticky vs auto-clearing triggers, time-locked halts, kill-switch, manual
  override with double-confirmation tokens, and a **graduated recovery**
  program per the spec.

Design notes
------------
* ``evaluate(snapshot)`` is pure w.r.t. its inputs: all mutable state lives
  in the manager and is persisted to the ``breaker_state`` table so halts
  survive restarts. Every escalation/de-escalation appends an immutable row
  to ``circuit_breaker_log`` and emits a structured log + optional notifier.
* Time is injectable (``now_fn``) which keeps the whole state machine
  deterministically testable.
* The manager never raises during ``evaluate`` — safety code must not itself
  become a failure mode. Programming errors surface as exceptions in tests
  (state machine violations), runtime anomalies become triggers.
"""

from __future__ import annotations

import json
import math
import secrets
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from data.database import DatabaseManager
from utils.config import AppConfig
from utils.constants import (
    AlertLevel,
    BreakerCategory,
    CircuitBreakerState,
    OrderSide,
    PositionSide,
    STATE_POLICY_DEFAULTS,
    STATE_SEVERITY,
    VALID_STATE_TRANSITIONS,
)
from utils.helpers import (
    MARKET_TZ,
    day_key,
    month_key,
    next_trading_day,
    parse_datetime,
    session_bounds,
    to_iso_z,
    utc_now,
    week_key,
)
from utils.logger import get_logger

__all__ = [
    "BreakerTrigger",
    "TradingPolicy",
    "PositionInfo",
    "PortfolioSnapshot",
    "OrderGateResult",
    "CircuitBreakerManager",
    "CircuitBreakerError",
    "InvalidStateTransition",
    "ManualOverrideRequired",
]

_log = get_logger("circuit_breakers")

SECONDS_PER_DAY = 86_400


class CircuitBreakerError(Exception):
    """Base error for circuit-breaker programming faults."""


class InvalidStateTransition(CircuitBreakerError):
    """Raised when an illegal state-machine transition is attempted."""


class ManualOverrideRequired(CircuitBreakerError):
    """Raised when an action needs the double-confirmation override flow."""


# =============================================================================
# Data objects
# =============================================================================


@dataclass
class BreakerTrigger:
    """One fired breaker.

    ``sticky`` triggers latch (loss-limit halts, kill switch, mismatches,
    anything requiring human acknowledgement) until cleared via
    :meth:`CircuitBreakerManager.resume` / override expiry. Non-sticky
    triggers re-evaluate every cycle and clear automatically (VIX ladder,
    crash detectors, feed staleness, flash-crash pauses).
    """

    category: BreakerCategory
    level: int
    severity: AlertLevel
    description: str
    timestamp: datetime
    value: Optional[float] = None
    threshold: Optional[float] = None
    sticky: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["category"] = self.category.value
        data["severity"] = int(self.severity)
        data["timestamp"] = to_iso_z(self.timestamp)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BreakerTrigger":
        return cls(
            category=BreakerCategory(str(data["category"])),
            level=int(data.get("level", 1)),
            severity=AlertLevel(int(data.get("severity", int(AlertLevel.INFO)))),
            description=str(data.get("description", "")),
            timestamp=parse_datetime(data["timestamp"]),
            value=data.get("value"),
            threshold=data.get("threshold"),
            sticky=bool(data.get("sticky", False)),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class PositionInfo:
    """Minimal per-position view needed by the breakers."""

    symbol: str
    side: PositionSide
    quantity: float
    avg_entry_price: float
    current_price: float
    sector: Optional[str] = None
    opened_at: Optional[datetime] = None

    @property
    def market_value(self) -> float:
        return abs(self.quantity) * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        direction = 1.0 if self.side is PositionSide.LONG else -1.0
        return direction * (self.current_price - self.avg_entry_price) * abs(self.quantity)

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.avg_entry_price <= 0:
            return 0.0
        direction = 1.0 if self.side is PositionSide.LONG else -1.0
        return direction * (self.current_price / self.avg_entry_price - 1.0)


@dataclass
class PortfolioSnapshot:
    """Everything the breakers need to evaluate one instant."""

    timestamp: datetime
    equity: float
    cash: float = 0.0
    positions: list[PositionInfo] = field(default_factory=list)
    vix: Optional[float] = None
    vix_day_open: Optional[float] = None
    benchmark_change_pct: Optional[float] = None      # daily move of SPY/QQQ
    sector_changes: dict[str, float] = field(default_factory=dict)
    # symbol -> {"spread_pct": float, "volume_ratio": float, "last_update": datetime}
    symbol_metrics: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class TradingPolicy:
    """Authoritative permission slip produced by ``evaluate``.

    The automation/orchestration code consults exactly one object — this —
    before proposing, sizing, or routing any order.
    """

    state: CircuitBreakerState
    position_size_multiplier: float = 1.0
    recovery_size_multiplier: float = 1.0
    confidence_boost: float = 0.0
    allow_new_entries: bool = True
    allow_new_longs: bool = True
    allow_new_shorts: bool = True
    allow_ml_signals: bool = True
    flatten_all: bool = False
    cancel_pending_orders: bool = False
    tighten_stops: bool = False
    stop_tighten_multiplier: float = 1.0
    max_open_positions: int = 10
    illiquid_symbols: list[str] = field(default_factory=list)
    blocked_symbols: list[str] = field(default_factory=list)
    blocked_sectors: dict[str, str] = field(default_factory=dict)  # sector -> until ISO
    required_actions: list[dict[str, Any]] = field(default_factory=list)
    active_triggers: list[BreakerTrigger] = field(default_factory=list)
    locked_until: Optional[datetime] = None
    reasons: list[str] = field(default_factory=list)

    @property
    def effective_size_multiplier(self) -> float:
        """Breaker multiplier combined with the recovery throttle."""
        return max(0.0, min(1.0, self.position_size_multiplier * self.recovery_size_multiplier))

    @property
    def trading_halted(self) -> bool:
        return not self.allow_new_entries or self.flatten_all

    def min_confidence(self, base_normal: float, base_restricted: float,
                       base_defensive: float) -> float:
        """Resolve the effective confidence gate for the current state."""
        sev = STATE_SEVERITY[self.state]
        if sev >= STATE_SEVERITY[CircuitBreakerState.DEFENSIVE]:
            base = base_defensive
        elif sev >= STATE_SEVERITY[CircuitBreakerState.RESTRICTED]:
            base = base_restricted
        elif sev >= STATE_SEVERITY[CircuitBreakerState.CAUTION]:
            base = max(base_normal, base_restricted - 0.05)
        else:
            base = base_normal
        return max(0.0, min(1.0, base + self.confidence_boost))

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "position_size_multiplier": self.position_size_multiplier,
            "recovery_size_multiplier": self.recovery_size_multiplier,
            "effective_size_multiplier": self.effective_size_multiplier,
            "confidence_boost": self.confidence_boost,
            "allow_new_entries": self.allow_new_entries,
            "allow_new_longs": self.allow_new_longs,
            "allow_new_shorts": self.allow_new_shorts,
            "allow_ml_signals": self.allow_ml_signals,
            "flatten_all": self.flatten_all,
            "cancel_pending_orders": self.cancel_pending_orders,
            "tighten_stops": self.tighten_stops,
            "stop_tighten_multiplier": self.stop_tighten_multiplier,
            "max_open_positions": self.max_open_positions,
            "illiquid_symbols": self.illiquid_symbols,
            "blocked_symbols": self.blocked_symbols,
            "blocked_sectors": self.blocked_sectors,
            "required_actions": self.required_actions,
            "active_triggers": [t.to_dict() for t in self.active_triggers],
            "locked_until": to_iso_z(self.locked_until) if self.locked_until else None,
            "reasons": self.reasons,
        }


@dataclass
class OrderGateResult:
    """Outcome of the runaway-order/flow gate."""

    allowed: bool
    reasons: list[str] = field(default_factory=list)
    retry_after_seconds: Optional[float] = None


# =============================================================================
# Manager
# =============================================================================

#: Map trigger severity -> the minimum state it forces.
_SEVERITY_STATE: dict[AlertLevel, CircuitBreakerState] = {
    AlertLevel.NONE: CircuitBreakerState.NORMAL,
    AlertLevel.INFO: CircuitBreakerState.NORMAL,
    AlertLevel.YELLOW: CircuitBreakerState.CAUTION,
    AlertLevel.ORANGE: CircuitBreakerState.RESTRICTED,
    AlertLevel.RED: CircuitBreakerState.HALTED,
    AlertLevel.EMERGENCY: CircuitBreakerState.EMERGENCY,
}

#: States that never de-escalate without an explicit, confirmed resume().
_LOCKED_STATES = {
    CircuitBreakerState.HALTED,
    CircuitBreakerState.EMERGENCY,
    CircuitBreakerState.SUSPENDED,
}

_OVERRIDE_TOKEN_TTL_SECONDS = 120


class CircuitBreakerManager:
    """Coordinates all breaker layers, the state machine, and recovery.

    Args:
        config: master :class:`AppConfig`.
        db: optional :class:`DatabaseManager` for persistence/audit. ``None``
            runs memory-only (unit tests, dry tooling).
        notifier: optional callable ``(message, AlertLevel, payload)`` used
            for alerts; its exceptions are logged and swallowed.
        now_fn: injectable clock returning aware UTC datetimes.
    """

    def __init__(
        self,
        config: AppConfig,
        db: Optional[DatabaseManager] = None,
        *,
        notifier: Optional[Callable[[str, AlertLevel, dict[str, Any]], None]] = None,
        now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._cfg = config
        self._cb = config.circuit_breakers
        self._db = db
        self._notifier = notifier
        self._now = now_fn or utc_now

        # --- state machine / persistence ---
        self._state: CircuitBreakerState = CircuitBreakerState.NORMAL
        self._sticky: list[BreakerTrigger] = []
        self._day_anchor: Optional[float] = None
        self._week_anchor: Optional[float] = None
        self._month_anchor: Optional[float] = None
        self._peak_equity: Optional[float] = None
        self._day_key: Optional[str] = None
        self._week_key: Optional[str] = None
        self._month_key_v: Optional[str] = None
        self._locked_until: Optional[datetime] = None
        self._manual: set[BreakerCategory] = set()   # sticky reasons pinned by a human
        self._recovery_start: Optional[datetime] = None
        self._recovery_anchor: Optional[float] = None
        self._killed: bool = False

        # --- technical-failure tracking ---
        self._feed_heartbeats: dict[str, datetime] = {}
        self._feed_down_since: Optional[datetime] = None
        self._api_failures: int = 0
        self._api_first_failure: Optional[datetime] = None
        self._model_confidences: dict[str, float] = {}
        self._model_conf_at: Optional[datetime] = None
        self._position_mismatches: list[str] = []

        # --- flow control (runaway prevention) ---
        self._order_submissions: deque[datetime] = deque(maxlen=10_000)
        self._recent_orders: deque[dict[str, Any]] = deque(maxlen=1_000)
        # attempts key -> (count, window_start); window resets after 30 min quiet
        self._order_attempts: dict[str, tuple[int, datetime]] = {}
        self._flow_pause_until: Optional[datetime] = None
        self._recent_events: dict[str, float] = {}  # description -> monotonic ts (throttle)

        # --- crash detectors ---
        self._index_prices: deque[tuple[datetime, float]] = deque(maxlen=10_000)
        self._flash_pause_until: Optional[datetime] = None
        self._flash_drop_origin: Optional[float] = None
        self._flash_drop_low: Optional[float] = None
        self._sector_blocks: dict[str, datetime] = {}
        self._last_vix: Optional[float] = None

        self._pending_override: dict[str, dict[str, Any]] = {}
        self._load_persisted()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load_persisted(self) -> None:
        if self._db is None:
            return
        try:
            row = self._db.load_breaker_state()
            if not row:
                return
            self._state = CircuitBreakerState(str(row.get("state", "NORMAL")))
            self._sticky = [BreakerTrigger.from_dict(t) for t in row.get("active_breakers", [])]
            self._day_anchor = row.get("day_anchor")
            self._week_anchor = row.get("week_anchor")
            self._month_anchor = row.get("month_anchor")
            self._peak_equity = row.get("peak_equity")
            self._day_key = row.get("day_key")
            self._week_key = row.get("week_key")
            self._month_key_v = row.get("month_key")
            self._locked_until = (parse_datetime(row["locked_until"])
                                  if row.get("locked_until") else None)
            self._recovery_start = (parse_datetime(row["recovery_start"])
                                    if row.get("recovery_start") else None)
            self._recovery_anchor = row.get("recovery_anchor")
            self._killed = any(t.category is BreakerCategory.KILL_SWITCH for t in self._sticky)
            sector_blocks = self._db.kv_get("breaker:sector_blocks", default={})
            for sector, until in (sector_blocks or {}).items():
                try:
                    self._sector_blocks[str(sector)] = parse_datetime(until)
                except (ValueError, TypeError, KeyError):
                    continue
            _log.info("circuit breaker state restored: {} ({} sticky trigger(s))",
                      self._state.value, len(self._sticky))
        except Exception as exc:  # persistence problems must NEVER break safety
            _log.error("failed to restore breaker state ({}); continuing in {}", exc,
                       self._state.value)

    def _persist(self) -> None:
        if self._db is None:
            return
        try:
            self._db.save_breaker_state({
                "state": self._state.value,
                "active_breakers": [t.to_dict() for t in self._sticky],
                "day_anchor": self._day_anchor,
                "week_anchor": self._week_anchor,
                "month_anchor": self._month_anchor,
                "peak_equity": self._peak_equity,
                "day_key": self._day_key,
                "week_key": self._week_key,
                "month_key": self._month_key_v,
                "locked_until": to_iso_z(self._locked_until) if self._locked_until else None,
                "recovery_start": to_iso_z(self._recovery_start) if self._recovery_start else None,
                "recovery_anchor": self._recovery_anchor,
                "notes": "kill_switch" if self._killed else None,
            })
            self._db.kv_set("breaker:sector_blocks",
                            {s: to_iso_z(u) for s, u in self._sector_blocks.items()})
        except Exception as exc:
            _log.error("failed to persist breaker state: {}", exc)

    # ------------------------------------------------------------------
    # Event logging / notifications
    # ------------------------------------------------------------------
    def _record_event(self, trigger: Optional[BreakerTrigger], action: str,
                      state_before: CircuitBreakerState, details: Optional[dict[str, Any]] = None) -> None:
        category = trigger.category.value if trigger else "system"
        severity = trigger.severity if trigger else AlertLevel.INFO
        payload = {
            "trigger": trigger.to_dict() if trigger else None,
            "details": details or {},
        }
        if self._db is not None:
            try:
                self._db.log_circuit_breaker_event(
                    category, action,
                    level=severity,
                    state_before=state_before.value,
                    state_after=self._state.value,
                    trigger_type=trigger.description if trigger else None,
                    details=payload,
                )
            except Exception as exc:
                _log.error("failed to persist breaker event: {}", exc)
        log = _log.bind(breaker=category)
        if int(severity) >= int(AlertLevel.RED):
            log.error("[{} -> {}] {} :: {}", state_before.value, self._state.value, action,
                      trigger.description if trigger else "")
        elif int(severity) >= int(AlertLevel.YELLOW):
            log.warning("[{} -> {}] {} :: {}", state_before.value, self._state.value, action,
                        trigger.description if trigger else "")
        else:
            log.info("[{} -> {}] {}", state_before.value, self._state.value, action)
        if self._notifier is not None and int(severity) >= int(AlertLevel.ORANGE):
            try:
                self._notifier(action, severity, payload)
            except Exception as exc:
                _log.error("notifier failed: {}", exc)

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------
    @property
    def state(self) -> CircuitBreakerState:
        return self._state

    def _transition(self, target: CircuitBreakerState, reason: str,
                    trigger: Optional[BreakerTrigger]) -> None:
        if target is self._state:
            return
        allowed = VALID_STATE_TRANSITIONS[self._state]
        if target not in allowed:
            raise InvalidStateTransition(
                f"cannot transition {self._state.value} -> {target.value} ({reason})")
        before = self._state
        self._state = target
        _log.info("state transition: {} -> {} ({})", before.value, target.value, reason)
        self._record_event(trigger, f"state:{before.value}->{target.value}: {reason}", before)

    def _worst_forced_state(self, triggers: Sequence[BreakerTrigger]) -> CircuitBreakerState:
        worst = CircuitBreakerState.NORMAL
        for trigger in triggers:
            forced = _SEVERITY_STATE.get(trigger.severity, CircuitBreakerState.NORMAL)
            if STATE_SEVERITY[forced] > STATE_SEVERITY[worst]:
                worst = forced
        return worst

    # ------------------------------------------------------------------
    # Anchors
    # ------------------------------------------------------------------
    def _roll_anchors(self, snapshot: PortfolioSnapshot) -> None:
        now = snapshot.timestamp
        today, this_week, this_month = day_key(now), week_key(now), month_key(now)
        if self._day_key is None:
            self._day_key, self._week_key, self._month_key_v = today, this_week, this_month
            self._day_anchor = self._week_anchor = self._month_anchor = snapshot.equity
            self._peak_equity = snapshot.equity
        else:
            if today != self._day_key:
                _log.info("new trading day: rolling day anchor to equity {:.2f}", snapshot.equity)
                self._day_key, self._day_anchor = today, snapshot.equity
            if this_week != self._week_key:
                _log.info("new trading week: rolling week anchor to equity {:.2f}", snapshot.equity)
                self._week_key, self._week_anchor = this_week, snapshot.equity
            if this_month != self._month_key_v:
                _log.info("new trading month: rolling month anchor to equity {:.2f}", snapshot.equity)
                self._month_key_v, self._month_anchor = this_month, snapshot.equity
        if self._peak_equity is None or snapshot.equity > self._peak_equity:
            self._peak_equity = snapshot.equity

    # ------------------------------------------------------------------
    # Layer 2/3/4: loss breakers
    # ------------------------------------------------------------------
    def _check_losses(self, snapshot: PortfolioSnapshot, now: datetime) -> list[BreakerTrigger]:
        triggers: list[BreakerTrigger] = []

        def pct(anchor: Optional[float]) -> Optional[float]:
            if anchor is None or anchor <= 0:
                return None
            return snapshot.equity / anchor - 1.0

        daily = pct(self._day_anchor)
        if daily is not None:
            dl = self._cb.daily_loss
            if daily <= dl.level4_pct:
                triggers.append(BreakerTrigger(
                    BreakerCategory.DAILY_LOSS, 4, AlertLevel.EMERGENCY,
                    f"daily loss {daily:.2%} breached level4 {dl.level4_pct:.2%}",
                    now, value=daily, threshold=dl.level4_pct, sticky=True,
                    metadata={"action": "flatten_all", "lock_sessions": 2}))
            elif daily <= dl.level3_pct:
                triggers.append(BreakerTrigger(
                    BreakerCategory.DAILY_LOSS, 3, AlertLevel.RED,
                    f"daily loss {daily:.2%} breached level3 {dl.level3_pct:.2%}",
                    now, value=daily, threshold=dl.level3_pct, sticky=True,
                    metadata={"action": "close_worst_half", "lock_sessions": 1}))
            elif daily <= dl.level2_pct:
                triggers.append(BreakerTrigger(
                    BreakerCategory.DAILY_LOSS, 2, AlertLevel.ORANGE,
                    f"daily loss {daily:.2%} breached level2 {dl.level2_pct:.2%}",
                    now, value=daily, threshold=dl.level2_pct,
                    metadata={"action": "block_entries_reduce", "size_multiplier": 0.75,
                              "tighten_stops": True}))
            elif daily <= dl.level1_pct:
                triggers.append(BreakerTrigger(
                    BreakerCategory.DAILY_LOSS, 1, AlertLevel.YELLOW,
                    f"daily loss {daily:.2%} breached level1 {dl.level1_pct:.2%}",
                    now, value=daily, threshold=dl.level1_pct))

        weekly = pct(self._week_anchor)
        if weekly is not None:
            wl = self._cb.weekly_loss
            if weekly <= wl.level3_pct:
                triggers.append(BreakerTrigger(
                    BreakerCategory.WEEKLY_LOSS, 3, AlertLevel.RED,
                    f"weekly loss {weekly:.2%} breached level3 {wl.level3_pct:.2%}",
                    now, value=weekly, threshold=wl.level3_pct, sticky=True,
                    metadata={"action": "flatten_all", "until": "week_end"}))
            elif weekly <= wl.level2_pct:
                triggers.append(BreakerTrigger(
                    BreakerCategory.WEEKLY_LOSS, 2, AlertLevel.ORANGE,
                    f"weekly loss {weekly:.2%} breached level2 {wl.level2_pct:.2%}",
                    now, value=weekly, threshold=wl.level2_pct,
                    metadata={"size_multiplier": 0.50, "min_confidence": 0.80,
                              "block_shorts": True}))
            elif weekly <= wl.level1_pct:
                triggers.append(BreakerTrigger(
                    BreakerCategory.WEEKLY_LOSS, 1, AlertLevel.YELLOW,
                    f"weekly loss {weekly:.2%} breached level1 {wl.level1_pct:.2%}",
                    now, value=weekly, threshold=wl.level1_pct,
                    metadata={"size_multiplier": 0.75, "confidence_boost": 0.10}))

        monthly = pct(self._month_anchor)
        if monthly is not None:
            ml = self._cb.monthly_loss
            if monthly <= ml.level3_pct:
                triggers.append(BreakerTrigger(
                    BreakerCategory.MONTHLY_LOSS, 3, AlertLevel.RED,
                    f"monthly loss {monthly:.2%} breached level3 {ml.level3_pct:.2%}",
                    now, value=monthly, threshold=ml.level3_pct, sticky=True,
                    metadata={"action": "flatten_all", "until": "month_end",
                              "manual_review": True}))
            elif monthly <= ml.level2_pct:
                triggers.append(BreakerTrigger(
                    BreakerCategory.MONTHLY_LOSS, 2, AlertLevel.ORANGE,
                    f"monthly loss {monthly:.2%} breached level2 {ml.level2_pct:.2%}",
                    now, value=monthly, threshold=ml.level2_pct,
                    metadata={"size_multiplier": 0.50, "highest_conviction_only": True,
                              "pause_retraining": True}))
            elif monthly <= ml.level1_pct:
                triggers.append(BreakerTrigger(
                    BreakerCategory.MONTHLY_LOSS, 1, AlertLevel.YELLOW,
                    f"monthly loss {monthly:.2%} breached level1 {ml.level1_pct:.2%}",
                    now, value=monthly, threshold=ml.level1_pct,
                    metadata={"size_multiplier": 0.75}))
        return triggers

    # ------------------------------------------------------------------
    # Layer 5: drawdown breakers
    # ------------------------------------------------------------------
    def _check_drawdown(self, snapshot: PortfolioSnapshot, now: datetime) -> list[BreakerTrigger]:
        peak = self._peak_equity
        if peak is None or peak <= 0:
            return []
        dd = snapshot.equity / peak - 1.0
        cfg = self._cb.drawdown
        triggers: list[BreakerTrigger] = []
        if dd <= cfg.level4_pct:
            triggers.append(BreakerTrigger(
                BreakerCategory.DRAWDOWN, 4, AlertLevel.EMERGENCY,
                f"drawdown {dd:.2%} breached level4 {cfg.level4_pct:.2%}",
                now, value=dd, threshold=cfg.level4_pct, sticky=True,
                metadata={"action": "flatten_all", "cooling_off_days":
                          self._cfg.recovery.cooling_off_days, "forced_review": True}))
        elif dd <= cfg.level3_pct:
            triggers.append(BreakerTrigger(
                BreakerCategory.DRAWDOWN, 3, AlertLevel.RED,
                f"drawdown {dd:.2%} breached level3 {cfg.level3_pct:.2%}",
                now, value=dd, threshold=cfg.level3_pct,
                metadata={"size_multiplier": 0.50, "max_open_positions": 3,
                          "min_confidence": 0.85}))
        elif dd <= cfg.level2_pct:
            triggers.append(BreakerTrigger(
                BreakerCategory.DRAWDOWN, 2, AlertLevel.ORANGE,
                f"drawdown {dd:.2%} breached level2 {cfg.level2_pct:.2%}",
                now, value=dd, threshold=cfg.level2_pct,
                metadata={"size_multiplier": 0.60, "min_confidence": 0.75,
                          "block_shorts": True}))
        elif dd <= cfg.level1_pct:
            triggers.append(BreakerTrigger(
                BreakerCategory.DRAWDOWN, 1, AlertLevel.YELLOW,
                f"drawdown {dd:.2%} breached level1 {cfg.level1_pct:.2%}",
                now, value=dd, threshold=cfg.level1_pct,
                metadata={"size_multiplier": 0.80, "tighten_stops": True}))
        return triggers

    # ------------------------------------------------------------------
    # Layer 6: market-wide breakers
    # ------------------------------------------------------------------
    def _check_market(self, snapshot: PortfolioSnapshot, now: datetime) -> list[BreakerTrigger]:
        triggers: list[BreakerTrigger] = []
        vix = snapshot.vix if snapshot.vix is not None else self._last_vix
        if vix is not None:
            self._last_vix = vix
            vx = self._cb.vix
            if vix >= vx.exit_all:
                triggers.append(BreakerTrigger(
                    BreakerCategory.VIX, 4, AlertLevel.EMERGENCY,
                    f"VIX {vix:.1f} >= exit_all {vx.exit_all}",
                    now, value=vix, threshold=float(vx.exit_all),
                    metadata={"action": "flatten_all", "size_multiplier": 0.0}))
            elif vix >= vx.reduce_75:
                triggers.append(BreakerTrigger(
                    BreakerCategory.VIX, 3, AlertLevel.RED,
                    f"VIX {vix:.1f} >= reduce_75 {vx.reduce_75}",
                    now, value=vix, threshold=float(vx.reduce_75),
                    metadata={"size_multiplier": 0.25}))
            elif vix >= vx.reduce_50:
                triggers.append(BreakerTrigger(
                    BreakerCategory.VIX, 2, AlertLevel.ORANGE,
                    f"VIX {vix:.1f} >= reduce_50 {vx.reduce_50}",
                    now, value=vix, threshold=float(vx.reduce_50),
                    metadata={"size_multiplier": 0.50, "block_longs": True}))
            elif vix >= vx.reduce_25:
                triggers.append(BreakerTrigger(
                    BreakerCategory.VIX, 1, AlertLevel.YELLOW,
                    f"VIX {vix:.1f} >= reduce_25 {vx.reduce_25}",
                    now, value=vix, threshold=float(vx.reduce_25),
                    metadata={"size_multiplier": 0.75}))
            if (snapshot.vix_day_open and snapshot.vix_day_open > 0
                    and vix / snapshot.vix_day_open - 1.0 >= vx.intraday_spike_pct):
                triggers.append(BreakerTrigger(
                    BreakerCategory.VIX, 2, AlertLevel.ORANGE,
                    f"VIX intraday spike {vix / snapshot.vix_day_open - 1.0:.1%} >= "
                    f"{vx.intraday_spike_pct:.0%}",
                    now, value=vix / snapshot.vix_day_open - 1.0,
                    threshold=vx.intraday_spike_pct,
                    metadata={"action": "immediate_review"}))
        change = snapshot.benchmark_change_pct
        if change is not None:
            mc = self._cb.market_crash
            if change <= mc.red_pct:
                triggers.append(BreakerTrigger(
                    BreakerCategory.MARKET_CRASH, 3, AlertLevel.RED,
                    f"benchmark move {change:.2%} <= red {mc.red_pct:.2%}",
                    now, value=change, threshold=mc.red_pct,
                    metadata={"action": "exit_longs"}))
            elif change <= mc.orange_pct:
                triggers.append(BreakerTrigger(
                    BreakerCategory.MARKET_CRASH, 2, AlertLevel.ORANGE,
                    f"benchmark move {change:.2%} <= orange {mc.orange_pct:.2%}",
                    now, value=change, threshold=mc.orange_pct,
                    metadata={"size_multiplier": 0.50, "block_longs": True}))
            elif change <= mc.yellow_pct:
                triggers.append(BreakerTrigger(
                    BreakerCategory.MARKET_CRASH, 1, AlertLevel.YELLOW,
                    f"benchmark move {change:.2%} <= yellow {mc.yellow_pct:.2%}",
                    now, value=change, threshold=mc.yellow_pct))
        return triggers

    def _check_sectors(self, snapshot: PortfolioSnapshot, now: datetime) -> list[BreakerTrigger]:
        triggers: list[BreakerTrigger] = []
        crashed: list[tuple[str, float]] = []
        threshold = self._cb.market_crash.sector_crash_pct
        for sector, change in snapshot.sector_changes.items():
            if change is None:
                continue
            if change <= threshold:
                crashed.append((sector, change))
        for sector, change in crashed:
            until = now + timedelta(days=self._cb.market_crash.sector_block_days)
            self._sector_blocks[sector] = until
            triggers.append(BreakerTrigger(
                BreakerCategory.SECTOR_CRASH, 1, AlertLevel.ORANGE,
                f"sector {sector} crashed {change:.2%} (<= {threshold:.2%}); blocked until "
                f"{until.date()}", now, value=change, threshold=threshold,
                metadata={"exit_sector": sector, "block_until": to_iso_z(until)}))
        return triggers

    def _check_flash_crash(self, now: datetime) -> list[BreakerTrigger]:
        fc = self._cb.flash_crash
        triggers: list[BreakerTrigger] = []
        window = timedelta(minutes=fc.timeframe_minutes)
        recent = [(ts, px) for ts, px in self._index_prices if now - ts <= window]
        if len(recent) >= 2:
            origin = max(px for _, px in recent)
            low = min(px for _, px in recent)
            if origin > 0:
                drop = low / origin - 1.0
                if drop <= fc.threshold_pct:
                    self._flash_drop_origin = origin
                    self._flash_drop_low = min(low, self._flash_drop_low or low)
                    self._flash_pause_until = now + timedelta(minutes=fc.pause_minutes)
                    triggers.append(BreakerTrigger(
                        BreakerCategory.FLASH_CRASH, 1, AlertLevel.RED,
                        f"flash crash: {drop:.2%} in {fc.timeframe_minutes}m; pausing orders "
                        f"{fc.pause_minutes}m", now, value=drop, threshold=fc.threshold_pct,
                        metadata={"pause_until": to_iso_z(self._flash_pause_until)}))
        active_pause = (self._flash_pause_until is not None and now < self._flash_pause_until)
        if active_pause and self._flash_drop_origin and self._flash_drop_low:
            latest = self._index_prices[-1][1] if self._index_prices else None
            drop_range = self._flash_drop_origin - self._flash_drop_low
            if latest is not None and drop_range > 0:
                recovered = (latest - self._flash_drop_low) / drop_range
                if recovered >= fc.resume_recovery_pct:
                    _log.info("flash crash recovery {:.0%} >= {:.0%}; lifting pause",
                              recovered, fc.resume_recovery_pct)
                    self._flash_pause_until = None
                    self._flash_drop_origin = None
                    self._flash_drop_low = None
                    active_pause = False
                else:
                    triggers.append(BreakerTrigger(
                        BreakerCategory.FLASH_CRASH, 1, AlertLevel.ORANGE,
                        f"flash crash pause active; recovery at {recovered:.0%} (needs "
                        f"{fc.resume_recovery_pct:.0%})", now, value=recovered,
                        threshold=fc.resume_recovery_pct,
                        metadata={"pause_until": to_iso_z(self._flash_pause_until)}))
        return triggers

    def _check_liquidity(self, snapshot: PortfolioSnapshot, now: datetime) -> list[BreakerTrigger]:
        liq = self._cb.liquidity
        triggers: list[BreakerTrigger] = []
        for symbol, metrics in snapshot.symbol_metrics.items():
            spread = metrics.get("spread_pct")
            vol_ratio = metrics.get("volume_ratio")
            if spread is not None and spread > liq.max_spread_pct:
                triggers.append(BreakerTrigger(
                    BreakerCategory.LIQUIDITY, 1, AlertLevel.ORANGE,
                    f"{symbol} spread {spread:.2%} > {liq.max_spread_pct:.2%} (illiquid)",
                    now, value=spread, threshold=liq.max_spread_pct,
                    metadata={"symbol": symbol, "limit_orders_only": True}))
            if vol_ratio is not None and vol_ratio < liq.min_volume_ratio:
                triggers.append(BreakerTrigger(
                    BreakerCategory.LIQUIDITY, 1, AlertLevel.YELLOW,
                    f"{symbol} volume {vol_ratio:.0%} of normal < {liq.min_volume_ratio:.0%}",
                    now, value=vol_ratio, threshold=liq.min_volume_ratio,
                    metadata={"symbol": symbol, "block_entries": True}))
        return triggers

    # ------------------------------------------------------------------
    # Layer 7: technical failure breakers
    # ------------------------------------------------------------------
    def _check_technical(self, now: datetime) -> list[BreakerTrigger]:
        tech = self._cb.technical
        triggers: list[BreakerTrigger] = []
        if self._feed_heartbeats:
            oldest = min(self._feed_heartbeats.values())
            silence = (now - oldest).total_seconds()
            if silence > tech.data_feed_timeout_seconds:
                if self._feed_down_since is None:
                    self._feed_down_since = now
                down_for = (now - self._feed_down_since).total_seconds()
                if silence > tech.data_feed_emergency_seconds or down_for > tech.data_feed_emergency_seconds:
                    triggers.append(BreakerTrigger(
                        BreakerCategory.DATA_FEED, 2, AlertLevel.EMERGENCY,
                        f"data feeds silent {silence:.0f}s > emergency "
                        f"{tech.data_feed_emergency_seconds}s; exiting at market",
                        now, value=float(silence),
                        threshold=float(tech.data_feed_emergency_seconds),
                        metadata={"action": "flatten_all"}))
                else:
                    triggers.append(BreakerTrigger(
                        BreakerCategory.DATA_FEED, 1, AlertLevel.RED,
                        f"data feeds silent {silence:.0f}s > timeout "
                        f"{tech.data_feed_timeout_seconds}s; halting new orders",
                        now, value=float(silence),
                        threshold=float(tech.data_feed_timeout_seconds),
                        metadata={"action": "block_entries"}))
        if self._api_failures >= tech.api_retry_attempts:
            since = self._api_first_failure or now
            duration = (now - since).total_seconds()
            if duration > tech.api_failure_escalation_seconds:
                triggers.append(BreakerTrigger(
                    BreakerCategory.API_FAILURE, 2, AlertLevel.EMERGENCY,
                    f"broker API failing for {duration:.0f}s after {self._api_failures} errors; "
                    "emergency exit attempt", now, value=float(self._api_failures),
                    threshold=float(tech.api_retry_attempts),
                    metadata={"action": "flatten_all"}))
            else:
                triggers.append(BreakerTrigger(
                    BreakerCategory.API_FAILURE, 1, AlertLevel.ORANGE,
                    f"broker API consecutive failures: {self._api_failures} >= "
                    f"{tech.api_retry_attempts}", now, value=float(self._api_failures),
                    threshold=float(tech.api_retry_attempts)))
        if self._model_confidences:
            best = max(self._model_confidences.values())
            if best < tech.model_min_confidence:
                triggers.append(BreakerTrigger(
                    BreakerCategory.MODEL_FAILURE, 1, AlertLevel.ORANGE,
                    f"all model confidences < {tech.model_min_confidence:.2f} (best "
                    f"{best:.2f}); falling back to technical-only signals",
                    now, value=best, threshold=tech.model_min_confidence,
                    metadata={"action": "technical_only"}))
        if self._position_mismatches:
            triggers.append(BreakerTrigger(
                BreakerCategory.POSITION_MISMATCH, 1, AlertLevel.RED,
                f"position mismatch detected ({len(self._position_mismatches)} symbol(s)); "
                "halting for reconciliation", now, sticky=True,
                metadata={"mismatches": list(self._position_mismatches)}))
        if self._flow_pause_until is not None and now < self._flow_pause_until:
            triggers.append(BreakerTrigger(
                BreakerCategory.RUNAWAY_ORDER, 1, AlertLevel.RED,
                f"order flow paused until {to_iso_z(self._flow_pause_until)} (rate limit trip)",
                now, metadata={"pause_until": to_iso_z(self._flow_pause_until)}))
        return triggers

    # ------------------------------------------------------------------
    # Feed- API the rest of the system calls
    # ------------------------------------------------------------------
    def record_data_heartbeat(self, source: str = "market_data", ts: Optional[datetime] = None) -> None:
        """Data feed liveness ping; a fresh heartbeat auto-clears feed breakers."""
        self._feed_heartbeats[source] = ts or self._now()
        if self._feed_down_since is not None and all(
            (self._now() - stamp).total_seconds() <= self._cb.technical.data_feed_timeout_seconds
            for stamp in self._feed_heartbeats.values()
        ):
            _log.info("data feeds healthy again after {0:.0f}s outage",
                      (self._now() - self._feed_down_since).total_seconds())
            self._feed_down_since = None

    def record_api_failure(self, error: str = "") -> None:
        """Register a broker-API failure (escalates with duration)."""
        self._api_failures += 1
        if self._api_first_failure is None:
            self._api_first_failure = self._now()
        _log.warning("broker API failure #{}: {}", self._api_failures, error or "unknown")

    def record_api_success(self) -> None:
        """Reset the API failure counters after a healthy broker call."""
        if self._api_failures:
            _log.info("broker API recovered after {} failure(s)", self._api_failures)
        self._api_failures = 0
        self._api_first_failure = None

    def record_model_confidences(self, confidences: Mapping[str, float],
                                 ts: Optional[datetime] = None) -> None:
        """Latest per-model confidence scores for the model-failure watchdog."""
        self._model_confidences = {str(k): float(v) for k, v in confidences.items()}
        self._model_conf_at = ts or self._now()

    def record_index_price(self, price: float, ts: Optional[datetime] = None,
                           symbol: Optional[str] = None) -> None:
        """Feed the flash-crash detector with benchmark ticks (default SPY)."""
        if price <= 0:
            return
        self._index_prices.append((ts or self._now(), float(price)))

    def report_position_mismatch(self, mismatches: Iterable[str]) -> None:
        """Declare expected-vs-broker position divergence (sticky halt)."""
        items = [str(m) for m in mismatches]
        self._position_mismatches = sorted(set(items))
        if items:
            _log.error("position mismatch reported: {}", items)

    def clear_position_mismatch(self) -> None:
        """Mark reconciliation complete; the sticky trigger clears next cycle."""
        self._position_mismatches = []
        self._drop_sticky(BreakerCategory.POSITION_MISMATCH)

    def record_vix(self, vix: float) -> None:
        """Update VIX out-of-band (used when the snapshot has no VIX)."""
        if vix > 0:
            self._last_vix = float(vix)

    # ------------------------------------------------------------------
    # Runaway-order flow gate
    # ------------------------------------------------------------------
    def can_submit_order(
        self,
        symbol: str,
        side: OrderSide | str,
        quantity: float,
        price: Optional[float] = None,
        *,
        now: Optional[datetime] = None,
    ) -> OrderGateResult:
        """Flow-control gate: rate caps, duplicate detection, attempt ceiling.

        This complements (never replaces) the :meth:`evaluate` policy; the
        orchestrator calls both.
        """
        now = now or self._now()
        reasons: list[str] = []
        tech = self._cb.technical
        side = OrderSide(side) if not isinstance(side, OrderSide) else side

        if self.state in _LOCKED_STATES:
            reasons.append(f"circuit breaker state {self.state.value} blocks new orders")
        if self._flow_pause_until is not None and now < self._flow_pause_until:
            wait = (self._flow_pause_until - now).total_seconds()
            reasons.append(f"order flow paused for {wait:.0f}s (rate limit trip)")
        if self._flash_pause_until is not None and now < self._flash_pause_until:
            wait = (self._flash_pause_until - now).total_seconds()
            reasons.append(f"flash-crash pause active for another {wait:.0f}s")

        minute_ago = now - timedelta(seconds=60)
        recent_minute = sum(1 for ts in self._order_submissions if ts >= minute_ago)
        if recent_minute >= tech.max_orders_per_minute:
            self._flow_pause_until = now + timedelta(seconds=60)
            reasons.append(f"order rate {recent_minute}/min >= cap {tech.max_orders_per_minute}; "
                           "60s flow pause engaged")

        window_start = now - timedelta(seconds=tech.duplicate_order_window_seconds)
        fingerprint = (symbol.upper(), side.value, round(float(quantity), 6))
        for entry in reversed(self._recent_orders):
            if entry["ts"] < window_start:
                break
            if entry["fingerprint"] == fingerprint:
                reasons.append(f"duplicate order detected within "
                               f"{tech.duplicate_order_window_seconds}s")
                break

        attempt_key = f"{symbol.upper()}|{side.value}"
        count, window_start_ts = self._order_attempts.get(attempt_key, (0, now))
        if (now - window_start_ts).total_seconds() > 1800:
            count = 0
        if count >= tech.max_order_attempts:
            reasons.append(f"order attempt ceiling reached for {attempt_key} "
                           f"({count}/{tech.max_order_attempts} within 30min)")
        return OrderGateResult(allowed=not reasons, reasons=reasons,
                               retry_after_seconds=60.0 if reasons else None)

    def register_order_submission(self, symbol: str, side: OrderSide | str,
                                  quantity: float, *, now: Optional[datetime] = None) -> int:
        """Record an order submission for flow accounting; returns the attempt #."""
        now = now or self._now()
        side = OrderSide(side) if not isinstance(side, OrderSide) else side
        self._order_submissions.append(now)
        fingerprint = (symbol.upper(), side.value, round(float(quantity), 6))
        self._recent_orders.append({"ts": now, "fingerprint": fingerprint})
        key = f"{symbol.upper()}|{side.value}"
        count, window_start = self._order_attempts.get(key, (0, now))
        if (now - window_start).total_seconds() > 1800:
            count, window_start = 0, now
        self._order_attempts[key] = (count + 1, window_start)
        return count + 1

    def reset_order_attempts(self, symbol: Optional[str] = None) -> None:
        """Clear attempt counters (e.g. after a fill or manual operator reset)."""
        if symbol is None:
            self._order_attempts.clear()
        else:
            prefix = symbol.upper() + "|"
            self._order_attempts = {k: v for k, v in self._order_attempts.items()
                                    if not k.startswith(prefix)}

    # ------------------------------------------------------------------
    # Kill switch / suspend / resume / override
    # ------------------------------------------------------------------
    def activate_kill_switch(self, reason: str, *, flatten: bool = True) -> BreakerTrigger:
        """Global kill switch: halt everything immediately (sticky, manual reset).

        Returns the latch trigger so callers can attach it to their own logs.
        """
        now = self._now()
        self._killed = True
        trigger = BreakerTrigger(
            BreakerCategory.KILL_SWITCH, 1, AlertLevel.EMERGENCY,
            f"KILL SWITCH engaged: {reason}", now, sticky=True,
            metadata={"flatten": flatten})
        self._add_sticky(trigger)
        self._transition(CircuitBreakerState.EMERGENCY, f"kill switch: {reason}", trigger)
        self._persist()
        return trigger

    def suspend(self, reason: str) -> None:
        """Operator takes the system fully offline (SUSPENDED state)."""
        trigger = BreakerTrigger(BreakerCategory.MANUAL, 1, AlertLevel.RED,
                                 f"system suspended by operator: {reason}", self._now(),
                                 sticky=True)
        before = self._state
        self._add_sticky(trigger)
        # SUSPENDED is reachable from every state.
        self._state = CircuitBreakerState.SUSPENDED
        self._record_event(trigger, f"manual suspend: {reason}", before)
        self._persist()

    def resume(self, reason: str, *, token: Optional[str] = None,
               equity: Optional[float] = None) -> None:
        """Resume after halt/emergency/suspension; starts graduated recovery.

        Raises:
            ManualOverrideRequired: locked states need a confirm token from
                :meth:`request_override` + :meth:`confirm_override`.
            CircuitBreakerError: when a cooling-off/lock window is active.
        """
        if self._state in _LOCKED_STATES:
            if token is None or token not in self._pending_override:
                raise ManualOverrideRequired(
                    "resume from HALTED/EMERGENCY/SUSPENDED requires a confirmed override token")
            self._pending_override.pop(token, None)
        if self._locked_until is not None and self._now() < self._locked_until:
            raise CircuitBreakerError(
                f"trading locked until {to_iso_z(self._locked_until)}; cannot resume yet")
        before = self._state
        cleared = [t.description for t in self._sticky if t.category not in self._manual]
        self._sticky = [t for t in self._sticky if t.category in self._manual]
        self._killed = any(t.category is BreakerCategory.KILL_SWITCH for t in self._sticky)
        self._locked_until = None
        self._recovery_start = self._now()
        self._recovery_anchor = equity if equity is not None else self._day_anchor
        target = CircuitBreakerState.RESTRICTED
        # walk down one legal step at a time
        while self._state is not target:
            candidates = sorted(
                (s for s in VALID_STATE_TRANSITIONS[self._state]),
                key=lambda s: STATE_SEVERITY[s],
            )
            self._transition(candidates[0], f"resume: {reason}", None)
        if not self._sticky and self._state in (CircuitBreakerState.RESTRICTED,):
            pass
        self._record_event(None, f"resumed: {reason}; sticky cleared: {cleared}", before)
        self._persist()

    # ------------------------------------------------------------------
    # Double-confirmation override
    # ------------------------------------------------------------------
    def request_override(self, action: str, *, reason: str) -> str:
        """Step 1 of a manual override: mint an expiring confirmation token."""
        token = secrets.token_hex(8)
        self._pending_override[token] = {
            "action": action, "reason": reason,
            "created_at": time.monotonic(), "expires_in": _OVERRIDE_TOKEN_TTL_SECONDS,
        }
        _log.warning("manual override requested: {} ({}); token expires in {}s",
                     action, reason, _OVERRIDE_TOKEN_TTL_SECONDS)
        return token

    def confirm_override(self, token: str) -> bool:
        """Step 2: validate the token. True == confirmed and still valid."""
        entry = self._pending_override.get(token)
        if entry is None:
            return False
        if time.monotonic() - entry["created_at"] > _OVERRIDE_TOKEN_TTL_SECONDS:
            self._pending_override.pop(token, None)
            _log.warning("override token expired")
            return False
        # token remains valid for the immediately following privileged call.
        _log.warning("override confirmed: {}", entry["action"])
        return True

    # ------------------------------------------------------------------
    # Sticky trigger management
    # ------------------------------------------------------------------
    def _add_sticky(self, trigger: BreakerTrigger) -> None:
        """Latch a sticky trigger (dedupes by category+level+description)."""
        key = (trigger.category, trigger.level, trigger.description)
        for existing in self._sticky:
            if (existing.category, existing.level, existing.description) == key:
                return
        self._sticky.append(trigger)

    def _drop_sticky(self, category: BreakerCategory, level: Optional[int] = None) -> None:
        self._sticky = [t for t in self._sticky
                        if not (t.category is category and (level is None or t.level == level))]
        if category is BreakerCategory.KILL_SWITCH:
            self._killed = any(t.category is category for t in self._sticky)

    # ------------------------------------------------------------------
    # Lock computation
    # ------------------------------------------------------------------
    def _session_open_after(self, sessions: int, from_dt: datetime) -> datetime:
        """UTC instant of the session open *sessions* trading days after today.

        'Today' is the exchange-timezone date of *from_dt*. The lock always
        lasts at least the remainder of today, so the answer is the open of
        the *sessions*-th trading day strictly after today (level3: 1, i.e.
        next session open; level4: 2, i.e. locked today + next day).
        """
        if sessions < 1:
            raise CircuitBreakerError("sessions must be >= 1")
        open_t = self._cfg.automation.market_open
        et_day = from_dt.astimezone(MARKET_TZ).date()
        day = next_trading_day(et_day)  # first trading day strictly after today
        for _ in range(sessions - 1):
            day = next_trading_day(day)
        open_utc, _ = session_bounds(day, open_t)
        return open_utc

    # ------------------------------------------------------------------
    # THE evaluation pipeline
    # ------------------------------------------------------------------
    def evaluate(self, snapshot: PortfolioSnapshot) -> TradingPolicy:
        """Evaluate every breaker layer against the current snapshot.

        Returns the merged :class:`TradingPolicy`. Side effects: anchor rolls,
        sticky-trigger latching, lock timers, state transitions, audit logs,
        persistence, and notifications.
        """
        try:
            return self._evaluate_inner(snapshot)
        except InvalidStateTransition:
            raise
        except Exception as exc:  # safety code must not crash the loop
            _log.error("evaluate() caught unexpected error: {} — returning DEFENSIVE policy", exc)
            policy = TradingPolicy(state=CircuitBreakerState.DEFENSIVE,
                                   allow_new_entries=False, allow_new_longs=False,
                                   allow_new_shorts=False,
                                   reasons=[f"evaluation error: {exc}"])
            return policy

    def _evaluate_inner(self, snapshot: PortfolioSnapshot) -> TradingPolicy:
        now = snapshot.timestamp
        if not self._cb.enabled:
            self._roll_anchors(snapshot)
            self._persist()
            defaults = STATE_POLICY_DEFAULTS[CircuitBreakerState.NORMAL]
            policy = TradingPolicy(state=self._state,
                                   position_size_multiplier=float(defaults["position_size_multiplier"]),
                                   max_open_positions=int(defaults["max_open_positions"]),
                                   reasons=["circuit breakers disabled by configuration"])
            policy.recovery_size_multiplier = self._recovery_multiplier(now, snapshot.equity)
            return policy

        self._roll_anchors(snapshot)

        fresh = self._check_losses(snapshot, now)
        fresh += self._check_drawdown(snapshot, now)
        fresh += self._check_market(snapshot, now)
        fresh += self._check_sectors(snapshot, now)
        fresh += self._check_flash_crash(now)
        fresh += self._check_liquidity(snapshot, now)
        fresh += self._check_technical(now)

        # Latch new sticky triggers; (re)arm lock timers.
        for trigger in fresh:
            if trigger.sticky:
                was_new = not any(
                    (t.category, t.level, t.description) == (trigger.category, trigger.level,
                                                             trigger.description)
                    for t in self._sticky)
                self._add_sticky(trigger)
                if was_new:
                    self._apply_lock(trigger, now)
                    self._record_event(trigger, f"latched: {trigger.description}", self._state)

        # Merge fresh + latched triggers, deduped — a sticky trigger latched
        # this cycle would otherwise appear twice and duplicate its actions.
        _seen: set[tuple[Any, int, str]] = set()
        all_triggers: list[BreakerTrigger] = []
        for _t in fresh + list(self._sticky):
            _key = (_t.category, _t.level, _t.description)
            if _key not in _seen:
                _seen.add(_key)
                all_triggers.append(_t)

        # ------------------------------------------------------------------
        # Resolve target state
        # ------------------------------------------------------------------
        forced = self._worst_forced_state(all_triggers)
        lock_active = self._locked_until is not None and now < self._locked_until
        if lock_active and STATE_SEVERITY[forced] < STATE_SEVERITY[CircuitBreakerState.HALTED]:
            forced = CircuitBreakerState.HALTED
        if self._killed:
            forced = CircuitBreakerState.EMERGENCY

        if STATE_SEVERITY[forced] > STATE_SEVERITY[self._state]:
            worst_trigger = max(all_triggers, key=lambda t: int(t.severity), default=None)
            self._transition(
                forced,
                "escalation: " + (worst_trigger.description if worst_trigger else "policy"),
                worst_trigger,
            )
        elif STATE_SEVERITY[forced] < STATE_SEVERITY[self._state]:
            # Auto de-escalation: free states always recover one step when
            # conditions clear. HALTED additionally recovers automatically
            # when the halt was purely condition-driven (no sticky latches,
            # no active lock, no kill switch) — e.g. VIX / flash-crash / feed
            # events that self-resolve. EMERGENCY and SUSPENDED always need a
            # human resume().
            condition_driven_halt = (
                self._state is CircuitBreakerState.HALTED
                and not self._sticky and not self._killed and not lock_active
                and not any(int(t.severity) >= int(AlertLevel.RED) for t in all_triggers)
            )
            can_auto = (self._state not in _LOCKED_STATES) or condition_driven_halt
            if can_auto:
                candidates = sorted(
                    (s for s in VALID_STATE_TRANSITIONS[self._state]
                     if STATE_SEVERITY[s] < STATE_SEVERITY[self._state]),
                    key=lambda s: -STATE_SEVERITY[s],
                )
                if candidates:
                    target = candidates[0]
                    if STATE_SEVERITY[target] >= STATE_SEVERITY[forced]:
                        self._transition(target, "conditions cleared (auto de-escalation)", None)
        elif self._state in _LOCKED_STATES and not self._sticky and not self._killed \
                and not lock_active and self._state is not CircuitBreakerState.SUSPENDED:
            # defensive: locked-state without cause — require resume() anyway.
            pass

        # ------------------------------------------------------------------
        # Aggregate the policy (most conservative wins)
        # ------------------------------------------------------------------
        defaults = STATE_POLICY_DEFAULTS[self._state]
        multiplier = float(defaults["position_size_multiplier"])
        confidence_boost = float(defaults["confidence_boost"])
        max_positions = int(defaults["max_open_positions"])
        allow_longs = bool(defaults["allow_new_longs"])
        allow_shorts = bool(defaults["allow_new_shorts"])
        allow_entries = bool(defaults["allow_new_entries"])
        allow_ml = True
        flatten_all = False
        cancel_pending = False
        tighten_stops = False
        tighten_mult = 1.0
        reasons: list[str] = []
        actions: list[dict[str, Any]] = []
        illiquid: set[str] = set()
        blocked: set[str] = set()
        min_confidence_directives: list[float] = []

        for trigger in all_triggers:
            md = trigger.metadata or {}
            reasons.append(trigger.description)
            if "size_multiplier" in md:
                multiplier = min(multiplier, float(md["size_multiplier"]))
            if "confidence_boost" in md:
                confidence_boost = max(confidence_boost, float(md["confidence_boost"]))
            if "min_confidence" in md:
                min_confidence_directives.append(float(md["min_confidence"]))
            if "max_open_positions" in md:
                max_positions = min(max_positions, int(md["max_open_positions"]))
            if md.get("block_longs"):
                allow_longs = False
            if md.get("block_shorts"):
                allow_shorts = False
            if md.get("action") in {"block_entries", "block_entries_reduce"}:
                allow_entries = False
            if md.get("action") == "block_entries_reduce":
                # ORANGE daily speed breaker also cancels all pending limit
                # orders, per the Layer-2 spec.
                cancel_pending = True
            if md.get("action") == "flatten_all":
                flatten_all = True
                allow_entries = False
                allow_longs = allow_shorts = False
                cancel_pending = True
            if md.get("action") == "close_worst_half":
                allow_entries = False
                cancel_pending = True
                tighten_stops = True
                tighten_mult = min(tighten_mult, 0.5)
                losers = sorted(snapshot.positions, key=lambda p: p.unrealized_pnl)
                # "Close 50% of all open positions (highest loss first)"
                n_close = math.ceil(len(losers) / 2) if losers else 0
                for pos in losers[:n_close]:
                    actions.append({"type": "close_position", "symbol": pos.symbol,
                                    "fraction": 1.0, "reason": trigger.description})
                if losers:
                    actions.append({"type": "tighten_stops_to_breakeven",
                                    "reason": "daily loss level3"})
            if md.get("action") == "exit_longs":
                for pos in snapshot.positions:
                    if pos.side is PositionSide.LONG:
                        actions.append({"type": "close_position", "symbol": pos.symbol,
                                        "fraction": 1.0, "reason": trigger.description})
                allow_longs = False
            if md.get("action") == "technical_only":
                allow_ml = False
            if md.get("tighten_stops") or md.get("action") == "block_entries_reduce":
                tighten_stops = True
                tighten_mult = min(tighten_mult, 0.5)
            if md.get("exit_sector"):
                for pos in snapshot.positions:
                    if (pos.sector or "") == md["exit_sector"]:
                        actions.append({"type": "close_position", "symbol": pos.symbol,
                                        "fraction": 1.0, "sector": md["exit_sector"],
                                        "reason": trigger.description})
                blocked.add(f"sector:{md['exit_sector']}")
            if md.get("symbol") and md.get("limit_orders_only"):
                illiquid.add(str(md["symbol"]))
            if md.get("symbol") and md.get("block_entries"):
                blocked.add(str(md["symbol"]))
            if md.get("pause_retraining"):
                actions.append({"type": "pause_model_retraining", "reason": trigger.description})
            if md.get("forced_review"):
                actions.append({"type": "forced_backtest_review", "window_days": 90,
                                "reason": trigger.description})

        if min_confidence_directives:
            # expressed as confidence_boost relative to the normal threshold
            best_directive = max(min_confidence_directives)
            confidence_boost = max(confidence_boost,
                                   best_directive - self._cfg.trading.min_confidence_normal)
        if self._killed:
            flatten_all = True
            cancel_pending = True
            allow_entries = allow_longs = allow_shorts = False
        if lock_active:
            allow_entries = False
            reasons.append(f"trading locked until {to_iso_z(self._locked_until)}")
        if self._flash_pause_until is not None and now < self._flash_pause_until:
            allow_entries = False
        if self._flow_pause_until is not None and now < self._flow_pause_until:
            allow_entries = False

        # Sector blocks still in force (persisted across days)
        sector_blocks_active = {
            sector: to_iso_z(until) for sector, until in self._sector_blocks.items() if until > now}
        self._sector_blocks = {s: parse_datetime(u) for s, u in sector_blocks_active.items()}

        if flatten_all and snapshot.positions and not any(
                a.get("type") == "close_position" for a in actions):
            for pos in snapshot.positions:
                actions.append({"type": "close_position", "symbol": pos.symbol,
                                "fraction": 1.0, "reason": "flatten_all"})

        policy = TradingPolicy(
            state=self._state,
            position_size_multiplier=multiplier,
            recovery_size_multiplier=self._recovery_multiplier(now, snapshot.equity),
            confidence_boost=max(0.0, min(1.0, confidence_boost)),
            allow_new_entries=allow_entries,
            allow_new_longs=allow_longs and allow_entries,
            allow_new_shorts=allow_shorts and allow_entries,
            allow_ml_signals=allow_ml,
            flatten_all=flatten_all,
            cancel_pending_orders=cancel_pending,
            tighten_stops=tighten_stops,
            stop_tighten_multiplier=tighten_mult,
            max_open_positions=max_positions,
            illiquid_symbols=sorted(illiquid),
            blocked_symbols=sorted(sym for sym in blocked if not sym.startswith("sector:")),
            blocked_sectors=sector_blocks_active,
            required_actions=actions,
            active_triggers=list(all_triggers),
            locked_until=self._locked_until,
            reasons=[r for r in reasons if r],
        )

        # Log active non-sticky triggers, throttled to one entry per 15
        # minutes per description so the audit table stays readable.
        wall = time.monotonic()
        for trigger in fresh:
            if not trigger.sticky and int(trigger.severity) >= int(AlertLevel.ORANGE):
                last = self._recent_events.get(trigger.description, 0.0)
                if wall - last > 900:
                    self._recent_events[trigger.description] = wall
                    self._record_event(trigger, f"active: {trigger.description}", self._state)

        self._persist()
        return policy

    def _apply_lock(self, trigger: BreakerTrigger, now: datetime) -> None:
        """Set locked_until (and recovery gates) when a sticky trigger lands."""
        md = trigger.metadata or {}
        sessions = int(md.get("lock_sessions", 0) or 0)
        if sessions > 0:
            self._locked_until = self._session_open_after(sessions, now)
        if trigger.category is BreakerCategory.DRAWDOWN and trigger.level >= 4:
            cooling = int(md.get("cooling_off_days", self._cfg.recovery.cooling_off_days))
            self._locked_until = now + timedelta(days=cooling)
        if self._locked_until is not None:
            _log.warning("trading locked until {} ({})", to_iso_z(self._locked_until),
                         trigger.description)

    def _recovery_multiplier(self, now: datetime, equity: Optional[float]) -> float:
        """Graduated recovery throttle per the spec's recovery program."""
        if self._recovery_start is None:
            return 1.0
        rec = self._cfg.recovery
        elapsed_days = (now - self._recovery_start).total_seconds() / SECONDS_PER_DAY
        if elapsed_days < 3:
            mult = rec.day1_3_size_pct
        elif elapsed_days < 7:
            mult = rec.day4_7_size_pct
        elif elapsed_days < 14:
            mult = rec.week2_size_pct
        else:
            mult = rec.week3_plus_size_pct
        if (rec.require_positive_performance and mult >= rec.week3_plus_size_pct
                and self._recovery_anchor is not None and equity is not None
                and equity < self._recovery_anchor):
            _log.info("recovery: holding at {:.0%} sizing until performance recovers",
                      rec.week2_size_pct)
            return rec.week2_size_pct
        if mult >= 1.0:
            _log.info("recovery complete ({:.1f} days); returning to full sizing", elapsed_days)
            self._recovery_start = None
            self._recovery_anchor = None
            return 1.0
        return mult

    # ------------------------------------------------------------------
    # Layer 1: position-level breakers
    # ------------------------------------------------------------------
    def evaluate_position(
        self,
        position: PositionInfo,
        *,
        atr: Optional[float] = None,
        portfolio_equity: Optional[float] = None,
        days_open: Optional[int] = None,
        current_volatility: Optional[float] = None,
        baseline_volatility: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        """Layer-1 checks for a single open position.

        Returns a list of protective actions; the execution layer turns them
        into orders. Checks: hard stop, ATR stop, max-loss-per-trade cap,
        volatility spike halving, and time stop.
        """
        risk = self._cfg.risk
        actions: list[dict[str, Any]] = []
        side_long = position.side is PositionSide.LONG
        entry = position.avg_entry_price
        now_price = position.current_price
        if entry <= 0 or now_price <= 0:
            return actions

        # 1) hard stop (default 2%)
        stop_pct = risk.default_stop_loss_pct
        breached = (now_price <= entry * (1 - stop_pct)) if side_long else \
            (now_price >= entry * (1 + stop_pct))
        if breached:
            actions.append({"type": "close_position", "symbol": position.symbol, "fraction": 1.0,
                            "reason": f"hard stop {stop_pct:.1%} breached",
                            "stop_kind": "hard"})
            return actions  # full exit dominates the remaining checks

        # 2) ATR stop (2x ATR from entry)
        if atr is not None and atr > 0:
            mult = risk.trailing_stop_atr_multiple
            stop_price = entry - mult * atr if side_long else entry + mult * atr
            atr_breached = now_price <= stop_price if side_long else now_price >= stop_price
            if atr_breached:
                actions.append({"type": "close_position", "symbol": position.symbol,
                                "fraction": 1.0,
                                "reason": f"ATR stop breached ({mult}x ATR={atr:.4f})",
                                "stop_kind": "atr"})
                return actions

        # 3) max loss per trade cap (percentage of portfolio equity)
        if portfolio_equity and portfolio_equity > 0:
            loss_frac = -position.unrealized_pnl / portfolio_equity
            if loss_frac >= risk.max_loss_per_trade_pct:
                actions.append({"type": "close_position", "symbol": position.symbol,
                                "fraction": 1.0,
                                "reason": f"max loss per trade hit "
                                          f"({loss_frac:.2%} >= {risk.max_loss_per_trade_pct:.2%})",
                                "stop_kind": "max_loss"})

        # 4) volatility spike: >3x normal -> halve
        if (current_volatility and baseline_volatility and baseline_volatility > 0
                and current_volatility > 3 * baseline_volatility):
            actions.append({"type": "reduce_position", "symbol": position.symbol,
                            "fraction": 0.5,
                            "reason": f"volatility spike {current_volatility:.3f} > 3x "
                                      f"baseline {baseline_volatility:.3f}",
                            "stop_kind": "volatility"})

        # 5) time stop: no progress after N days
        if days_open is not None and days_open >= risk.time_stop_days:
            actions.append({"type": "close_position", "symbol": position.symbol,
                            "fraction": 1.0,
                            "reason": f"time stop: {days_open}d without progress",
                            "stop_kind": "time"})
        return actions

    # ------------------------------------------------------------------
    # Reporting / dashboard support
    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        """Snapshot of the breaker system for the dashboard."""
        now = self._now()
        return {
            "state": self._state.value,
            "severity": STATE_SEVERITY[self._state],
            "kill_switch_engaged": self._killed,
            "sticky_triggers": [t.to_dict() for t in self._sticky],
            "anchors": {
                "day": self._day_anchor,
                "week": self._week_anchor,
                "month": self._month_anchor,
                "peak_equity": self._peak_equity,
                "day_key": self._day_key,
                "week_key": self._week_key,
                "month_key": self._month_key_v,
            },
            "locked_until": to_iso_z(self._locked_until) if self._locked_until else None,
            "lock_remaining_seconds": (
                (self._locked_until - now).total_seconds()
                if self._locked_until and self._locked_until > now else 0.0),
            "recovery": {
                "active": self._recovery_start is not None,
                "started_at": to_iso_z(self._recovery_start) if self._recovery_start else None,
                "anchor_equity": self._recovery_anchor,
            },
            "sector_blocks": {s: to_iso_z(u) for s, u in self._sector_blocks.items()},
            "flash_pause_until": to_iso_z(self._flash_pause_until)
            if self._flash_pause_until and self._flash_pause_until > now else None,
            "flow_pause_until": to_iso_z(self._flow_pause_until)
            if self._flow_pause_until and self._flow_pause_until > now else None,
            "api_failures": self._api_failures,
            "feeds": {src: to_iso_z(ts) for src, ts in self._feed_heartbeats.items()},
            "position_mismatches": list(self._position_mismatches),
        }

    def history(self, limit: int = 100):
        """Breaker event history (newest first) from the audit table."""
        if self._db is None:
            return []
        return self._db.fetch_breaker_events(limit=limit)

    def generate_post_halt_report(self, snapshot: Optional[PortfolioSnapshot] = None) -> dict[str, Any]:
        """Post-halt analysis: root cause + stats, logged to the automation log."""
        now = self._now()
        events = (self._db.fetch_breaker_events(limit=50) if self._db is not None
                  else self._sticky and None)
        root = self._sticky[0].description if self._sticky else "no sticky triggers recorded"
        report = {
            "generated_at": to_iso_z(now),
            "state": self._state.value,
            "root_cause_guess": root,
            "active_sticky_triggers": [t.to_dict() for t in self._sticky],
            "locked_until": to_iso_z(self._locked_until) if self._locked_until else None,
            "recent_events_count": int(len(events)) if events is not None else 0,
            "anchors": self.status()["anchors"],
            "recommendations": self._recommendations(),
        }
        if snapshot is not None:
            report["last_equity"] = snapshot.equity
            report["open_positions"] = [p.symbol for p in snapshot.positions]
        if self._db is not None:
            try:
                self._db.log_automation("risk", "post_halt_report", "ok", details=report)
            except Exception as exc:
                _log.error("failed to persist post-halt report: {}", exc)
        return report

    def _recommendations(self) -> list[str]:
        recs = ["review model performance and recent signals before resuming",
                "verify data feed health and broker connectivity"]
        if any(t.category in (BreakerCategory.DAILY_LOSS, BreakerCategory.WEEKLY_LOSS,
                              BreakerCategory.MONTHLY_LOSS) for t in self._sticky):
            recs.append("analyze losing positions for a common factor (sector/regime)")
        if any(t.category is BreakerCategory.DRAWDOWN for t in self._sticky):
            recs.append("run forced backtest review of the last 90 days")
            recs.append(f"cooling-off period active: {self._cfg.recovery.cooling_off_days} day(s)")
        if any(t.category is BreakerCategory.POSITION_MISMATCH for t in self._sticky):
            recs.append("reconcile broker positions manually and call clear_position_mismatch()")
        if self._killed:
            recs.append("kill switch requires explicit operator acknowledgement to resume")
        return recs
