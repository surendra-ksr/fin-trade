"""Tests for data/database.py — schema, CRUD, migrations, thread safety."""

from __future__ import annotations

import threading
from pathlib import Path

import pandas as pd
import pytest

from data.database import DatabaseManager, DatabaseError, get_database
from utils.constants import AlertLevel


def _bars(symbol_days: int = 5) -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-02", periods=symbol_days, freq="D", tz="UTC"),
        "open": [100 + i for i in range(symbol_days)],
        "high": [101 + i for i in range(symbol_days)],
        "low": [99 + i for i in range(symbol_days)],
        "close": [100.5 + i for i in range(symbol_days)],
        "volume": [1_000_000] * symbol_days,
    })


class TestSchema:
    EXPECTED_TABLES = {
        "price_data", "fundamental_data", "macro_data", "sentiment_data",
        "news_events", "trade_signals", "paper_trades", "live_trades",
        "performance_metrics", "circuit_breaker_log", "automation_log",
        "limit_breach_log", "patterns_detected", "breaker_state",
        "system_state", "schema_migrations",
    }

    def test_all_tables_created(self, db: DatabaseManager) -> None:
        assert self.EXPECTED_TABLES <= set(db.list_tables())

    def test_migration_recorded(self, db: DatabaseManager) -> None:
        versions = [r["version"] for r in db.query("SELECT version FROM schema_migrations")]
        assert versions == [1]

    def test_migrate_is_idempotent(self, db: DatabaseManager) -> None:
        db.migrate()
        versions = [r["version"] for r in db.query("SELECT version FROM schema_migrations")]
        assert versions == [1]

    def test_integrity_check(self, db: DatabaseManager) -> None:
        assert db.integrity_check()


class TestPriceData:
    def test_upsert_and_fetch_roundtrip(self, db: DatabaseManager) -> None:
        written = db.upsert_price_bars("aapl", "1d", _bars(5))
        assert written == 5
        df = db.fetch_price_bars("AAPL", "1d")
        assert len(df) == 5
        assert df["timestamp"].is_monotonic_increasing
        assert str(df["timestamp"].dtype).startswith("datetime64")

    def test_upsert_replaces_same_key(self, db: DatabaseManager) -> None:
        db.upsert_price_bars("AAPL", "1d", _bars(5))
        updated = _bars(5)
        updated.loc[4, "close"] = 999.0
        db.upsert_price_bars("AAPL", "1d", updated)
        assert db.count_price_bars("AAPL", "1d") == 5
        df = db.fetch_price_bars("AAPL", "1d")
        assert float(df.iloc[-1]["close"]) == 999.0

    def test_last_and_first_timestamp(self, db: DatabaseManager) -> None:
        db.upsert_price_bars("MSFT", "1h", _bars(3))
        assert db.get_last_bar_timestamp("MSFT", "1h") == "2024-01-04T00:00:00.000000Z"
        assert db.get_first_bar_timestamp("MSFT", "1h") == "2024-01-02T00:00:00.000000Z"
        assert db.get_last_bar_timestamp("NVDA", "1h") is None

    def test_fetch_with_range_and_limit(self, db: DatabaseManager) -> None:
        db.upsert_price_bars("AAPL", "1d", _bars(10))
        df = db.fetch_price_bars("AAPL", "1d",
                                 start="2024-01-05T00:00:00Z", limit=2)
        assert len(df) == 2
        assert df.iloc[0]["timestamp"] == pd.Timestamp("2024-01-05", tz="UTC")

    def test_list_symbols(self, db: DatabaseManager) -> None:
        db.upsert_price_bars("AAPL", "1d", _bars(2))
        db.upsert_price_bars("TSLA", "1d", _bars(2))
        db.upsert_price_bars("TSLA", "1h", _bars(2))
        assert db.list_price_symbols() == ["AAPL", "TSLA"]
        assert db.list_price_symbols("1h") == ["TSLA"]

    def test_upsert_rejects_bad_frame(self, db: DatabaseManager) -> None:
        with pytest.raises(DatabaseError):
            db.upsert_price_bars("AAPL", "1d", pd.DataFrame({"x": [1]}))

    def test_empty_frame_is_noop(self, db: DatabaseManager) -> None:
        assert db.upsert_price_bars("AAPL", "1d", pd.DataFrame()) == 0


class TestFundamentalMacroSentiment:
    def test_fundamentals_roundtrip(self, db: DatabaseManager) -> None:
        db.upsert_fundamentals("AAPL", "2024-06-01", {"trailingPE": 25.4, "beta": 1.2})
        db.upsert_fundamentals("AAPL", "2024-06-01", {"trailingPE": 26.0})  # replace
        df = db.fetch_fundamentals("AAPL", "trailingPE")
        assert len(df) == 1
        assert float(df.iloc[0]["value"]) == 26.0

    def test_macro_upsert_and_fetch(self, db: DatabaseManager) -> None:
        import datetime as dt
        rows = [(dt.date(2024, 1, d), 20.0 + d) for d in range(1, 6)]
        db.upsert_macro("VIXCLS", rows)
        df = db.fetch_macro("VIXCLS", start="2024-01-03T00:00:00Z")
        assert len(df) == 3
        assert df["date"].is_monotonic_increasing

    def test_sentiment_upsert_replace(self, db: DatabaseManager) -> None:
        db.upsert_sentiment("TSLA", "2024-06-01", "finbert", 0.42, volume=17,
                            payload={"source_count": 3})
        db.upsert_sentiment("TSLA", "2024-06-01", "finbert", 0.55)
        df = db.fetch_sentiment("TSLA", "finbert")
        assert len(df) == 1
        assert float(df.iloc[0]["score"]) == 0.55


class TestNewsAndSignals:
    def test_news_dedupe(self, db: DatabaseManager) -> None:
        first = db.insert_news("AAPL", "2024-06-01T10:00:00Z", "Apple announces AI",
                               source="testwire")
        again = db.insert_news("AAPL", "2024-06-01T10:00:00Z", "Apple announces AI",
                               source="testwire")
        assert first > 0
        assert again == 0  # UNIQUE natural key ignored
        df = db.fetch_news("AAPL")
        assert len(df) == 1

    def test_signal_lifecycle(self, db: DatabaseManager) -> None:
        db.insert_signal("sig-1", "AAPL", "2024-06-01T13:30:00Z", "BUY", "ensemble",
                         score=0.7, confidence=0.8, price=190.0,
                         rationale={"technical": 0.6})
        df = db.recent_signals("AAPL")
        assert df.iloc[0]["executed"] == 0
        db.mark_signal_executed("sig-1")
        df = db.recent_signals("AAPL")
        assert df.iloc[0]["executed"] == 1

    def test_signal_id_dedupes(self, db: DatabaseManager) -> None:
        db.insert_signal("sig-x", "AAPL", "2024-06-01T13:30:00Z", "BUY", "ensemble")
        db.insert_signal("sig-x", "AAPL", "2024-06-01T13:30:00Z", "SELL", "ensemble")
        df = db.recent_signals("AAPL")
        assert len(df) == 1
        assert df.iloc[0]["signal_type"] == "SELL"  # REPLACE semantics


class TestPaperTrades:
    def test_open_close_long_pnl(self, db: DatabaseManager) -> None:
        tid = db.insert_paper_trade("AAPL", "BUY", 10, "2024-06-01T13:30:00Z", 100.0,
                                    strategy="unit-test", fees=0.5)
        pnl = db.close_paper_trade(tid, "2024-06-03T19:00:00Z", 105.0, fees=0.5)
        assert pnl == pytest.approx(10 * 5.0 - 0.5 - 0.5, abs=1e-9)
        trades = db.fetch_paper_trades(status="CLOSED")
        assert trades.iloc[0]["realized_pnl"] == pytest.approx(49.0)

    def test_open_close_short_pnl(self, db: DatabaseManager) -> None:
        tid = db.insert_paper_trade("TSLA", "SELL", 5, "2024-06-01T13:30:00Z", 200.0)
        pnl = db.close_paper_trade(tid, "2024-06-02T19:00:00Z", 190.0)
        assert pnl == pytest.approx(5 * 10.0)

    def test_close_returns_none_for_non_open(self, db: DatabaseManager) -> None:
        tid = db.insert_paper_trade("AAPL", "BUY", 1, "2024-06-01T13:30:00Z", 100.0)
        db.close_paper_trade(tid, "2024-06-01T15:00:00Z", 101.0)
        assert db.close_paper_trade(tid, "2024-06-01T16:00:00Z", 102.0) is None
        assert db.close_paper_trade(99999, "2024-06-01T16:00:00Z", 102.0) is None

    def test_open_positions_query(self, db: DatabaseManager) -> None:
        db.insert_paper_trade("AAPL", "BUY", 1, "2024-06-01T13:30:00Z", 100.0)
        db.insert_paper_trade("MSFT", "BUY", 2, "2024-06-01T13:30:00Z", 400.0)
        open_df = db.fetch_open_paper_trades()
        assert len(open_df) == 2
        assert set(open_df["symbol"]) == {"AAPL", "MSFT"}


class TestMetricsAndLogs:
    def test_performance_metrics_upsert(self, db: DatabaseManager) -> None:
        db.upsert_performance_metric("2024-06-01T00:00:00Z", 101_000.0, daily_return=0.01)
        db.upsert_performance_metric("2024-06-01T00:00:00Z", 102_000.0, daily_return=0.02)
        df = db.fetch_performance_metrics()
        assert len(df) == 1
        assert float(df.iloc[0]["portfolio_value"]) == 102_000.0

    def test_breaker_event_log(self, db: DatabaseManager) -> None:
        db.log_circuit_breaker_event("daily_loss", "latched: test", level=AlertLevel.RED,
                                     state_before="NORMAL", state_after="HALTED",
                                     details={"pct": -0.02})
        df = db.fetch_breaker_events(category="daily_loss")
        assert len(df) == 1
        assert df.iloc[0]["state_after"] == "HALTED"

    def test_automation_log(self, db: DatabaseManager) -> None:
        db.log_automation("pre_market", "fetch_data", "ok", details={"symbols": 10})
        df = db.fetch_automation_log("pre_market")
        assert df.iloc[0]["action"] == "fetch_data"

    def test_limit_breach_log(self, db: DatabaseManager) -> None:
        db.log_limit_breach("max_order_value", "order_rejected", entity="AAPL",
                            value=15_000.0, threshold=10_000.0)
        df = db.fetch_limit_breaches()
        assert df.iloc[0]["limit_type"] == "max_order_value"


class TestPatterns:
    def test_insert_and_label(self, db: DatabaseManager) -> None:
        pid = db.insert_pattern({
            "symbol": "NVDA", "timeframe": "1d", "pattern_type": "double_bottom",
            "detection_date": "2024-06-01T00:00:00Z", "detection_price": 120.0,
            "quality_score": 8.5, "volume_confirmation": 1,
            "market_regime": "bull", "sector": "Information Technology",
        })
        assert pid > 0
        df = db.fetch_patterns(unlabeled_only=True)
        assert len(df) == 1
        db.update_pattern_outcomes(pid, outcome_5d=0.03, outcome_10d=0.06,
                                   outcome_20d=0.08, outcome_magnitude=0.08,
                                   was_successful=True)
        df = db.fetch_patterns(unlabeled_only=True)
        assert df.empty
        stats = db.pattern_success_stats()
        assert stats.iloc[0]["pattern_type"] == "double_bottom"
        assert stats.iloc[0]["success_rate"] == 1.0


class TestBreakerStateAndKv:
    def test_save_load_roundtrip(self, db: DatabaseManager) -> None:
        db.save_breaker_state({
            "state": "HALTED", "active_breakers": [{"category": "daily_loss", "level": 3}],
            "day_anchor": 100_000.0, "peak_equity": 105_000.0, "day_key": "2024-06-03",
            "locked_until": "2024-06-04T13:30:00Z",
        })
        state = db.load_breaker_state()
        assert state["state"] == "HALTED"
        assert state["active_breakers"] == [{"category": "daily_loss", "level": 3}]
        assert state["day_anchor"] == 100_000.0
        assert db.load_breaker_state() == state  # repeat read

    def test_load_empty_returns_none(self, db: DatabaseManager) -> None:
        assert db.load_breaker_state() is None

    def test_replace_state(self, db: DatabaseManager) -> None:
        db.save_breaker_state({"state": "HALTED", "active_breakers": []})
        db.save_breaker_state({"state": "NORMAL", "active_breakers": []})
        assert db.load_breaker_state()["state"] == "NORMAL"
        assert db.query_scalar("SELECT COUNT(*) AS n FROM breaker_state") == 1

    def test_kv_roundtrip(self, db: DatabaseManager) -> None:
        db.kv_set("universe:sp500", {"rows": ["AAPL", "MSFT"], "fetched_at": "2024-01-01"})
        assert db.kv_get("universe:sp500")["rows"] == ["AAPL", "MSFT"]
        assert db.kv_get("missing", default=42) == 42


class TestMaintenance:
    def test_table_stats(self, db: DatabaseManager) -> None:
        db.upsert_price_bars("AAPL", "1d", _bars(3))
        stats = db.table_stats()
        assert stats["price_data"] == 3
        assert stats["news_events"] == 0

    def test_backup_creates_valid_copy(self, db: DatabaseManager, tmp_path: Path) -> None:
        db.upsert_price_bars("AAPL", "1d", _bars(2))
        dest = db.backup(tmp_path / "backup" / "copy.db")
        other = DatabaseManager(dest)
        assert other.count_price_bars("AAPL", "1d") == 2
        other.close()

    def test_export_csv(self, db: DatabaseManager, tmp_path: Path) -> None:
        db.upsert_price_bars("AAPL", "1d", _bars(2))
        dest = db.export_table_csv("price_data", tmp_path / "out.csv")
        frame = pd.read_csv(dest)
        assert len(frame) == 2

    def test_optimize_runs(self, db: DatabaseManager) -> None:
        db.optimize()

    def test_concurrent_writes(self, tmp_path: Path) -> None:
        manager = DatabaseManager(tmp_path / "threads.db")
        errors: list[Exception] = []

        def worker(n: int) -> None:
            for i in range(15):
                manager.log_automation("stress", f"w{n}", "ok", details={"i": i})

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        count = manager.query_scalar(
            "SELECT COUNT(*) AS n FROM automation_log WHERE routine = 'stress'")
        assert count == 4 * 15
        manager.close()

    def test_get_database_caches(self, tmp_path: Path) -> None:
        a = get_database(tmp_path / "cached.db", reload=True)
        b = get_database(tmp_path / "cached.db")
        assert a is b
        c = get_database(tmp_path / "cached.db", reload=True)
        assert c is not a
        c.reset_file()
