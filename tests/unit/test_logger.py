"""Tests for utils/logger.py — sinks, routing, rotation config."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from utils.logger import (
    configure_logging,
    flush,
    get_logger,
    iter_log_files,
    log_event,
    suppress,
    timed_block,
)


@pytest.fixture()
def log_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "logs"
    configure_logging({
        "level": "DEBUG",
        "log_to_file": True,
        "log_dir": str(directory),
        "rotate_daily": True,
        "keep_days": 5,
        "structured_json": True,
    })
    yield directory
    configure_logging({"log_to_file": False, "level": "WARNING"})


class TestConfiguration:
    def test_console_only_config_is_safe(self) -> None:
        configure_logging({"log_to_file": False})
        get_logger("app").info("console-only works")

    def test_creates_log_directory(self, log_dir: Path) -> None:
        assert log_dir.exists()

    def test_invalid_level_falls_back(self, tmp_path: Path) -> None:
        configure_logging({"level": "BOGUS", "log_to_file": True,
                           "log_dir": str(tmp_path / "x")})
        get_logger("app").info("still works")
        configure_logging({"log_to_file": False, "level": "WARNING"})


class TestRouting:
    def test_app_file_captures_all(self, log_dir: Path) -> None:
        get_logger("trades").info("fill happened", symbol="AAPL")
        get_logger("signals").info("signal generated")
        get_logger("app").warning("plain app event")
        flush()
        app_files = list(log_dir.glob("app_*.log"))
        assert app_files, "app_* log file missing"
        content = app_files[0].read_text()
        assert "fill happened" in content
        assert "signal generated" in content
        assert "plain app event" in content

    def test_category_files_json_lines(self, log_dir: Path) -> None:
        get_logger("circuit_breakers").error("breaker tripped", breaker="daily_loss", level=3)
        get_logger("trades").info("paper fill")
        flush()
        cb_files = list(log_dir.glob("circuit_breakers_*.log"))
        assert cb_files
        lines = [ln for ln in cb_files[0].read_text().splitlines() if ln.strip()]
        assert lines
        record = json.loads(lines[0])["record"]
        assert record["message"] == "breaker tripped"
        assert record["extra"]["category"] == "circuit_breakers"
        assert record["extra"]["breaker"] == "daily_loss"

        trade_files = list(log_dir.glob("trades_*.log"))
        assert trade_files
        assert "paper fill" in trade_files[0].read_text()
        # trades file must NOT contain the breaker event
        assert "breaker tripped" not in trade_files[0].read_text()

    def test_errors_file_captures_all_errors(self, log_dir: Path) -> None:
        get_logger("signals").error("signal boom")
        get_logger("app").info("not an error")
        flush()
        error_files = list(log_dir.glob("errors_*.log"))
        assert error_files
        content = error_files[0].read_text()
        assert "signal boom" in content
        assert "not an error" not in content

    def test_unknown_category_falls_back_to_app(self, log_dir: Path) -> None:
        get_logger("made_up_category").info("hello fallback")
        flush()
        app_files = list(log_dir.glob("app_*.log"))
        assert "hello fallback" in app_files[0].read_text()

    def test_log_event_helper(self, log_dir: Path) -> None:
        log_event("risk", "warning", "risk warning fired", metric="var", value=0.02)
        flush()
        risk_files = list(log_dir.glob("risk_*.log"))
        assert risk_files
        record = json.loads(risk_files[0].read_text().splitlines()[0])["record"]
        assert record["message"] == "risk warning fired"
        assert record["extra"]["metric"] == "var"

    def test_stdlib_logging_intercepted(self, log_dir: Path) -> None:
        logging.getLogger("somelib").warning("stdlib warning")
        flush()
        app_files = list(log_dir.glob("app_*.log"))
        assert "stdlib warning" in app_files[0].read_text()


class TestHelpers:
    def test_suppress_swallows_and_logs(self, log_dir: Path) -> None:
        @suppress(default="fallback", category="errors", message="{func} blew up")
        def boom() -> str:
            raise RuntimeError("kaput")

        assert boom() == "fallback"
        flush()
        app_files = list(log_dir.glob("app_*.log"))
        assert "kaput" in app_files[0].read_text() or "blew up" in app_files[0].read_text()

    def test_suppress_reraise(self) -> None:
        @suppress(reraise=True)
        def boom() -> None:
            raise ValueError("nope")

        with pytest.raises(ValueError):
            boom()

    def test_timed_block_logs(self, log_dir: Path) -> None:
        with timed_block("unit block", category="app", level="INFO"):
            sum(range(1000))
        flush()
        app_files = list(log_dir.glob("app_*.log"))
        assert "unit block completed" in app_files[0].read_text()

    def test_iter_log_files(self, log_dir: Path) -> None:
        get_logger("trades").info("x")
        flush()
        assert iter_log_files(log_dir)
        assert all(p.name.startswith("trades_") for p in iter_log_files(log_dir, ["trades"]))
