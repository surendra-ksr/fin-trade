"""Startup reconciliation: DB positions/orders vs broker state.

On startup the orchestrator compares the authoritative local view (the
``paper_trades`` / ``live_trades`` ledger) against the broker's reported
positions. Any divergence is:

* logged in detail to ``automation_log`` (audit trail), and
* escalated to the :class:`CircuitBreakerManager` as a sticky
  ``POSITION_MISMATCH`` trigger, which **halts new entries** per policy until
  a human clears the mismatch (``clear_position_mismatch``).

The broker state is supplied by the caller (an adapter's ``list_positions``),
so this module never touches the network and is fully unit-testable with a
plain list of dicts.

All time is read from an injected clock; no wall-clock in the comparison.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from data.database import DatabaseManager
from utils.config import AppConfig, load_config
from utils.helpers import to_iso_z, utc_now
from utils.logger import get_logger

__all__ = ["ReconcileResult", "reconcile_positions"]

_log = get_logger("automation")

#: Absolute quantity delta below which a position is considered "matched".
#: (handles float rounding from broker share lots).
DEFAULT_TOLERANCE = 1e-6


@dataclass
class ReconcileResult:
    """Outcome of a reconciliation pass."""

    matched: list[str] = field(default_factory=list)
    db_only: list[dict[str, float]] = field(default_factory=list)
    broker_only: list[dict[str, float]] = field(default_factory=list)
    quantity_mismatches: list[dict[str, Any]] = field(default_factory=list)
    halted: bool = False
    summary: str = ""

    @property
    def ok(self) -> bool:
        return not (self.db_only or self.broker_only or self.quantity_mismatches)


def _db_positions(db: DatabaseManager, portfolio_id: str) -> dict[str, float]:
    """Aggregate OPEN paper_trades into symbol -> signed quantity."""
    rows = db.query(
        "SELECT symbol, side, quantity FROM paper_trades "
        "WHERE portfolio_id = ? AND status = 'OPEN'",
        (portfolio_id,),
    )
    net: dict[str, float] = {}
    for r in rows:
        qty = float(r["quantity"])
        side = str(r["side"]).upper()
        signed = qty if side in {"BUY", "LONG"} else -qty
        net[r["symbol"]] = net.get(r["symbol"], 0.0) + signed
    return net


def _normalize_broker(positions: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Broker positions (various key spellings) -> symbol -> signed quantity."""
    net: dict[str, float] = {}
    for pos in positions or []:
        symbol = str(pos.get("symbol") or pos.get("Symbol") or "").upper()
        if not symbol:
            continue
        qty = pos.get("quantity", pos.get("qty", pos.get("shares", 0.0)))
        side = str(pos.get("side", pos.get("position", "long"))).lower()
        signed = abs(float(qty)) * (1.0 if side in {"long", "buy"} else -1.0)
        net[symbol] = net.get(symbol, 0.0) + signed
    return net


def reconcile_positions(
    db: DatabaseManager,
    broker_positions: Sequence[Mapping[str, Any]],
    *,
    config: Optional[AppConfig] = None,
    breaker: Any = None,
    portfolio_id: str = "default",
    tolerance: float = DEFAULT_TOLERANCE,
    now_fn: Optional[Callable[[], datetime]] = None,
) -> ReconcileResult:
    """Compare DB positions vs ``broker_positions``; halt on mismatch.

    Args:
        db: the authoritative :class:`DatabaseManager`.
        broker_positions: broker-reported positions as a list of mappings
            (``{"symbol": "AAPL", "quantity": 10, "side": "long"}``).
        breaker: optional :class:`CircuitBreakerManager`; when supplied and a
            mismatch is found, :meth:`report_position_mismatch` is called so
            the sticky ``POSITION_MISMATCH`` trigger halts new entries.

    Returns:
        A :class:`ReconcileResult` with every divergence enumerated.
    """
    cfg = config or load_config()
    now = (now_fn or utc_now)()
    db_net = _db_positions(db, portfolio_id)
    broker_net = _normalize_broker(broker_positions)

    result = ReconcileResult()
    symbols = sorted(set(db_net) | set(broker_net))
    for symbol in symbols:
        db_qty = db_net.get(symbol, 0.0)
        broker_qty = broker_net.get(symbol, 0.0)
        if db_qty == 0.0 and broker_qty == 0.0:
            continue
        if abs(db_qty) <= tolerance and abs(broker_qty) > tolerance:
            result.broker_only.append({"symbol": symbol, "quantity": broker_qty})
        elif abs(broker_qty) <= tolerance and abs(db_qty) > tolerance:
            result.db_only.append({"symbol": symbol, "quantity": db_qty})
        elif abs(db_qty - broker_qty) <= tolerance:
            result.matched.append(symbol)
        else:
            result.quantity_mismatches.append({
                "symbol": symbol, "db_quantity": db_qty,
                "broker_quantity": broker_qty,
                "delta": db_qty - broker_qty,
            })

    # Escalate + log.
    divergences = (result.db_only or []) + (result.broker_only or []) + (
        result.quantity_mismatches or [])
    result.halted = bool(divergences)
    if divergences and breaker is not None and hasattr(breaker, "report_position_mismatch"):
        mismatch_names = [d.get("symbol") for d in divergences]
        breaker.report_position_mismatch(mismatch_names)

    details = {
        "matched": result.matched,
        "db_only": result.db_only,
        "broker_only": result.broker_only,
        "quantity_mismatches": result.quantity_mismatches,
        "halted": result.halted,
        "at": to_iso_z(now),
    }
    result.summary = (
        f"matched={len(result.matched)} db_only={len(result.db_only)} "
        f"broker_only={len(result.broker_only)} "
        f"qty_mismatch={len(result.quantity_mismatches)} "
        f"halted={'yes' if result.halted else 'no'}")
    try:
        db.log_automation(
            "reconcile", "startup_reconciliation",
            "halt" if result.halted else "ok", details=details)
    except Exception as exc:
        _log.warning("could not persist reconciliation result: {}", exc)
    _log.info("reconciliation: {}", result.summary)
    return result
