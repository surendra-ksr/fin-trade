"""Shared dashboard bootstrap — config + local DB open (Streamlit-FREE).

Kept separate from the Streamlit rendering so it can be imported by the
headless boot smoke and unit-tested without the optional Streamlit tier.
The DB path is the configured LOCAL sqlite file (offline), optionally
overridden by ``FIN_TRADE_DASHBOARD_DB`` for the boot smoke / tests.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from data.database import DatabaseManager
from utils.config import AppConfig, load_config

__all__ = ["dashboard_config", "dashboard_db", "boot_check"]

#: Optional env override for the DB the dashboard reads (boot smoke / tests).
_DB_OVERRIDE_ENV = "FIN_TRADE_DASHBOARD_DB"
#: Optional env override for the config file (boot smoke / tests).
_CONFIG_OVERRIDE_ENV = "FIN_TRADE_CONFIG"


def dashboard_config(path: Optional[str] = None) -> AppConfig:
    """Load the master config, applying the dashboard DB override if set."""
    cfg = load_config(path or os.environ.get(_CONFIG_OVERRIDE_ENV) or "config.yaml")
    db_override = os.environ.get(_DB_OVERRIDE_ENV)
    if db_override:
        cfg.data.database_path = db_override
    return cfg


def dashboard_db(config: AppConfig) -> DatabaseManager:
    """Open the configured LOCAL sqlite DB (offline; never a broker/feed)."""
    return DatabaseManager(config.resolve_path(config.data.database_path))


def boot_check(config: AppConfig, db: DatabaseManager) -> dict[str, object]:
    """Exercise every data provider once against the DB (pure, no Streamlit).

    Called on dashboard boot so a corrupt/empty DB surfaces immediately and
    the headless boot smoke proves the full data layer comes up. Returns a
    summary dict; raises if any provider throws (fail-fast at boot).
    """
    from dashboard import data as ddata  # local import keeps this module light

    report_dir = Path("backtesting/reports")
    summary: dict[str, object] = {
        "row_counts": ddata.table_row_counts(db),
        "overview": ddata.overview_view(db),
        "positions": len(ddata.positions_view(db)),
        "orders": len(ddata.orders_view(db)),
        "breaker_state": ddata.breaker_state_view(db, config)["state"],
        "limits": len(ddata.limits_view(db)),
        "models": len(ddata.models_view(db)),
        "backtests": len(ddata.backtests_view(report_dir)),
        "logs": {k: len(v) for k, v in ddata.logs_view(db).items()},
        "db_path": str(db.path),
    }
    return summary
