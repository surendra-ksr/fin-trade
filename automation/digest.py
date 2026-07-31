"""Daily digest: positions, P&L, breaker events, and limit breaches.

Builds a structured :class:`DailyDigest` from the SQLite audit tables for a
given exchange day (default: the most recent trading day in the database) and
renders a plain-text summary suitable for operator notifications / the
dashboard.

Sources:

* ``paper_trades`` — open positions and realized P&L for the day
* ``performance_metrics`` — equity / cash / daily return / drawdown
* ``circuit_breaker_log`` — breaker events that fired
* ``limit_breach_log`` — order-limit gateway rejections

All time is read from an injected clock; the digest never reads the wall
clock, so the "which trading day" resolution is deterministic in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

from data.database import DatabaseManager
from utils.config import AppConfig, load_config
from utils.helpers import (
    MARKET_TZ,
    UTC,
    is_trading_day,
    previous_trading_day,
    to_iso_z,
    utc_now,
)
from utils.logger import get_logger

__all__ = ["DailyDigest", "build_digest", "render_text"]

_log = get_logger("automation")


@dataclass
class DailyDigest:
    """One day's rolled-up snapshot for operator review."""

    date: str
    equity: Optional[float] = None
    cash: Optional[float] = None
    invested_value: Optional[float] = None
    daily_return: Optional[float] = None
    drawdown: Optional[float] = None
    realized_pnl: float = 0.0
    open_positions: list[dict[str, Any]] = field(default_factory=list)
    trades_today: int = 0
    new_positions_today: int = 0
    breaker_events: list[dict[str, Any]] = field(default_factory=list)
    breaches: list[dict[str, Any]] = field(default_factory=list)
    snapshot_source: str = "none"

    @property
    def breaker_count(self) -> int:
        return len(self.breaker_events)

    @property
    def breach_count(self) -> int:
        return len(self.breaches)

    @property
    def open_position_count(self) -> int:
        return len(self.open_positions)


def _resolve_day(db: DatabaseManager, *, day: Optional[date], now: datetime) -> date:
    """The digest day: the explicit ``day`` if given, else the most recent
    trading day on/before ``now`` (exchange-local)."""
    if day is not None:
        return day
    local_today = now.astimezone(MARKET_TZ).date()
    # If the market is still mid-session or before close, the just-finished
    # day is the previous trading day; otherwise it is today.
    return previous_trading_day(local_today, include=True) if is_trading_day(local_today) \
        else previous_trading_day(local_today)


def _day_window(day: date) -> tuple[str, str]:
    """[start, end) UTC ISO bounds covering the whole calendar day.

    US market sessions (pre 06:00 ET .. post 18:00 ET) always fall within a
    single UTC calendar day (EST: 11:00-23:00Z, EDT: 10:00-22:00Z), so a UTC
    calendar-day window both matches the ``performance_metrics`` midnight-UTC
    storage and captures every session event unambiguously.
    """
    start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=UTC)
    end = start + timedelta(days=1)
    return to_iso_z(start), to_iso_z(end)


def build_digest(
    db: DatabaseManager,
    *,
    config: Optional[AppConfig] = None,
    day: Optional[date] = None,
    now_fn: Optional[Callable[[], datetime]] = None,
) -> DailyDigest:
    """Build a :class:`DailyDigest` for ``day`` from the audit tables."""
    cfg = config or load_config()
    now = (now_fn or utc_now)()
    resolved = _resolve_day(db, day=day, now=now)
    start_iso, end_iso = _day_window(resolved)

    # Performance snapshot for the day (if present).
    perf = db.query_one(
        "SELECT portfolio_value, cash, invested_value, daily_return, drawdown "
        "FROM performance_metrics WHERE portfolio_id = ? AND date >= ? AND date < ? "
        "ORDER BY date DESC LIMIT 1",
        ("default", start_iso, end_iso),
    )
    snapshot_source = "performance_metrics" if perf else "none"
    equity = float(perf["portfolio_value"]) if perf and perf.get("portfolio_value") is not None else None
    cash = float(perf["cash"]) if perf and perf.get("cash") is not None else None
    invested_value = float(perf["invested_value"]) if perf and perf.get("invested_value") is not None else None
    daily_return = float(perf["daily_return"]) if perf and perf.get("daily_return") is not None else None
    drawdown = float(perf["drawdown"]) if perf and perf.get("drawdown") is not None else None

    # Realized P&L + counts from paper_trades closed today.
    pnl_row = db.query_one(
        "SELECT COALESCE(SUM(realized_pnl), 0.0) AS pnl, COUNT(*) AS n "
        "FROM paper_trades WHERE portfolio_id = ? AND status = 'CLOSED' "
        "AND exit_time IS NOT NULL AND exit_time >= ? AND exit_time < ?",
        ("default", start_iso, end_iso),
    ) or {"pnl": 0.0, "n": 0}
    realized_pnl = float(pnl_row.get("pnl") or 0.0)
    trades_today = int(pnl_row.get("n") or 0)

    new_positions_row = db.query_one(
        "SELECT COUNT(*) AS n FROM paper_trades WHERE portfolio_id = ? "
        "AND entry_time >= ? AND entry_time < ?",
        ("default", start_iso, end_iso),
    ) or {"n": 0}
    new_positions_today = int(new_positions_row.get("n") or 0)

    # Open positions at the end of the day.
    open_rows = db.query(
        "SELECT symbol, side, quantity, entry_price, strategy "
        "FROM paper_trades WHERE portfolio_id = ? AND status = 'OPEN' "
        "ORDER BY symbol",
        ("default",),
    )
    open_positions = [
        {
            "symbol": r["symbol"],
            "side": r["side"],
            "quantity": float(r["quantity"]),
            "entry_price": float(r["entry_price"]),
            "market_value": float(r["quantity"]) * float(r["entry_price"]),
            "strategy": r["strategy"],
        }
        for r in open_rows
    ]

    # Breaker events + breaches within the day window.
    breaker_events = db.query(
        "SELECT timestamp, category, level, state_before, state_after, action_taken "
        "FROM circuit_breaker_log WHERE timestamp >= ? AND timestamp < ? "
        "ORDER BY timestamp ASC",
        (start_iso, end_iso),
    )
    breaches = db.query(
        "SELECT timestamp, limit_type, entity, value, threshold, action "
        "FROM limit_breach_log WHERE timestamp >= ? AND timestamp < ? "
        "ORDER BY timestamp ASC",
        (start_iso, end_iso),
    )

    return DailyDigest(
        date=resolved.isoformat(),
        equity=equity,
        cash=cash,
        invested_value=invested_value,
        daily_return=daily_return,
        drawdown=drawdown,
        realized_pnl=realized_pnl,
        open_positions=open_positions,
        trades_today=trades_today,
        new_positions_today=new_positions_today,
        breaker_events=breaker_events,
        breaches=breaches,
        snapshot_source=snapshot_source,
    )


def render_text(digest: DailyDigest) -> str:
    """Plain-text rendering of a :class:`DailyDigest`."""
    lines: list[str] = []
    lines.append(f"=== Daily Digest — {digest.date} ===")
    if digest.equity is not None:
        lines.append(f"Equity:        ${digest.equity:,.2f}")
    if digest.cash is not None:
        lines.append(f"Cash:          ${digest.cash:,.2f}")
    if digest.invested_value is not None:
        lines.append(f"Invested:      ${digest.invested_value:,.2f}")
    if digest.daily_return is not None:
        lines.append(f"Daily return:  {digest.daily_return * 100:+.2f}%")
    if digest.drawdown is not None:
        lines.append(f"Drawdown:      {digest.drawdown * 100:+.2f}%")
    lines.append(f"Realized P&L:  ${digest.realized_pnl:,.2f}")
    lines.append(f"Trades today:  {digest.trades_today} ({digest.new_positions_today} new)")
    lines.append(f"Open positions: {digest.open_position_count}")
    for pos in digest.open_positions:
        lines.append(f"  - {pos['side']} {pos['quantity']:.4g} {pos['symbol']} "
                     f"@ ${pos['entry_price']:.2f}  (${pos['market_value']:,.2f})")
    lines.append(f"Breaker events: {digest.breaker_count}")
    for ev in digest.breaker_events:
        lines.append(f"  - [{ev.get('level', '?')}] {ev.get('category', '?')}: "
                     f"{ev.get('state_before', '')}->{ev.get('state_after', '')} "
                     f"{ev.get('action_taken', '')}")
    lines.append(f"Limit breaches: {digest.breach_count}")
    for br in digest.breaches:
        lines.append(f"  - {br.get('limit_type', '?')} {br.get('entity', '')}: "
                     f"{br.get('action', '')}")
    lines.append("=" * (21 + len(digest.date) + 1))
    return "\n".join(lines)
