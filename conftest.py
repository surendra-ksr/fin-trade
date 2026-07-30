"""Root pytest configuration: import path + shared fixtures."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.database import DatabaseManager  # noqa: E402
from utils.config import AppConfig, load_config  # noqa: E402


@pytest.fixture()
def app_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    """A validated AppConfig whose paths point at a tmp sandbox.

    Built from the repo's real ``config.yaml`` (so schema drift breaks the
    suite) but with the database/log/report directories redirected into the
    test's tmp_path.
    """
    cfg = load_config(ROOT / "config.yaml")
    cfg.data.database_path = str(tmp_path / "test_trading.db")
    cfg.logging.log_dir = str(tmp_path / "logs")
    cfg.logging.log_to_file = False
    cfg.backtesting.report_dir = str(tmp_path / "reports")
    cfg.models.model_dir = str(tmp_path / "models")
    cfg.base_dir = str(tmp_path)
    return cfg


@pytest.fixture()
def db(tmp_path: Path) -> DatabaseManager:
    """Fresh file-backed database in a tmp directory."""
    manager = DatabaseManager(tmp_path / "test.db")
    try:
        yield manager
    finally:
        manager.close()


@pytest.fixture()
def mem_db() -> DatabaseManager:
    """In-memory database (fastest; single-threaded tests only)."""
    manager = DatabaseManager(":memory:")
    try:
        yield manager
    finally:
        manager.close()


@pytest.fixture(autouse=True)
def _quiet_console_logging() -> None:
    """Keep test output readable: console-only, errors only."""
    from utils.logger import configure_logging

    configure_logging({"log_to_file": False, "level": "CRITICAL"})


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure tests never observe a developer's real credentials/mode."""
    for var in ("FIN_TRADE_MODE_OVERRIDE", "FIN_TRADE_LIVE_AUTHORIZATION"):
        monkeypatch.delenv(var, raising=False)
    os.environ.setdefault("FIN_TRADE_TEST", "1")
