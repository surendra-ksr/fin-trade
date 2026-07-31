"""Phase 14 operational hardening tests; all HTTP is mocked."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import requests

from automation.digest import DailyDigest
from automation.notify import WebhookNotifier, send_breaker_alert, send_digest_alert
from scripts.backup_db import backup_database, prune_backups


class _Response:
    def raise_for_status(self) -> None:
        return None


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append((url, dict(kwargs)))
        return _Response()


def test_webhook_notifier_reads_env_and_sends_breaker_payload():
    client = _Client()
    notifier = WebhookNotifier.from_env(environ={"FIN_TRADE_ALERT_WEBHOOK_URL": "https://alerts.example/hook"}, client=client)
    assert send_breaker_alert(notifier, category="drawdown", level="HALT", action="cancel orders")
    assert client.calls[0][0] == "https://alerts.example/hook"
    assert client.calls[0][1]["json"]["event"] == "breaker"
    assert client.calls[0][1]["timeout"] == 5.0


def test_webhook_notifier_is_safe_when_disabled_or_http_fails():
    assert not send_digest_alert(WebhookNotifier.from_env(environ={}), DailyDigest(date="2026-07-31"))

    class FailingClient:
        def post(self, *args: object, **kwargs: object) -> object:
            raise requests.ConnectionError("offline")

    notifier = WebhookNotifier("https://alerts.example/hook", FailingClient())
    assert not send_digest_alert(notifier, DailyDigest(date="2026-07-31"))


def test_backup_database_creates_valid_sqlite_copy(tmp_path: Path):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample (id INTEGER)")
        connection.execute("INSERT INTO sample VALUES (7)")
    backup = backup_database(source, tmp_path / "backups", now=1_000_000)
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT id FROM sample").fetchone() == (7,)


def test_prune_backups_removes_only_expired_database_files(tmp_path: Path):
    old = tmp_path / "old.db"
    recent = tmp_path / "recent.db"
    note = tmp_path / "note.txt"
    for path in (old, recent, note):
        path.write_text("x")
    old.touch()
    import os
    os.utime(old, (0, 0))
    os.utime(recent, (950_000, 950_000))
    assert prune_backups(tmp_path, retention_days=1, now=1_000_000) == [old]
    assert not old.exists() and recent.exists() and note.exists()


def test_prune_backups_rejects_negative_retention(tmp_path: Path):
    with pytest.raises(ValueError, match="non-negative"):
        prune_backups(tmp_path, retention_days=-1)
