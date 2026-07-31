"""Phase 11 dashboard — PURE python data providers.

Architecture rule (prevents reconciliation corruption)
-------------------------------------------------------
This module is the **single source of dashboard data**. Every page renders
exclusively from the typed functions defined here. The module imports
**nothing** from Streamlit (proven by ``test_dashboard_data_module_*``) so it
runs and is unit-tested in the clean CORE environment exactly like every
other safety-critical module. The thin Streamlit rendering lives in
``dashboard/pages/*.py`` and only calls these functions.

Design constraints (enforced)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Read-only.** Every provider reads ONLY the local SQLite DB (or local
  report files). There is no network import and no network call path.
* **Offline.** No provider touches a broker, a feed, or the filesystem other
  than the configured DB file / report directory.
* **Typed.** Every provider returns a typed structure (``dict`` or
  ``DataFrame``) so the Streamlit layer is a pure renderer.
* **Defensive.** A fresh/empty DB is a first-class case: every provider
  returns an empty/zero structure rather than raising.

The two mutation paths (kill switch + approval queue) live in
``dashboard/actions.py``; nothing here mutates state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

import pandas as pd

from data.database import DatabaseManager
from utils.config import AppConfig
from utils.constants import (
    STATE_POLICY_DEFAULTS,
    STATE_SEVERITY,
    CircuitBreakerState,
)
from utils.helpers import parse_datetime, to_iso_z, utc_now
from utils.logger import get_logger

__all__ = [
    "table_row_counts",
    "overview_view",
    "positions_view",
    "orders_view",
    "breaker_state_view",
    "limits_view",
    "models_view",
    "backtests_view",
    "logs_view",
]

_log = get_logger("dashboard")

#: Sentinel column lists so empty-DB results keep a stable schema.
_POSITIONS_COLUMNS = (
    "id", "portfolio_id", "symbol", "side", "quantity", "entry_time",
    "entry_price", "status", "realized_pnl", "fees", "strategy", "signal_id",
)
_ORDERS_COLUMNS = (
    "id", "symbol", "timestamp", "signal_type", "score", "confidence",
    "model_source", "timeframe", "price", "executed",
)
_MODELS_COLUMNS = (
    "id", "model_name", "version", "file_path", "metrics_json",
    "created_at", "updated_at",
)
_BREACH_COLUMNS = (
    "id", "timestamp", "limit_type", "entity", "value", "threshold",
    "action", "details",
)


# =============================================================================
# Helpers
# =============================================================================

def _empty(columns: tuple[str, ...]) -> pd.DataFrame:
    """An empty DataFrame with a fixed column schema (stable for renderers)."""
    return pd.DataFrame({col: pd.Series(dtype="object") for col in columns})


def _load_state_enum(row: Optional[Mapping[str, Any]]) -> CircuitBreakerState:
    """Coerce a persisted breaker_state row into a validated enum.

    An unknown/missing value degrades to NORMAL rather than raising — the
    dashboard must render even on a corrupt first-run row.
    """
    if row is None:
        return CircuitBreakerState.NORMAL
    try:
        return CircuitBreakerState(str(row.get("state", "NORMAL")))
    except ValueError:
        _log.warning("unknown persisted breaker state {!r}; rendering as NORMAL",
                     row.get("state"))
        return CircuitBreakerState.NORMAL


def _derive_trading_policy(
    state: CircuitBreakerState,
    triggers: list[dict[str, Any]],
    *,
    locked_until: Optional[str] = None,
    kill_switch: bool = False,
) -> dict[str, Any]:
    """Read-only reconstruction of the active TradingPolicy.

    Mirrors the circuit-breaker manager's merge of ``STATE_POLICY_DEFAULTS``
    with the worst active trigger's metadata, but WITHOUT calling
    ``evaluate()`` (which persists + logs). The dashboard only renders the
    persisted snapshot, so it derives the policy from the latched state +
    triggers that were already persisted by the live manager.
    """
    from risk.circuit_breakers import TradingPolicy  # local import keeps data.py import-light

    defaults = STATE_POLICY_DEFAULTS.get(
        state, STATE_POLICY_DEFAULTS[CircuitBreakerState.NORMAL])
    multiplier = float(defaults["position_size_multiplier"])
    confidence_boost = float(defaults["confidence_boost"])
    max_positions = int(defaults["max_open_positions"])
    allow_longs = bool(defaults["allow_new_longs"])
    allow_shorts = bool(defaults["allow_new_shorts"])
    allow_entries = bool(defaults["allow_new_entries"])
    flatten_all = False
    cancel_pending = False
    reasons: list[str] = []

    for trigger in triggers:
        md = dict(trigger.get("metadata") or {})
        desc = str(trigger.get("description") or trigger.get("category") or "")
        if desc:
            reasons.append(desc)
        if "size_multiplier" in md:
            multiplier = min(multiplier, float(md["size_multiplier"]))
        if "confidence_boost" in md:
            confidence_boost = max(confidence_boost, float(md["confidence_boost"]))
        if "max_open_positions" in md:
            max_positions = min(max_positions, int(md["max_open_positions"]))
        if md.get("block_longs"):
            allow_longs = False
        if md.get("block_shorts"):
            allow_shorts = False
        action = str(md.get("action") or "")
        if action in {"block_entries", "block_entries_reduce"}:
            allow_entries = False
        if action == "block_entries_reduce":
            cancel_pending = True
        if action == "flatten_all":
            flatten_all = True
            allow_entries = False
            allow_longs = allow_shorts = False
            cancel_pending = True

    if kill_switch:
        flatten_all = True
        cancel_pending = True
        allow_entries = allow_longs = allow_shorts = False
        multiplier = 0.0
    if locked_until:
        allow_entries = False

    policy = TradingPolicy(
        state=state,
        position_size_multiplier=multiplier,
        confidence_boost=max(0.0, min(1.0, confidence_boost)),
        allow_new_entries=allow_entries,
        allow_new_longs=allow_longs and allow_entries,
        allow_new_shorts=allow_shorts and allow_entries,
        flatten_all=flatten_all,
        cancel_pending_orders=cancel_pending,
        max_open_positions=max_positions,
        locked_until=(parse_datetime(locked_until) if locked_until else None),
        reasons=reasons,
    )
    data = policy.to_dict()
    # add a convenience severity rank for the renderer
    data["severity_rank"] = STATE_SEVERITY[state]
    return data


# =============================================================================
# Providers — one per dashboard page
# =============================================================================

def table_row_counts(db: DatabaseManager) -> dict[str, int]:
    """Row count per user table (the DB health panel)."""
    try:
        return db.table_stats()
    except Exception as exc:  # dashboard must never crash on a corrupt DB
        _log.error("table_row_counts failed: {}", exc)
        return {}


def overview_view(db: DatabaseManager) -> dict[str, Any]:
    """Portfolio overview: latest equity snapshot + headline counts.

    A completely empty DB returns zeros/None — the overview renders an
    empty-state rather than raising.
    """
    perf = db.query_df(
        "SELECT date, portfolio_id, portfolio_value, cash, invested_value, "
        "daily_return, cumulative_return, drawdown, sharpe "
        "FROM performance_metrics ORDER BY date DESC LIMIT 1")
    if perf.empty:
        latest: dict[str, Any] = {}
    else:
        row = perf.iloc[0].to_dict()
        latest = {k: (None if pd.isna(v) else v) for k, v in row.items()}

    open_positions = db.fetch_open_paper_trades()
    realized = db.query_df(
        "SELECT COALESCE(SUM(realized_pnl), 0.0) AS realized_pnl, "
        "COUNT(*) AS closed_trades FROM paper_trades WHERE status = 'CLOSED'"
    )
    realized_pnl = float(realized.iloc[0]["realized_pnl"]) if not realized.empty else 0.0
    closed_trades = int(realized.iloc[0]["closed_trades"]) if not realized.empty else 0

    signals = db.query(
        "SELECT COUNT(*) AS n, COALESCE(SUM(executed), 0) AS executed "
        "FROM trade_signals")
    sig_total = int(signals[0]["n"]) if signals else 0
    sig_exec = int(signals[0]["executed"]) if signals else 0

    breaker = db.load_breaker_state()
    state_enum = _load_state_enum(breaker)

    return {
        "latest_equity": latest.get("portfolio_value"),
        "cash": latest.get("cash"),
        "invested_value": latest.get("invested_value"),
        "daily_return": latest.get("daily_return"),
        "cumulative_return": latest.get("cumulative_return"),
        "drawdown": latest.get("drawdown"),
        "sharpe": latest.get("sharpe"),
        "equity_snapshot_date": latest.get("date"),
        "open_positions_count": int(len(open_positions)),
        "realized_pnl": realized_pnl,
        "closed_trades": closed_trades,
        "signals_total": sig_total,
        "signals_executed": sig_exec,
        "watchlist_symbols": len(db.list_price_symbols()),
        "breaker_state": state_enum.value,
        "breaker_severity": STATE_SEVERITY[state_enum],
    }


def positions_view(db: DatabaseManager) -> pd.DataFrame:
    """Open paper positions (entry ref + strategy + signal). Empty on fresh DB."""
    df = db.fetch_open_paper_trades()
    if df.empty:
        return _empty(_POSITIONS_COLUMNS)
    keep = [c for c in _POSITIONS_COLUMNS if c in df.columns]
    out = df[keep].copy()
    # market value at entry + cost basis for the renderer
    if {"entry_price", "quantity"}.issubset(set(out.columns)):
        out["cost_basis"] = out["entry_price"].astype(float) * out["quantity"].astype(float)
    return out


def orders_view(db: DatabaseManager, *, limit: int = 200) -> pd.DataFrame:
    """Recent trade signals with their execution flag (the orders feed)."""
    limit = max(1, int(limit))
    df = db.query_df(
        "SELECT id, symbol, timestamp, signal_type, score, confidence, "
        "model_source, timeframe, price, executed FROM trade_signals "
        "ORDER BY timestamp DESC LIMIT ?", (limit,))
    if df.empty:
        return _empty(_ORDERS_COLUMNS)
    return df


def breaker_state_view(db: DatabaseManager, config: AppConfig) -> dict[str, Any]:
    """Breaker-state panel: STATE_SEVERITY + active TradingPolicy.

    Reconstructs the panel from the persisted ``breaker_state`` row (the same
    row the live ``CircuitBreakerManager`` restores on restart). It is
    **read-only**: it derives the policy from the latched state + triggers and
    never calls ``evaluate()`` (which persists/logs).
    """
    row = db.load_breaker_state()
    state_enum = _load_state_enum(row)
    raw_triggers = []
    kill_switch = False
    locked_until: Optional[str] = None
    recovery_start: Optional[str] = None
    notes: Optional[str] = None
    anchors: dict[str, Any] = {}
    if row is not None:
        raw_triggers = list(row.get("active_breakers") or [])
        kill_switch = any(
            str(t.get("category")) == "kill_switch" for t in raw_triggers
            if isinstance(t, dict))
        locked_until = row.get("locked_until")
        recovery_start = row.get("recovery_start")
        notes = row.get("notes")
        anchors = {
            "day": row.get("day_anchor"),
            "week": row.get("week_anchor"),
            "month": row.get("month_anchor"),
            "peak_equity": row.get("peak_equity"),
            "day_key": row.get("day_key"),
            "week_key": row.get("week_key"),
            "month_key": row.get("month_key"),
        }
    if str(notes) == "kill_switch":
        kill_switch = True

    policy = _derive_trading_policy(
        state_enum, raw_triggers, locked_until=locked_until, kill_switch=kill_switch)

    return {
        "state": state_enum.value,
        "severity": STATE_SEVERITY[state_enum],
        "kill_switch_engaged": kill_switch,
        "locked_until": locked_until,
        "recovery_started_at": recovery_start,
        "active_triggers": raw_triggers,
        "anchors": anchors,
        "policy": policy,
    }


def limits_view(db: DatabaseManager, *, limit: int = 200) -> pd.DataFrame:
    """Recent order-limit gateway breaches/alerts. Empty when none."""
    limit = max(1, int(limit))
    df = db.fetch_limit_breaches(limit=limit)
    if df.empty:
        return _empty(_BREACH_COLUMNS)
    keep = [c for c in _BREACH_COLUMNS if c in df.columns]
    return df[keep].copy()


def models_view(db: DatabaseManager) -> pd.DataFrame:
    """Registered model versions (defensive: table is created lazily)."""
    tables = set(db.list_tables())
    if "model_registry" not in tables:
        return _empty(_MODELS_COLUMNS)
    df = db.query_df(
        "SELECT id, model_name, version, file_path, metrics_json, "
        "created_at, updated_at FROM model_registry ORDER BY updated_at DESC")
    if df.empty:
        return _empty(_MODELS_COLUMNS)
    return df


def backtests_view(report_dir: Path) -> list[dict[str, Any]]:
    """Backtest report artifacts on disk (json/csv), newest-first.

    The report directory may not exist yet on a fresh install — returns [].
    """
    if not report_dir.exists() or not report_dir.is_dir():
        return []
    artifacts: list[dict[str, Any]] = []
    for path in report_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".json", ".csv"}:
            continue
        stat = path.stat()
        artifacts.append({
            "name": path.name,
            "path": str(path.relative_to(report_dir)),
            "type": path.suffix.lower().lstrip("."),
            "size_bytes": int(stat.st_size),
            "modified": to_iso_z(pd.Timestamp(stat.st_mtime, unit="s", tz="UTC")),
        })
    artifacts.sort(key=lambda a: a["modified"], reverse=True)
    return artifacts


def logs_view(db: DatabaseManager, *, limit: int = 200) -> dict[str, pd.DataFrame]:
    """Automation + circuit-breaker audit logs (newest-first)."""
    limit = max(1, int(limit))
    automation = db.fetch_automation_log(limit=limit)
    breakers = db.fetch_breaker_events(limit=limit)
    return {
        "automation": automation,
        "circuit_breakers": breakers,
    }


# =============================================================================
# DB accessor used by the Streamlit shell — resolves the configured DB path.
# =============================================================================

def resolve_database(config: AppConfig) -> DatabaseManager:
    """Open the configured local SQLite DB for dashboard reads.

    Always the *local* database path from config — never a broker/feed. The
    dashboard holds no long-lived writes, so a fresh manager per request is
    safe and avoids cross-thread connection reuse.
    """
    path = config.resolve_path(config.data.database_path)
    return DatabaseManager(path)
