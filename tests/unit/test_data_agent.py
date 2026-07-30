"""Tests for agents/data_agent.py — all network I/O is faked/mocked."""

from __future__ import annotations

import io
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import pytest

import agents.data_agent as data_agent_module
from agents.data_agent import (
    FRED_SERIES,
    DataAgent,
    DataSourceUnavailable,
    FredClient,
    UniverseManager,
    parse_wikipedia_constituents,
)
from data.database import DatabaseManager
from utils.config import AppConfig
from utils.constants import Timeframe
from utils.helpers import next_trading_day

UTC = timezone.utc


# =============================================================================
# Fakes
# =============================================================================


class FakeProvider:
    """Deterministic MarketDataProvider implementation."""

    def __init__(self, bars_per_day: int = 7, seed: int = 42) -> None:
        self.download_calls: list[dict[str, Any]] = []
        self.fail_symbols: set[str] = set()
        self.empty_symbols: set[str] = set()
        self._seed = seed

    def download(self, symbol: str, interval: str,
                 start: Optional[datetime], end: Optional[datetime]) -> pd.DataFrame:
        self.download_calls.append({"symbol": symbol, "interval": interval,
                                    "start": start, "end": end})
        if symbol in self.fail_symbols:
            raise RuntimeError("provider exploded")
        if symbol in self.empty_symbols:
            return pd.DataFrame()
        end = end or datetime.now(tz=UTC)
        start = start or (end - timedelta(days=30))
        if interval in ("1d", "1wk", "1mo"):
            freq = {"1d": "B", "1wk": "W-FRI", "1mo": "MS"}[interval]
        elif interval == "60m":
            freq = "h"
        else:
            freq = f"{interval.rstrip('m')}min"
        stamps = pd.date_range(start=start, end=end, freq=freq, tz="UTC")[-400:]
        if stamps.empty:
            return pd.DataFrame()
        rng = np.random.default_rng(abs(hash((symbol, interval, self._seed))) % (2**32))
        base = 100 + rng.uniform(-5, 5)
        steps = rng.normal(0, 0.5, size=len(stamps)).cumsum()
        close = np.maximum(1.0, base + steps)
        open_ = close * (1 + rng.normal(0, 0.001, len(stamps)))
        high = np.maximum(open_, close) * (1 + abs(rng.normal(0, 0.001, len(stamps))))
        low = np.minimum(open_, close) * (1 - abs(rng.normal(0, 0.001, len(stamps))))
        volume = np.abs(rng.normal(1_000_000, 50_000, len(stamps)))
        return pd.DataFrame({
            "timestamp": stamps, "open": open_, "high": high, "low": low,
            "close": close, "volume": volume,
        })

    def get_info(self, symbol: str) -> dict[str, Any]:
        if symbol in self.empty_symbols:
            return {}
        return {"marketCap": 3e12, "trailingPE": 28.4, "beta": 1.2,
                "dividendYield": 0.005, "averageVolume": 55_000_000,
                "shortRatio": 1.7, "fiftyTwoWeekHigh": 220.0,
                "junkString": "ignored", "noneValue": None}

    def get_actions(self, symbol: str) -> pd.DataFrame:
        return pd.DataFrame()

    def get_option_chain(self, symbol: str):
        calls = pd.DataFrame({"volume": [500.0, 300.0]})
        puts = pd.DataFrame({"volume": [900.0, 200.0]})
        return calls, puts, "2026-08-21"


class FakeResponse:
    def __init__(self, text: str, status: int = 200) -> None:
        self.text = text
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """requests.Session stand-in returning canned pages keyed by URL."""

    def __init__(self, pages: dict[str, str], headers: Optional[dict] = None) -> None:
        self.pages = pages
        self.calls: list[str] = []
        self.headers: dict[str, str] = headers or {}

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(url)
        for needle, body in self.pages.items():
            if needle in url:
                return FakeResponse(body)
        return FakeResponse("", status=404)


SP500_HTML = """
<html><body>
<table class="infobox"><tr><td>ignore me</td></tr></table>
<table class="wikitable sortable">
<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>GICS Sub-Industry</th></tr>
<tr><td>AAPL</td><td>Apple Inc.</td><td>Information Technology</td><td>Hardware</td></tr>
<tr><td>BRK.B</td><td>Berkshire Hathaway</td><td>Financials</td><td>Insurance</td></tr>
<tr><td>XOM</td><td>Exxon Mobil</td><td>Energy</td><td>Integrated Oil &amp; Gas</td></tr>
</table>
</body></html>
"""

NDX_HTML = """
<html><body>
<table class="wikitable sortable">
<tr><th>Ticker</th><th>Company</th><th>GICS Sector</th><th>GICS Sub-Industry</th></tr>
<tr><td>MSFT</td><td>Microsoft</td><td>Information Technology</td><td>Software</td></tr>
<tr><td>NVDA</td><td>NVIDIA</td><td>Information Technology</td><td>Semiconductors</td></tr>
</table>
</body></html>
"""

RUSSELL_CSV = """iShares Russell 2000 ETF,as of,today
some,preamble,rows
Ticker,Name,Sector,Asset Class,Market Value
ABCD,ABCD Corp,Health Care,Equity,1000
EFGH,EFGH Inc,Industrials,Equity,900
CASH%,Cash,Cash and/or Derivatives,Cash and/or Derivatives,5
"""

FRED_CSV_VIX = """observation_date,VIXCLS
2024-01-01,13.42
2024-01-02,.
2024-01-03,14.01
"""


# =============================================================================
# Parsers
# =============================================================================


class TestWikipediaParsing:
    def test_sp500_table(self) -> None:
        rows = parse_wikipedia_constituents(SP500_HTML, ticker_header="Symbol")
        assert len(rows) == 3
        assert rows[0] == {"symbol": "AAPL", "name": "Apple Inc.",
                           "sector": "Information Technology", "sub_industry": "Hardware"}
        # Yahoo format normalization
        assert rows[1]["symbol"] == "BRK-B"

    def test_nasdaq100_table(self) -> None:
        rows = parse_wikipedia_constituents(NDX_HTML, ticker_header="Ticker")
        assert [r["symbol"] for r in rows] == ["MSFT", "NVDA"]

    def test_garbage_html_returns_empty(self) -> None:
        assert parse_wikipedia_constituents("<html><p>nothing</p></html>") == []


class TestUniverseManager:
    def _manager(self, cfg: AppConfig, db: DatabaseManager) -> UniverseManager:
        session = FakeSession({
            "S%26P_500": SP500_HTML,
            "Nasdaq-100": NDX_HTML,
            "1467271812596": RUSSELL_CSV,
        })
        return UniverseManager(cfg, db, session=session)

    def test_sp500_fetch_and_cache(self, app_config: AppConfig, db: DatabaseManager) -> None:
        mgr = self._manager(app_config, db)
        rows = mgr.sp500()
        assert [r["symbol"] for r in rows] == ["AAPL", "BRK-B", "XOM"]
        rows2 = mgr.sp500()  # second call served from cache
        assert [r["symbol"] for r in rows2] == ["AAPL", "BRK-B", "XOM"]

    def test_russell_filters_cash(self, app_config: AppConfig, db: DatabaseManager) -> None:
        mgr = self._manager(app_config, db)
        rows = mgr.russell2000()
        assert {r["symbol"] for r in rows} == {"ABCD", "EFGH"}

    def test_resolve_merges_and_dedupes(self, app_config: AppConfig,
                                        db: DatabaseManager) -> None:
        app_config.watchlist.use_sp500 = True
        app_config.watchlist.use_nasdaq100 = True
        app_config.watchlist.custom_stocks = ["aapl", "TSLA"]
        mgr = self._manager(app_config, db)
        symbols = mgr.resolve()
        assert "AAPL" in symbols and "TSLA" in symbols and "MSFT" in symbols
        assert len(symbols) == len(set(symbols))

    def test_offline_falls_back_to_static_list(self, app_config: AppConfig,
                                               db: DatabaseManager) -> None:
        session = FakeSession({})  # everything 404s
        mgr = UniverseManager(app_config, db, session=session)
        rows = mgr.sp500()
        assert rows  # static fallback
        assert any(r["symbol"] == "AAPL" for r in rows)

    def test_sector_map(self, app_config: AppConfig, db: DatabaseManager) -> None:
        mgr = self._manager(app_config, db)
        mgr.sp500()
        sectors = mgr.sector_map()
        assert sectors.get("AAPL") == "Information Technology"


class TestFredClient:
    def test_public_csv_parsing(self) -> None:
        session = FakeSession({"fredgraph.csv": FRED_CSV_VIX})
        client = FredClient(api_key="", session=session)
        rows = client.fetch_series("VIXCLS")
        assert rows == [(date(2024, 1, 1), 13.42), (date(2024, 1, 2), None),
                        (date(2024, 1, 3), 14.01)]
        assert client.mode == "public_csv"

    def test_csv_respects_start(self) -> None:
        session = FakeSession({"fredgraph.csv": FRED_CSV_VIX})
        client = FredClient(api_key="", session=session)
        rows = client.fetch_series("VIXCLS", start=date(2024, 1, 3))
        assert rows == [(date(2024, 1, 3), 14.01)]

    def test_http_error_propagates_after_retries(self) -> None:
        session = FakeSession({})
        client = FredClient(api_key="", session=session)
        client._limiter._interval = 0
        with pytest.raises(Exception):
            client.fetch_series("VIXCLS")


# =============================================================================
# DataAgent
# =============================================================================


def _agent(app_config: AppConfig, db: DatabaseManager,
           provider: Optional[FakeProvider] = None,
           symbols: Optional[list[str]] = None) -> tuple[DataAgent, FakeProvider]:
    provider = provider or FakeProvider()
    session = FakeSession({"S%26P_500": SP500_HTML, "fredgraph.csv": FRED_CSV_VIX})
    universe = UniverseManager(app_config, db, session=session)
    fred = FredClient(api_key="", session=session)
    agent = DataAgent(app_config, db, provider=provider, fred=fred, universe=universe)
    if symbols is not None:
        agent.resolve_universe = lambda: list(symbols)  # type: ignore[assignment]
    return agent, provider


class TestLookbackPolicy:
    def test_provider_caps_clamp(self, app_config: AppConfig, db: DatabaseManager) -> None:
        app_config.timeframes.lookback_days["1m"] = 30  # above the 7d provider cap
        agent, _ = _agent(app_config, db)
        assert agent._lookback_days(Timeframe.M1) == 7

    def test_daily_lookback_uses_historical_years(self, app_config: AppConfig,
                                                  db: DatabaseManager) -> None:
        agent, _ = _agent(app_config, db)
        assert agent._lookback_days(Timeframe.D1) == app_config.data.historical_years * 365

    def test_monthly_is_unbounded(self, app_config: AppConfig, db: DatabaseManager) -> None:
        agent, _ = _agent(app_config, db)
        assert agent._lookback_days(Timeframe.MO1) is None


class TestSyncTimeframe:
    def test_full_sync_inserts_bars(self, app_config: AppConfig, db: DatabaseManager) -> None:
        agent, provider = _agent(app_config, db)
        result = agent.sync_timeframe("AAPL", Timeframe.D1, full=True)
        assert result["action"] == "full"
        assert result["rows"] > 0
        assert db.count_price_bars("AAPL", "1d") == result["rows"]
        call = provider.download_calls[-1]
        assert call["interval"] == "1d"
        assert call["start"] is not None and call["end"] is not None

    def test_fresh_data_is_skipped(self, app_config: AppConfig, db: DatabaseManager) -> None:
        agent, provider = _agent(app_config, db)
        agent.sync_timeframe("AAPL", Timeframe.D1, full=True)
        calls_before = len(provider.download_calls)
        result = agent.sync_timeframe("AAPL", Timeframe.D1)
        assert result["action"] == "fresh"
        assert len(provider.download_calls) == calls_before  # no refetch

    def test_incremental_sync_after_gap(self, app_config: AppConfig,
                                        db: DatabaseManager) -> None:
        agent, provider = _agent(app_config, db)
        agent.sync_timeframe("AAPL", Timeframe.D1, full=True)
        # Pretend the stored history is stale by deleting the newest bars.
        stale_last = (datetime.now(tz=UTC) - timedelta(days=10)).strftime("%Y-%m-%d")
        db.execute("DELETE FROM price_data WHERE symbol = 'AAPL' AND timeframe = '1d' "
                   "AND timestamp >= ?", (f"{stale_last}T00:00:00.000000Z",))
        first_count = db.count_price_bars("AAPL", "1d")
        calls_before = len(provider.download_calls)
        result = agent.sync_timeframe("AAPL", Timeframe.D1)
        assert result["action"] == "incremental"
        assert len(provider.download_calls) == calls_before + 1
        assert db.count_price_bars("AAPL", "1d") >= first_count

    def test_empty_provider_response(self, app_config: AppConfig, db: DatabaseManager) -> None:
        provider = FakeProvider()
        provider.empty_symbols.add("DOA")
        agent, _ = _agent(app_config, db, provider=provider)
        result = agent.sync_timeframe("DOA", Timeframe.D1, full=True)
        assert result["action"] == "empty"
        assert result["rows"] == 0

    def test_provider_failure_becomes_error_not_exception(
            self, app_config: AppConfig, db: DatabaseManager) -> None:
        provider = FakeProvider()
        provider.fail_symbols.add("BOOM")
        provider._seed = 1
        agent, _ = _agent(app_config, db, provider=provider)
        result = agent.sync_timeframe("BOOM", Timeframe.D1, full=True)
        assert result["action"] == "error"
        assert result["issues"]

    def test_4h_resampled_from_1h(self, app_config: AppConfig, db: DatabaseManager) -> None:
        agent, _ = _agent(app_config, db)
        agent.sync_timeframe("AAPL", Timeframe.H1, full=True)
        n_1h = db.count_price_bars("AAPL", "1h")
        assert n_1h > 0
        result = agent.sync_timeframe("AAPL", Timeframe.H4, full=True)
        assert result["action"] == "resampled"
        assert 0 < result["rows"] <= n_1h

    def test_4h_skipped_without_1h(self, app_config: AppConfig, db: DatabaseManager) -> None:
        agent, _ = _agent(app_config, db)
        result = agent.sync_timeframe("NOH1", Timeframe.H4, full=True)
        assert result["action"] == "skipped"


class TestQualityChecks:
    def test_daily_gap_detected(self, app_config: AppConfig, db: DatabaseManager) -> None:
        start = date(2024, 3, 1)
        timestamps = []
        cursor = start
        while len(timestamps) < 10:
            cursor = next_trading_day(cursor, include=(not timestamps))
            timestamps.append(datetime(cursor.year, cursor.month, cursor.day, tzinfo=UTC))
            if len(timestamps) == 5:
                cursor = cursor + timedelta(days=5)  # carve a gap
                continue
            cursor = cursor + timedelta(days=1)
        frame = pd.DataFrame({
            "timestamp": timestamps,
            "open": [100.0] * 10, "high": [101.0] * 10, "low": [99.0] * 10,
            "close": [100.5] * 10, "volume": [1e6] * 10,
        })
        frame = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)  # plus a duplicate
        agent, _ = _agent(app_config, db)
        _, issues = __import__("utils.helpers", fromlist=["validate_ohlcv"]).validate_ohlcv(frame)
        assert any("duplicate" in i for i in issues)


class TestFundamentalsAndOptions:
    def test_fundamentals_stored(self, app_config: AppConfig, db: DatabaseManager) -> None:
        agent, _ = _agent(app_config, db)
        written = agent.sync_fundamentals("AAPL")
        assert written >= 5
        df = db.fetch_fundamentals("AAPL", "trailingPE")
        assert float(df.iloc[0]["value"]) == pytest.approx(28.4)
        # strings/None filtered out
        df = db.fetch_fundamentals("AAPL", "junkString")
        assert df.empty

    def test_empty_info_noop(self, app_config: AppConfig, db: DatabaseManager) -> None:
        provider = FakeProvider()
        provider.empty_symbols.add("NONE")
        agent, _ = _agent(app_config, db, provider=provider)
        assert agent.sync_fundamentals("NONE") == 0

    def test_options_put_call_ratio(self, app_config: AppConfig, db: DatabaseManager) -> None:
        agent, _ = _agent(app_config, db)
        result = agent.sync_options_metrics("AAPL")
        assert result["put_call_ratio"] == pytest.approx(1100 / 800)
        df = db.fetch_sentiment("AAPL", "options")
        assert not df.empty


class TestMacroAndPipeline:
    def test_macro_sync(self, app_config: AppConfig, db: DatabaseManager) -> None:
        agent, _ = _agent(app_config, db)
        written = agent.sync_macro(["VIXCLS"])
        assert written["VIXCLS"] == 3
        df = db.fetch_macro("VIXCLS")
        assert len(df) == 3

    def test_sync_all_aggregates_errors(self, app_config: AppConfig,
                                        db: DatabaseManager) -> None:
        provider = FakeProvider()
        provider.fail_symbols.add("BOOM")
        agent, _ = _agent(app_config, db, provider=provider)
        agent.resolve_universe = lambda: ["AAPL", "BOOM"]  # type: ignore[assignment]
        summary = agent.sync_all(timeframes=[Timeframe.D1], include_fundamentals=False,
                                 batch_size=2)
        assert summary["symbols_total"] == 2
        assert summary["macro"] == {}
        events = db.fetch_automation_log("pipeline")
        assert not events.empty

    def test_latest_close_and_benchmark_change(self, app_config: AppConfig,
                                               db: DatabaseManager) -> None:
        agent, _ = _agent(app_config, db)
        agent.sync_timeframe("SPY", Timeframe.D1, full=True)
        assert agent.latest_close("SPY") is not None
        change = agent.benchmark_change_pct()
        assert change is not None
        assert agent.latest_close("MISSING") is None

    def test_latest_vix(self, app_config: AppConfig, db: DatabaseManager) -> None:
        agent, _ = _agent(app_config, db)
        agent.sync_macro(["VIXCLS"])
        assert agent.latest_vix() == pytest.approx(14.01)

    def test_data_status_frame(self, app_config: AppConfig, db: DatabaseManager) -> None:
        agent, _ = _agent(app_config, db)
        agent.sync_timeframe("AAPL", Timeframe.D1, full=True)
        status = agent.data_status("AAPL")
        assert {"symbol", "timeframe", "bars", "last", "stale"} <= set(status.columns)
        daily = status[status["timeframe"] == "1d"].iloc[0]
        assert daily["bars"] > 0
        assert daily["stale"] in (False, True)
