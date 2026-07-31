"""Semi-automated approval queue for trade signals.

Behavior is driven entirely by ``trading.automation_mode``:

* ``semi_automated`` — every candidate signal **enqueues** pending human
  approval. Each entry carries a TTL; unactioned entries **expire** and are
  dropped so stale signals are never executed.
* ``full_auto`` — the queue is **bypassed**: :meth:`ApprovalQueue.bypass`
  returns ``True`` and callers execute immediately.
* ``hybrid`` — high-confidence signals bypass; the rest enqueue.
* ``manual`` — everything enqueues (the operator drives execution).

The queue is **persisted in the database** (the ``system_state`` KV table,
key ``approval_queue``) so it survives a process restart: on construction
the queue rehydrates any still-pending entries, and every state transition
is also recorded in ``automation_log`` for the audit trail.

ALL time is read from an injected clock (``now_fn``); there is no wall-clock
in the transition logic, so expiry / TTL branches are deterministic in tests.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Optional

from data.database import DatabaseManager
from utils.config import AppConfig, load_config
from utils.helpers import to_iso_z, to_utc, utc_now
from utils.logger import get_logger

__all__ = [
    "ApprovalStatus",
    "QueuedSignal",
    "ApprovalQueue",
    "ApprovalError",
]

_log = get_logger("automation")


class ApprovalStatus(str):
    """Lifecycle states for a queued signal."""


# Explicit constants (kept as plain strings so they serialize cleanly).
PENDING = "PENDING"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
EXPIRED = "EXPIRED"
EXECUTED = "EXECUTED"
CANCELLED = "CANCELLED"

#: All reachable statuses, for validation.
_ALL_STATUSES = frozenset({PENDING, APPROVED, REJECTED, EXPIRED, EXECUTED, CANCELLED})

#: Statuses considered terminal (no further transition allowed).
_TERMINAL = frozenset({REJECTED, EXPIRED, EXECUTED, CANCELLED})

#: The authoritative approval-transition table. ``_ALLOWED[from]`` is the set
#: of destination statuses reachable from ``from``. This is the gate that
#: enforces a sane lifecycle (e.g. an EXECUTED signal can never be re-approved).
_ALLOWED: dict[str, frozenset[str]] = {
    PENDING: frozenset({APPROVED, REJECTED, EXPIRED, CANCELLED}),
    APPROVED: frozenset({EXECUTED, CANCELLED}),
    REJECTED: frozenset(),
    EXPIRED: frozenset(),
    EXECUTED: frozenset(),
    CANCELLED: frozenset(),
}

#: Default TTL for a queued signal when none is configured (30 minutes).
DEFAULT_TTL_SECONDS = 1800.0


@dataclass
class QueuedSignal:
    """One signal awaiting or carrying a human decision."""

    signal_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    strategy: str = "default"
    confidence: float = 0.0
    enqueued_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    status: str = PENDING
    decided_at: Optional[datetime] = None
    decision_by: Optional[str] = None
    reason: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("enqueued_at", "expires_at", "decided_at"):
            val = data.get(key)
            data[key] = to_iso_z(val) if isinstance(val, datetime) else val
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "QueuedSignal":
        from utils.helpers import parse_datetime

        def _dt(value: Any) -> Optional[datetime]:
            return parse_datetime(value) if value else None

        return cls(
            signal_id=str(data["signal_id"]),
            symbol=str(data["symbol"]),
            side=str(data["side"]),
            quantity=float(data["quantity"]),
            price=float(data["price"]),
            strategy=str(data.get("strategy", "default")),
            confidence=float(data.get("confidence", 0.0) or 0.0),
            enqueued_at=_dt(data.get("enqueued_at")),
            expires_at=_dt(data.get("expires_at")),
            status=str(data.get("status", PENDING)),
            decided_at=_dt(data.get("decided_at")),
            decision_by=data.get("decision_by"),
            reason=data.get("reason"),
            meta=dict(data.get("meta") or {}),
        )


class ApprovalError(Exception):
    """Raised on an illegal approval-queue transition or unknown signal."""


class ApprovalQueue:
    """Persisted, TTL-aware approval queue driven by ``automation_mode``."""

    _KV_KEY = "approval_queue"

    def __init__(
        self,
        config: Optional[AppConfig] = None,
        *,
        now_fn: Optional[Callable[[], datetime]] = None,
        db: Optional[DatabaseManager] = None,
        ttl_seconds: Optional[float] = None,
    ) -> None:
        self.config = config or load_config()
        self._now = now_fn or utc_now
        self.db = db
        self.ttl_seconds = (
            float(ttl_seconds) if ttl_seconds is not None else DEFAULT_TTL_SECONDS
        )
        self._signals: dict[str, QueuedSignal] = {}
        self._load()

    # ------------------------------------------------------------------
    # Mode / bypass policy
    # ------------------------------------------------------------------
    @property
    def automation_mode(self) -> str:
        return self.config.trading.automation_mode

    def requires_approval(self) -> bool:
        """True when the mode routes at least some signals through the queue.

        ``full_auto`` returns False (nothing is queued); every other mode
        (semi_automated / hybrid / manual) returns True.
        """
        return self.automation_mode != "full_auto"

    def bypass(self, confidence: Optional[float] = None) -> bool:
        """Should this signal skip the queue and execute immediately?

        * ``full_auto``  -> always bypass
        * ``hybrid``     -> bypass when ``confidence`` >= the restricted
          confidence gate (high-conviction auto-execute, the rest queue)
        * ``manual`` / ``semi_automated`` -> never bypass (queue everything)
        """
        mode = self.automation_mode
        if mode == "full_auto":
            return True
        if mode == "hybrid" and confidence is not None:
            return float(confidence) >= float(self.config.trading.min_confidence_restricted)
        return False

    # ------------------------------------------------------------------
    # Enqueue / decide / expire
    # ------------------------------------------------------------------
    def enqueue(
        self,
        signal_id: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        *,
        strategy: str = "default",
        confidence: float = 0.0,
        ttl_seconds: Optional[float] = None,
        meta: Optional[dict[str, Any]] = None,
    ) -> QueuedSignal:
        """Add a signal to the queue (status PENDING) and persist it."""
        now = self._now()
        ttl = float(ttl_seconds) if ttl_seconds is not None else self.ttl_seconds
        signal = QueuedSignal(
            signal_id=signal_id,
            symbol=symbol.upper(),
            side=side.upper(),
            quantity=float(quantity),
            price=float(price),
            strategy=strategy,
            confidence=float(confidence),
            enqueued_at=now,
            expires_at=now + timedelta(seconds=ttl),
            status=PENDING,
            meta=dict(meta or {}),
        )
        self._signals[signal_id] = signal
        self._persist(action="enqueue", signal=signal)
        _log.info("signal {} enqueued for approval ({} {} {} @ {}, ttl {:.0f}s)",
                  signal_id, signal.side, signal.quantity, signal.symbol, signal.price, ttl)
        return signal

    def approve(self, signal_id: str, *, by: str = "operator") -> QueuedSignal:
        return self._transition(signal_id, APPROVED, by=by)

    def reject(self, signal_id: str, *, by: str = "operator",
               reason: str = "") -> QueuedSignal:
        return self._transition(signal_id, REJECTED, by=by, reason=reason)

    def cancel(self, signal_id: str, *, by: str = "operator",
               reason: str = "") -> QueuedSignal:
        return self._transition(signal_id, CANCELLED, by=by, reason=reason)

    def mark_executed(self, signal_id: str, *, by: str = "system") -> QueuedSignal:
        return self._transition(signal_id, EXECUTED, by=by)

    def _transition(
        self,
        signal_id: str,
        target: str,
        *,
        by: str,
        reason: str = "",
    ) -> QueuedSignal:
        """Apply a lifecycle transition gated by the ``_ALLOWED`` table.

        This is the **approval-transition** function: it enforces that a
        signal can only move along a legal edge (e.g. PENDING -> APPROVED ->
        EXECUTED), records the actor + timestamp, and persists the result.
        Illegal moves raise :class:`ApprovalError`.
        """
        if target not in _ALL_STATUSES:
            raise ApprovalError(f"unknown target status: {target!r}")
        signal = self._signals.get(signal_id)
        if signal is None:
            raise ApprovalError(f"unknown signal: {signal_id!r}")
        if target not in _ALLOWED.get(signal.status, frozenset()):
            raise ApprovalError(
                f"illegal transition {signal.status} -> {target} for {signal_id!r}")
        now = self._now()
        signal.status = target
        signal.decided_at = now
        signal.decision_by = by
        if reason:
            signal.reason = reason
        self._persist(action=f"transition:{target}", signal=signal,
                      extra={"by": by, "reason": reason} if reason else {"by": by})
        return signal

    def expire_due(self) -> list[QueuedSignal]:
        """Expire every PENDING signal whose TTL has elapsed; returns them."""
        now = self._now()
        expired: list[QueuedSignal] = []
        for signal in self._signals.values():
            if signal.status == PENDING and signal.expires_at is not None and now >= signal.expires_at:
                signal.status = EXPIRED
                signal.decided_at = now
                signal.decision_by = "system"
                signal.reason = "ttl_expired"
                expired.append(signal)
        for signal in expired:
            self._persist(action="transition:EXPIRED", signal=signal,
                          extra={"reason": "ttl_expired"})
        return expired

    # ------------------------------------------------------------------
    # Read views
    # ------------------------------------------------------------------
    def get(self, signal_id: str) -> Optional[QueuedSignal]:
        return self._signals.get(signal_id)

    def all(self) -> list[QueuedSignal]:
        return list(self._signals.values())

    def pending(self) -> list[QueuedSignal]:
        return [s for s in self._signals.values() if s.status == PENDING]

    def approved(self) -> list[QueuedSignal]:
        return [s for s in self._signals.values() if s.status == APPROVED]

    def next_approved(self) -> Optional[QueuedSignal]:
        """Oldest APPROVED signal (FIFO execution), or None."""
        items = [s for s in self._signals.values() if s.status == APPROVED]
        if not items:
            return None
        items.sort(key=lambda s: s.decided_at or s.enqueued_at or self._now())
        return items[0]

    # ------------------------------------------------------------------
    # Persistence (survives restart)
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if self.db is None:
            return
        try:
            raw = self.db.kv_get(self._KV_KEY, default=[]) or []
            for entry in raw:
                signal = QueuedSignal.from_dict(entry)
                self._signals[signal.signal_id] = signal
            if raw:
                _log.info("approval queue restored: {} signal(s)", len(raw))
        except Exception as exc:
            _log.warning("could not restore approval queue: {}", exc)

    def _persist(self, *, action: str, signal: QueuedSignal,
                 extra: Optional[dict[str, Any]] = None) -> None:
        """Snapshot the whole queue to the DB + append an automation_log row."""
        if self.db is None:
            return
        try:
            self.db.kv_set(self._KV_KEY, [s.to_dict() for s in self._signals.values()])
            details = {"signal_id": signal.signal_id, "status": signal.status,
                       "symbol": signal.symbol, "side": signal.side,
                       "quantity": signal.quantity, "price": signal.price}
            if extra:
                details.update(extra)
            self.db.log_automation("approval_queue", action, signal.status.lower(),
                                   details=details)
        except Exception as exc:
            _log.warning("could not persist approval queue: {}", exc)
