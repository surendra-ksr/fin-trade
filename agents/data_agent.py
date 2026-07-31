"""DataAgent — all market-data ingestion for the fin-trade agent.

Responsibilities (Part 2 of the spec)
-------------------------------------
* **Universe selection** — S&P 500 / NASDAQ-100 lists parsed from Wikipedia
  with a std-lib HTML parser (no lxml dependency), Russell-2000 via the
  iShares holdings CSV, plus the custom watchlist. Lists are cached (TTL in
  config) and merged/deduplicated. Note: index memberships are *current*
  constituents — historical survivorship-bias-free membership is an explicit
  Phase 2+ concern documented in ARCHITECTURE.md.
* **Historical OHLCV** — incremental, cap-aware syncing from yfinance across
  all configured timeframes (1m -> 1mo). Intraday lookbacks are clamped to
  provider hard limits with loud logging. All series are adjusted
  OHLC (splits/dividends applied) so indicators are comparable over time.
* **Fundamentals** — valuation metrics from yfinance ``info`` snapshots
  stored point-in-time per sync date.
* **Macro** — FRED series via the official API when a key exists, otherwise
  the public fredgraph CSV endpoint (no key required).
* **Data quality** — schema validation, duplicate removal, timezone
  normalization to UTC, return-outlier detection (flag-only by default),
  zero-volume and gap reporting. Quality issues land in the automation log.

Every network call is isolated behind small client classes with retry +
rate-limiting, so the module is fully unit-testable with fake providers.
"""

from __future__ import annotations

import csv
import io
import math
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Protocol, Sequence

import numpy as np
import pandas as pd
import requests

from data.database import DatabaseManager, get_database
from utils.config import AppConfig, get_config
from utils.constants import Timeframe
from utils.helpers import (
    RateLimiter,
    chunked,
    dedupe_preserve_order,
    detect_outliers_zscore,
    ensure_directory,
    next_trading_day,
    ohlcv_from_provider,
    read_json_file,
    resample_ohlcv,
    retry,
    to_iso_z,
    utc_now,
    validate_ohlcv,
    write_json_file,
)
from utils.logger import get_logger

__all__ = [
    "DataAgent",
    "DataAgentError",
    "DataSourceUnavailable",
    "YFinanceClient",
    "FredClient",
    "UniverseManager",
    "FRED_SERIES",
    "TIMEFRAME_PROVIDER_CAPS_DAYS",
    "TIMEFRAME_DEFAULT_LOOKBACK_DAYS",
]

_log = get_logger("automation")
_log_data = get_logger("app")

UTC = timezone.utc


class DataAgentError(Exception):
    """Raised for unrecoverable data-pipeline failures."""


class DataSourceUnavailable(DataAgentError):
    """Raised when a data source is unreachable after retries."""


# =============================================================================
# Timeframe policy
# =============================================================================

#: Provider hard caps (Yahoo Finance) for intraday history, in days.
TIMEFRAME_PROVIDER_CAPS_DAYS: dict[Timeframe, Optional[int]] = {
    Timeframe.M1: 7,
    Timeframe.M5: 60,
    Timeframe.M15: 60,
    Timeframe.M30: 60,
    Timeframe.H1: 730,
    Timeframe.H4: 730,        # built locally from 60m bars
    Timeframe.D1: None,       # unbounded (use data.historical_years)
    Timeframe.W1: None,
    Timeframe.MO1: None,
}

#: Research targets from the spec (Part 2) when config has nothing to say.
TIMEFRAME_DEFAULT_LOOKBACK_DAYS: dict[Timeframe, Optional[int]] = {
    Timeframe.M1: 7,
    Timeframe.M5: 60,
    Timeframe.M15: 180,
    Timeframe.M30: 180,
    Timeframe.H1: 730,
    Timeframe.H4: 730,
    Timeframe.D1: None,       # -> data.historical_years
    Timeframe.W1: 20 * 365,   # ~20 years
    Timeframe.MO1: None,      # 'max'
}

#: yfinance info keys persisted as point-in-time fundamentals.
FUNDAMENTAL_METRICS: tuple[str, ...] = (
    "marketCap", "trailingPE", "forwardPE", "priceToBook", "priceToSalesTrailing12Months",
    "enterpriseToEbitda", "enterpriseValue", "profitMargins", "operatingMargins",
    "grossMargins", "returnOnEquity", "returnOnAssets", "debtToEquity", "currentRatio",
    "quickRatio", "totalCash", "totalDebt", "totalRevenue", "revenueGrowth",
    "earningsGrowth", "earningsQuarterlyGrowth", "trailingEps", "forwardEps",
    "dividendYield", "dividendRate", "payoutRatio", "beta", "sharesOutstanding",
    "floatShares", "heldPercentInsiders", "heldPercentInstitutions",
    "shortRatio", "shortPercentOfFloat", "averageVolume", "averageVolume10days",
    "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "fiftyDayAverage", "twoHundredDayAverage",
    "targetMeanPrice", "recommendationMean", "numberOfAnalystOpinions",
)

#: FRED macro series (Part 2 macro list) -> human description.
FRED_SERIES: dict[str, str] = {
    "FEDFUNDS": "Effective Federal Funds Rate",
    "CPIAUCSL": "Consumer Price Index (All Urban Consumers)",
    "UNRATE": "Unemployment Rate",
    "GDP": "Gross Domestic Product (quarterly, billions)",
    "A191RO1Q156NBEA": "Real GDP growth (quarterly, annualized %)",
    "DGS2": "2-Year Treasury Constant Maturity Rate",
    "DGS10": "10-Year Treasury Constant Maturity Rate",
    "DGS30": "30-Year Treasury Constant Maturity Rate",
    "T10Y2Y": "10Y-2Y Treasury Yield Spread",
    "DTWEXBGS": "Nominal Broad U.S. Dollar Index",
    "VIXCLS": "CBOE Volatility Index (VIX) Close",
}

_FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"
_FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_NDX_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
_RUT_URL = ("https://www.ishares.com/us/products/239710/ishares-russell-2000-etf/"
            "1467271812596.ajax?fileType=csv&fileName=IWM_holdings&dataType=fund")

#: Static fallback universe used only when all online sources fail AND no
#: cache exists. Deliberately small; the agent logs loudly when operating on
#: the fallback so the operator knows to investigate connectivity.
_FALLBACK_SP500: tuple[dict[str, str], ...] = tuple(
    {"symbol": sym, "name": name, "sector": sector, "sub_industry": ""}
    for sym, name, sector in (
        ("AAPL", "Apple Inc.", "Information Technology"),
        ("MSFT", "Microsoft Corp.", "Information Technology"),
        ("GOOGL", "Alphabet Inc.", "Communication Services"),
        ("AMZN", "Amazon.com Inc.", "Consumer Discretionary"),
        ("NVDA", "NVIDIA Corp.", "Information Technology"),
        ("META", "Meta Platforms Inc.", "Communication Services"),
        ("TSLA", "Tesla Inc.", "Consumer Discretionary"),
        ("NFLX", "Netflix Inc.", "Communication Services"),
        ("JPM", "JPMorgan Chase & Co.", "Financials"),
        ("XOM", "Exxon Mobil Corp.", "Energy"),
        ("UNH", "UnitedHealth Group Inc.", "Health Care"),
        ("JNJ", "Johnson & Johnson", "Health Care"),
        ("PG", "Procter & Gamble Co.", "Consumer Staples"),
        ("HD", "Home Depot Inc.", "Consumer Discretionary"),
        ("BA", "Boeing Co.", "Industrials"),
        ("SPY", "SPDR S&P 500 ETF Trust", "Benchmark"),
        ("QQQ", "Invesco QQQ Trust", "Benchmark"),
    )
)


# =============================================================================
# Provider clients (network edge — the only places requests/yfinance live)
# =============================================================================


class MarketDataProvider(Protocol):
    """Interface the DataAgent relies on (fakes in tests implement this)."""

    def download(self, symbol: str, interval: str, start: Optional[datetime],
                 end: Optional[datetime]) -> pd.DataFrame: ...

    def get_info(self, symbol: str) -> dict[str, Any]: ...

    def get_actions(self, symbol: str) -> pd.DataFrame: ...

    def get_option_chain(self, symbol: str) -> tuple[pd.DataFrame, pd.DataFrame, Optional[str]]: ...


class YFinanceClient:
    """yfinance wrapper with rate limiting, retries, and normalization."""

    def __init__(
        self,
        *,
        rate_limit_spacing: float = 0.5,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff: float = 2.0,
    ) -> None:
        self._limiter = RateLimiter(rate_limit_spacing)
        self._timeout = timeout
        self._max_retries = max(1, max_retries)
        self._backoff = backoff
        self._yf = None

    @property
    def yf(self) -> Any:
        """Lazily import yfinance so the module imports without it installed."""
        if self._yf is None:
            try:
                import yfinance as yf_module
            except ImportError as exc:  # pragma: no cover - environment guard
                raise DataSourceUnavailable(
                    "yfinance is not installed; run `pip install -r requirements.txt`"
                ) from exc
            self._yf = yf_module
        return self._yf

    def download(
        self,
        symbol: str,
        interval: str,
        start: Optional[datetime],
        end: Optional[datetime],
    ) -> pd.DataFrame:
        """Fetch OHLCV bars; returns the canonical normalized frame.

        Adjusted prices (``auto_adjust=True``) so historical indicators are
        split/dividend consistent. Returns an empty frame on no-data.
        """

        @retry(attempts=self._max_retries, base_delay=self._backoff, backoff=2.0)
        def _call() -> pd.DataFrame:
            self._limiter.wait()
            kwargs: dict[str, Any] = dict(
                tickers=symbol,
                interval=interval,
                auto_adjust=True,
                progress=False,
                threads=False,
                timeout=min(self._timeout, 60),
            )
            if start is not None:
                kwargs["start"] = start
            if end is not None:
                kwargs["end"] = end
            raw = self.yf.download(**kwargs)
            return ohlcv_from_provider(raw)

        try:
            frame = _call()
        except Exception as exc:
            raise DataSourceUnavailable(f"yfinance download failed for {symbol} {interval}: {exc}") from exc
        if frame.empty:
            _log_data.warning("yfinance returned no data for {} {}", symbol, interval)
        return frame

    def get_info(self, symbol: str) -> dict[str, Any]:
        """Fetch the yfinance ``info`` dict (empty dict on failure)."""

        @retry(attempts=self._max_retries, base_delay=self._backoff, backoff=2.0)
        def _call() -> dict[str, Any]:
            self._limiter.wait()
            info = self.yf.Ticker(symbol).get_info()
            if not isinstance(info, dict):
                return {}
            return info

        try:
            return _call()
        except Exception as exc:
            _log_data.warning("info lookup failed for {}: {}", symbol, exc)
            return {}

    def get_actions(self, symbol: str) -> pd.DataFrame:
        """Dividends/splits history (empty frame on failure)."""

        def _call() -> pd.DataFrame:
            self._limiter.wait()
            actions = self.yf.Ticker(symbol).actions
            return actions if isinstance(actions, pd.DataFrame) else pd.DataFrame()

        try:
            return _call()
        except Exception as exc:
            _log_data.warning("actions lookup failed for {}: {}", symbol, exc)
            return pd.DataFrame()

    def get_option_chain(self, symbol: str) -> tuple[pd.DataFrame, pd.DataFrame, Optional[str]]:
        """Nearest-expiry options chain as (calls, puts, expiry)."""

        def _call() -> tuple[pd.DataFrame, pd.DataFrame, Optional[str]]:
            self._limiter.wait()
            ticker = self.yf.Ticker(symbol)
            expirations = list(getattr(ticker, "options", []) or [])
            if not expirations:
                return pd.DataFrame(), pd.DataFrame(), None
            expiry = expirations[0]
            chain = ticker.option_chain(expiry)
            calls = getattr(chain, "calls", pd.DataFrame())
            puts = getattr(chain, "puts", pd.DataFrame())
            return calls, puts, expiry

        try:
            return _call()
        except Exception as exc:
            _log_data.warning("option chain lookup failed for {}: {}", symbol, exc)
            return pd.DataFrame(), pd.DataFrame(), None


class FredClient:
    """FRED macro data: official API with key, public CSV otherwise."""

    def __init__(
        self,
        *,
        api_key: str = "",
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff: float = 2.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._api_key = api_key.strip()
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = backoff
        self._session = session or requests.Session()
        self._limiter = RateLimiter(0.5)
        self.mode = "api" if self._api_key else "public_csv"

    @property
    def available(self) -> bool:
        """FRED is reachable either way; public CSV needs no credentials."""
        return True

    def fetch_series(
        self,
        series_id: str,
        start: Optional[date] = None,
    ) -> list[tuple[date, Optional[float]]]:
        """Return (date, value) observations for *series_id* ascending."""
        if self._api_key:
            try:
                return self._fetch_api(series_id, start)
            except Exception as exc:
                _log_data.warning(
                    "FRED API failed for {} ({}); falling back to public CSV", series_id, exc)
        return self._fetch_csv(series_id, start)

    @retry(attempts=3, base_delay=2.0, backoff=2.0)
    def _fetch_api(self, series_id: str, start: Optional[date]) -> list[tuple[date, Optional[float]]]:
        params: dict[str, str] = {
            "series_id": series_id,
            "api_key": self._api_key,
            "file_type": "json",
        }
        if start is not None:
            params["observation_start"] = start.isoformat()
        self._limiter.wait()
        response = self._session.get(_FRED_API_URL, params=params, timeout=self._timeout)
        response.raise_for_status()
        payload = response.json()
        rows: list[tuple[date, Optional[float]]] = []
        for obs in payload.get("observations", []):
            raw = obs.get("value", ".")
            value = None if raw in (".", "", None) else float(raw)
            rows.append((date.fromisoformat(obs["date"]), value))
        return rows

    @retry(attempts=3, base_delay=2.0, backoff=2.0)
    def _fetch_csv(self, series_id: str, start: Optional[date]) -> list[tuple[date, Optional[float]]]:
        self._limiter.wait()
        response = self._session.get(
            _FRED_CSV_URL,
            params={"id": series_id},
            timeout=self._timeout,
        )
        response.raise_for_status()
        reader = csv.DictReader(io.StringIO(response.text))
        rows = []
        for record in reader:
            obs_date = date.fromisoformat(record["observation_date"])
            if start is not None and obs_date < start:
                continue
            raw = record.get(series_id, ".")
            value = None if raw in (".", "", None) else float(raw)
            rows.append((obs_date, value))
        return rows


# =============================================================================
# Universe selection
# =============================================================================


class _WikiTableParser(HTMLParser):
    """Minimal wikitable extractor: tables -> rows -> cell texts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._in_target_table = False
        self._table_depth = 0
        self._current_rows: list[list[str]] = []
        self._current_row: Optional[list[str]] = None
        self._current_cell: Optional[list[str]] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attr = dict(attrs)
        if tag == "table":
            classes = (attr.get("class") or "").split()
            if self._in_target_table:
                self._table_depth += 1
            elif "wikitable" in classes:
                self._in_target_table = True
                self._table_depth = 1
                self._current_rows = []
            return
        if not self._in_target_table:
            return
        if tag == "tr":
            self._current_row = []
        elif tag in ("td", "th") and self._current_row is not None:
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._in_target_table:
            self._table_depth -= 1
            if self._table_depth <= 0:
                self._in_target_table = False
                if self._current_rows:
                    self.tables.append(self._current_rows)
            return
        if not self._in_target_table:
            return
        if tag in ("td", "th") and self._current_cell is not None and self._current_row is not None:
            text = " ".join("".join(self._current_cell).split())
            self._current_row.append(text)
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None:
            if self._current_row:
                self._current_rows.append(self._current_row)
            self._current_row = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)


def parse_wikipedia_constituents(html: str, ticker_header: str = "Symbol") -> list[dict[str, str]]:
    """Extract ``[{symbol, name, sector, sub_industry}]`` from a Wikipedia page.

    Works for both the S&P 500 ('Symbol') and NASDAQ-100 ('Ticker') tables.
    Symbols are normalized to Yahoo format ('BRK.B' -> 'BRK-B').
    """
    parser = _WikiTableParser()
    parser.feed(html)
    for table in parser.tables:
        if not table:
            continue
        header = [cell.strip() for cell in table[0]]
        normalized = [h.lower() for h in header]
        if ticker_header.lower() not in normalized:
            continue
        idx_symbol = normalized.index(ticker_header.lower())
        idx_name = next((i for i, h in enumerate(normalized)
                         if h in ("security", "company")), 1)
        idx_sector = next((i for i, h in enumerate(normalized) if "sector" in h), -1)
        idx_sub = next((i for i, h in enumerate(normalized) if "sub-industry" in h), -1)
        rows: list[dict[str, str]] = []
        for raw_row in table[1:]:
            if len(raw_row) <= idx_symbol:
                continue
            symbol = raw_row[idx_symbol].strip().replace(".", "-")
            if not symbol:
                continue
            rows.append({
                "symbol": symbol,
                "name": raw_row[idx_name].strip() if len(raw_row) > idx_name else "",
                "sector": raw_row[idx_sector].strip() if 0 <= idx_sector < len(raw_row) else "",
                "sub_industry": raw_row[idx_sub].strip() if 0 <= idx_sub < len(raw_row) else "",
            })
        if rows:
            return rows
    return []


class UniverseManager:
    """Fetches and caches index memberships; merges them with custom lists."""

    def __init__(
        self,
        config: AppConfig,
        db: DatabaseManager,
        *,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._cfg = config
        self._db = db
        self._session = session or requests.Session()
        self._session.headers.update({
            "User-Agent": config.api_keys.sec_edgar_user_agent or "fin-trade research agent",
            "Accept-Language": "en-US,en;q=0.9",
        })
        cache_dir = ensure_directory(config.resolve_path(config.data.alternative_dir))
        self._cache_dir = Path(cache_dir)
        self._limiter = RateLimiter(max(config.data.rate_limit_spacing_seconds, 0.25))

    # ------------------------------------------------------------------
    def _fetch_page(self, url: str) -> str:
        @retry(attempts=self._cfg.data.max_retries,
               base_delay=self._cfg.data.retry_backoff_seconds, backoff=2.0)
        def _call() -> str:
            self._limiter.wait()
            response = self._session.get(url, timeout=self._cfg.data.request_timeout_seconds)
            response.raise_for_status()
            return response.text

        return _call()

    def _cached_or_fetch(
        self,
        name: str,
        fetch_fn: Any,
        ticker_header: str,
    ) -> list[dict[str, str]]:
        cache_key = f"universe:{name}"
        cached = self._db.kv_get(cache_key)
        ttl_seconds = self._cfg.data.universe_cache_hours * 3600
        if isinstance(cached, dict):
            age = utc_now() - pd.to_datetime(cached.get("fetched_at"), utc=True).to_pydatetime()
            if age.total_seconds() < ttl_seconds and cached.get("rows"):
                _log_data.debug("using cached {} universe ({} rows)", name, len(cached["rows"]))
                return list(cached["rows"])
        rows: list[dict[str, str]] = []
        try:
            html = self._fetch_page(self._url_for(name))
            rows = parse_wikipedia_constituents(html, ticker_header=ticker_header) \
                if name != "russell2000" else self._parse_russell_csv(html)
            if not rows:
                raise DataSourceUnavailable(f"parsed 0 rows for {name}")
        except Exception as exc:
            _log_data.warning("{} universe fetch failed: {}", name, exc)
            file_rows = read_json_file(self._cache_dir / f"{name}.json", default=[])
            if file_rows:
                _log_data.info("using on-disk cache for {} universe", name)
                rows = file_rows
            elif isinstance(cached, dict) and cached.get("rows"):
                rows = list(cached["rows"])
        if rows:
            write_json_file(self._cache_dir / f"{name}.json", rows)
            self._db.kv_set(cache_key, {"fetched_at": to_iso_z(utc_now()), "rows": rows})
        return rows

    @staticmethod
    def _url_for(name: str) -> str:
        return {"sp500": _SP500_URL, "nasdaq100": _NDX_URL, "russell2000": _RUT_URL}[name]

    @staticmethod
    def _parse_russell_csv(text: str) -> list[dict[str, str]]:
        """Parse the iShares holdings CSV (metadata preamble + ticker rows)."""
        lines = text.splitlines()
        rows: list[dict[str, str]] = []
        header_idx: Optional[int] = None
        for i, line in enumerate(lines):
            if line.lower().startswith("ticker,"):
                header_idx = i
                break
        if header_idx is None:
            return rows
        reader = csv.DictReader(lines[header_idx:])
        for record in reader:
            ticker = (record.get("Ticker") or "").strip().replace(".", "-")
            sector = (record.get("Sector") or "").strip()
            asset_class = (record.get("Asset Class") or "").strip().lower()
            if not ticker or "equity" not in asset_class:
                continue
            rows.append({
                "symbol": ticker,
                "name": (record.get("Name") or "").strip(),
                "sector": sector,
                "sub_industry": "",
            })
        return rows

    # ------------------------------------------------------------------
    def sp500(self) -> list[dict[str, str]]:
        rows = self._cached_or_fetch("sp500", self._fetch_page, "Symbol")
        if not rows:
            _log_data.warning("ALL S&P 500 sources failed; using built-in fallback list")
            rows = list(_FALLBACK_SP500)
        return rows

    def nasdaq100(self) -> list[dict[str, str]]:
        rows = self._cached_or_fetch("nasdaq100", self._fetch_page, "Ticker")
        if not rows:
            _log_data.warning("NASDAQ-100 fetch failed and no cache exists; skipping universe")
        return rows

    def russell2000(self) -> list[dict[str, str]]:
        return self._cached_or_fetch("russell2000", self._fetch_page, "Ticker")

    def resolve(self) -> list[str]:
        """Final symbol list per watchlist config (deduped, sorted by source)."""
        wl = self._cfg.watchlist
        symbols: list[str] = list(wl.custom_stocks)
        if wl.use_sp500:
            symbols.extend(row["symbol"] for row in self.sp500())
        if wl.use_nasdaq100:
            symbols.extend(row["symbol"] for row in self.nasdaq100())
        if wl.use_russell2000:
            symbols.extend(row["symbol"] for row in self.russell2000())
        resolved = dedupe_preserve_order(s.strip().upper() for s in symbols if s and s.strip())
        _log.info("universe resolved to {} symbols", len(resolved))
        return resolved

    def sector_map(self) -> dict[str, str]:
        """symbol -> sector mapping built from cached universes (best effort)."""
        mapping: dict[str, str] = {}
        for name in ("sp500", "nasdaq100", "russell2000"):
            cached = self._db.kv_get(f"universe:{name}")
            if isinstance(cached, dict):
                for row in cached.get("rows", []):
                    sym = str(row.get("symbol", "")).upper()
                    sector = str(row.get("sector", ""))
                    if sym and sector and sym not in mapping:
                        mapping[sym] = sector
        return mapping


# =============================================================================
# DataAgent
# =============================================================================


@dataclass
class SyncReport:
    """Outcome bookkeeping for a sync run (per symbol, per timeframe)."""

    symbol: str
    timeframe_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    fundamentals_rows: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframes": self.timeframe_results,
            "fundamentals_rows": self.fundamentals_rows,
            "errors": self.errors,
            "ok": self.ok,
        }


class DataAgent:
    """Coordinates all market-data ingestion into the local database."""

    def __init__(
        self,
        config: Optional[AppConfig] = None,
        db: Optional[DatabaseManager] = None,
        *,
        provider: Optional[MarketDataProvider] = None,
        fred: Optional[FredClient] = None,
        universe: Optional[UniverseManager] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._cfg = config or get_config()
        self._db = db or get_database(self._cfg.resolve_path(self._cfg.data.database_path))
        dcfg = self._cfg.data
        self._provider: MarketDataProvider = provider or YFinanceClient(
            rate_limit_spacing=dcfg.rate_limit_spacing_seconds,
            timeout=dcfg.request_timeout_seconds,
            max_retries=dcfg.max_retries,
            backoff=dcfg.retry_backoff_seconds,
        )
        self._fred = fred or FredClient(
            api_key=self._cfg.api_keys.fred,
            timeout=dcfg.request_timeout_seconds,
            max_retries=dcfg.max_retries,
            backoff=dcfg.retry_backoff_seconds,
            session=session,
        )
        self._universe = universe or UniverseManager(self._cfg, self._db, session=session)
        self._log = get_logger("automation", agent="data")

    # ------------------------------------------------------------------
    # Universe
    # ------------------------------------------------------------------
    def resolve_universe(self) -> list[str]:
        """Symbols to work with, for the current watchlist configuration."""
        return self._universe.resolve()

    # ------------------------------------------------------------------
    # OHLCV sync
    # ------------------------------------------------------------------
    def _lookback_days(self, tf: Timeframe) -> Optional[int]:
        """Config lookback (clamped to provider cap) for *tf*."""
        configured = self._cfg.timeframes.lookback_days.get(tf.value)
        if configured is None:
            configured = TIMEFRAME_DEFAULT_LOOKBACK_DAYS.get(tf)
        if configured is None and tf is Timeframe.D1:
            configured = self._cfg.data.historical_years * 365
        cap = TIMEFRAME_PROVIDER_CAPS_DAYS.get(tf)
        if configured is not None and cap is not None and configured > cap:
            self._log.warning(
                "lookback for {} is {}d but provider cap is {}d; clamping",
                tf.value, configured, cap)
            configured = cap
        return configured

    def _fetch_window(self, tf: Timeframe, full: bool) -> tuple[Optional[datetime], datetime]:
        """(start, end) for the next download of *tf*; start=None means 'max'."""
        end = utc_now()
        lookback = self._lookback_days(tf)
        if full or lookback is None:
            return (None if lookback is None else end - timedelta(days=lookback)), end
        return end - timedelta(days=lookback), end

    def sync_timeframe(self, symbol: str, tf: Timeframe, *, full: bool = False) -> dict[str, Any]:
        """Incremental + capped OHLCV sync for one (symbol, timeframe).

        4h bars are derived from existing 1h bars instead of being downloaded
        (Yahoo has no 4h interval). Returns a per-timeframe result dict.
        """
        symbol = symbol.upper()
        result: dict[str, Any] = {"action": "noop", "rows": 0, "issues": []}

        if tf is Timeframe.H4:
            one_hour = self._db.fetch_price_bars(symbol, Timeframe.H1.value)
            if one_hour.empty:
                result["action"] = "skipped"
                result["issues"].append("no 1h bars available to resample to 4h")
                return result
            frame, issues = validate_ohlcv(resample_ohlcv(one_hour, Timeframe.H4.pandas_freq))
            result["issues"].extend(issues)
            if frame.empty:
                result["action"] = "empty"
                return result
            result["rows"] = self._db.upsert_price_bars(symbol, tf.value, frame)
            result["action"] = "resampled"
            return result

        last_ts = self._db.get_last_bar_timestamp(symbol, tf.value)
        start, end = self._fetch_window(tf, full or last_ts is None)
        action = "full" if (full or last_ts is None) else "incremental"

        if last_ts is not None and start is not None:
            last_dt = pd.to_datetime(last_ts, utc=True).to_pydatetime()
            # Skip work when the newest bar is fresher than one bar-width.
            age = (utc_now() - last_dt).total_seconds()
            if age < tf.seconds:
                result["action"] = "fresh"
                return result
            # Re-fetch the boundary bar to heal partial/corrected data.
            start = max(last_dt - timedelta(seconds=tf.seconds), start)
            action = "incremental"

        try:
            raw = self._provider.download(symbol, tf.yf_interval, start, end)
        except Exception as exc:
            # Any provider-level failure (network, parsing, rate limit after
            # retries) must degrade to a recorded error, never crash the loop.
            result["action"] = "error"
            result["issues"].append(str(exc))
            self._log.error("{} {} sync failed: {}", symbol, tf.value, exc)
            return result

        if raw.empty:
            result["action"] = "empty"
            return result

        frame, issues = validate_ohlcv(raw, min_rows=1)
        result["issues"].extend(self._quality_issues(symbol, tf, frame) + issues)
        written = self._db.upsert_price_bars(symbol, tf.value, frame)
        result["rows"] = written
        result["action"] = action if result["action"] == "noop" or action == "full" else action
        self._log.debug("{} {}: {} bars ({})", symbol, tf.value, written, result["action"])
        return result

    def _quality_issues(self, symbol: str, tf: Timeframe, frame: pd.DataFrame) -> list[str]:
        """Quality scan: return outliers, zero-volume, daily gaps."""
        issues: list[str] = []
        if frame.empty:
            return issues
        closes = frame["close"].astype(float)
        returns = closes.pct_change().dropna()
        if len(returns) >= 20:
            outliers = detect_outliers_zscore(returns, threshold=self._cfg.data.outlier_zscore)
            n_outliers = int(outliers.sum())
            if n_outliers:
                sample_dates = [to_iso_z(d) for d in returns.index[outliers].tolist()[:5]]
                issues.append(
                    f"{n_outliers} return outlier(s) beyond z={self._cfg.data.outlier_zscore} "
                    f"(first: {sample_dates})")
        zero_vol = int((frame["volume"] <= 0).sum()) if "volume" in frame else 0
        if zero_vol:
            issues.append(f"{zero_vol} zero-volume bar(s)")
        if tf is Timeframe.D1 and len(frame) >= 5:
            stamps = pd.to_datetime(frame["timestamp"], utc=True)
            expected = 0
            cursor = stamps.iloc[0].date()
            last = stamps.iloc[-1].date()
            while cursor <= last:
                cursor = next_trading_day(cursor, include=True)
                if cursor > last:
                    break
                expected += 1
                cursor = next_trading_day(cursor)
            actual = stamps.dt.normalize().nunique()
            if expected and actual < expected:
                issues.append(f"{expected - actual} missing daily bar(s) vs NYSE calendar")
        return issues

    def sync_symbol(
        self,
        symbol: str,
        *,
        timeframes: Optional[Sequence[str | Timeframe]] = None,
        full: bool = False,
        include_fundamentals: bool = True,
    ) -> SyncReport:
        """Sync one symbol across timeframes (+ fundamentals by default)."""
        tfs: list[Timeframe] = []
        for item in timeframes or self._cfg.timeframes.all_timeframes:
            tfs.append(item if isinstance(item, Timeframe) else Timeframe(str(item)))
        report = SyncReport(symbol=symbol.upper())
        for tf in dedupe_preserve_order(tfs):
            try:
                report.timeframe_results[tf.value] = self.sync_timeframe(symbol, tf, full=full)
            except Exception as exc:  # defense in depth: never kill the loop
                report.errors.append(f"{tf.value}: {exc}")
                self._log.error("{} {} unexpected sync error: {}", symbol, tf.value, exc)
        if include_fundamentals:
            report.fundamentals_rows = self.sync_fundamentals(symbol)
        return report

    # ------------------------------------------------------------------
    # Fundamentals
    # ------------------------------------------------------------------
    def sync_fundamentals(self, symbol: str) -> int:
        """Persist a point-in-time fundamental snapshot for *symbol*."""
        info = self._provider.get_info(symbol.upper())
        if not info:
            return 0
        metrics: dict[str, Optional[float]] = {}
        for key in FUNDAMENTAL_METRICS:
            value = info.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if value is None or (isinstance(value, float) and math.isnan(value)):
                    continue
                metrics[key] = float(value)
        if not metrics:
            return 0
        written = self._db.upsert_fundamentals(
            symbol.upper(), utc_now(), metrics, period="point", source="yfinance")
        self._log.debug("{}: {} fundamental metrics stored", symbol.upper(), written)
        return written

    # ------------------------------------------------------------------
    # Options sentiment extras (put/call ratio)
    # ------------------------------------------------------------------
    def sync_options_metrics(self, symbol: str) -> dict[str, Any]:
        """Put/call volume ratio from the nearest expiry chain (best effort)."""
        calls, puts, expiry = self._provider.get_option_chain(symbol.upper())
        result: dict[str, Any] = {"expiry": expiry, "put_call_ratio": None}
        if calls.empty or puts.empty:
            return result
        call_vol = float(calls.get("volume", pd.Series(dtype=float)).fillna(0).sum())
        put_vol = float(puts.get("volume", pd.Series(dtype=float)).fillna(0).sum())
        if call_vol > 0:
            ratio = put_vol / call_vol
            result["put_call_ratio"] = ratio
            result["call_volume"] = call_vol
            result["put_volume"] = put_vol
            self._db.upsert_sentiment(symbol.upper(), utc_now(), "options",
                                      score=None, volume=int(call_vol + put_vol),
                                      payload={"put_call_ratio": ratio, "expiry": expiry})
        return result

    # ------------------------------------------------------------------
    # Extended providers (credential-gated and injectable for tests)
    # ------------------------------------------------------------------
    def fetch_sec_company_facts(self, cik: str) -> dict[str, Any]:
        """Fetch SEC company facts with an explicit descriptive user agent."""
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json"
        response = requests.get(url, headers={"User-Agent": "fin-trade research contact@example.invalid"}, timeout=self._cfg.data.request_timeout_seconds)
        response.raise_for_status(); return response.json()

    def fetch_alpha_vantage_fundamentals(self, symbol: str) -> dict[str, Any]:
        """Fetch Alpha Vantage overview only when its key is configured."""
        key = os.getenv("ALPHAVANTAGE_API_KEY")
        if not key:
            self._log.warning("Alpha Vantage skipped: ALPHAVANTAGE_API_KEY is not set")
            return {}
        response = requests.get("https://www.alphavantage.co/query", params={"function":"OVERVIEW","symbol":symbol.upper(),"apikey":key}, timeout=self._cfg.data.request_timeout_seconds)
        response.raise_for_status(); return response.json()

    def options_iv_surface(self, symbol: str) -> pd.DataFrame:
        """Normalize provider option chains into an expiry/strike/IV surface."""
        calls, puts, expiry = self._provider.get_option_chain(symbol.upper()); frames=[]
        for side, chain in (("call",calls),("put",puts)):
            if chain.empty: continue
            z=chain.copy(); z["side"]=side; z["expiry"]=expiry; z["symbol"]=symbol.upper(); frames.append(z)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["symbol","expiry","side","strike","impliedVolatility"])

    def ingest_news(self, items: Iterable[Mapping[str, Any]]) -> int:
        """Insert normalized news records and rely on DB natural-key deduplication."""
        count=0
        for item in items:
            count += bool(self._db.insert_news(item["symbol"], item["published_at"], item["headline"], content=item.get("content"), url=item.get("url"), source=item.get("source"), sentiment_score=item.get("sentiment_score"), event_type=item.get("event_type")))
        return count

    # ------------------------------------------------------------------
    # Macro
    # ------------------------------------------------------------------
    def sync_macro(self, series: Optional[Iterable[str]] = None) -> dict[str, int]:
        """Sync configured FRED series into macro_data."""
        written: dict[str, int] = {}
        years = self._cfg.data.historical_years
        start = date(utc_now().year - years, 1, 1)
        for series_id in series or FRED_SERIES:
            try:
                rows = self._fred.fetch_series(series_id, start=start)
                written[series_id] = self._db.upsert_macro(series_id, rows, source="FRED")
            except Exception as exc:
                written[series_id] = 0
                self._log.error("macro sync failed for {}: {}", series_id, exc)
        self._db.log_automation("nightly", "sync_macro", "ok",
                                details={"series": written, "mode": self._fred.mode})
        return written

    # ------------------------------------------------------------------
    # Bulk pipeline entry points
    # ------------------------------------------------------------------
    def sync_all(
        self,
        symbols: Optional[Sequence[str]] = None,
        *,
        timeframes: Optional[Sequence[str | Timeframe]] = None,
        full: bool = False,
        include_macro: bool = False,
        include_fundamentals: bool = True,
        batch_size: int = 25,
    ) -> dict[str, Any]:
        """Sync the whole watchlist, batched, never raising on symbol errors."""
        universe = list(symbols) if symbols is not None else self.resolve_universe()
        self._log.info("data sync starting: {} symbols, full={}", len(universe), full)
        started = utc_now()
        reports: list[SyncReport] = []
        for batch in chunked(universe, batch_size):
            for symbol in batch:
                reports.append(self.sync_symbol(
                    symbol, timeframes=timeframes, full=full,
                    include_fundamentals=include_fundamentals))
            self._log.info("data sync progress: {}/{} symbols", len(reports), len(universe))
        macro_written = self.sync_macro() if include_macro else {}
        errors = [f"{r.symbol}: {err}" for r in reports for err in r.errors]
        summary = {
            "symbols_total": len(universe),
            "symbols_ok": sum(1 for r in reports if r.ok),
            "errors": errors,
            "macro": macro_written,
            "started_at": to_iso_z(started),
            "finished_at": to_iso_z(utc_now()),
            "duration_seconds": (utc_now() - started).total_seconds(),
        }
        self._db.log_automation(
            "pipeline", "sync_all", "ok" if not errors else "partial",
            details={**summary, "errors": errors[:20]})
        self._log.info(
            "data sync finished: {}/{} ok, {} error(s), {:.1f}s",
            summary["symbols_ok"], summary["symbols_total"], len(errors),
            summary["duration_seconds"])
        return summary

    # ------------------------------------------------------------------
    # Status & convenience reads
    # ------------------------------------------------------------------
    def data_status(self, symbol: Optional[str] = None) -> pd.DataFrame:
        """Freshness report per (symbol, timeframe)."""
        symbols = [symbol.upper()] if symbol else self._db.list_price_symbols()
        rows: list[dict[str, Any]] = []
        now = utc_now()
        for sym in symbols:
            for tf in Timeframe:
                last = self._db.get_last_bar_timestamp(sym, tf.value)
                count = self._db.count_price_bars(sym, tf.value)
                stale: Optional[bool] = None
                age_seconds: Optional[float] = None
                if last is not None:
                    age_seconds = (now - pd.to_datetime(last, utc=True).to_pydatetime()).total_seconds()
                    stale = age_seconds > tf.seconds * 3
                rows.append({
                    "symbol": sym, "timeframe": tf.value, "bars": count,
                    "first": self._db.get_first_bar_timestamp(sym, tf.value),
                    "last": last, "age_seconds": age_seconds, "stale": stale,
                })
        return pd.DataFrame(rows)

    def latest_close(self, symbol: str) -> Optional[float]:
        """Most recent stored daily close (None when unavailable)."""
        df = self._db.fetch_price_bars(symbol.upper(), Timeframe.D1.value, limit=1)
        if df.empty:
            return None
        return float(df.iloc[-1]["close"])

    def benchmark_change_pct(self, symbol: Optional[str] = None) -> Optional[float]:
        """Last daily % change of the crash-detector benchmark (default SPY)."""
        benchmark = symbol or self._cfg.circuit_breakers.market_crash.benchmark_symbol
        df = self._db.fetch_price_bars(benchmark.upper(), Timeframe.D1.value, limit=2)
        if len(df) < 2:
            return None
        prev, last = float(df.iloc[-2]["close"]), float(df.iloc[-1]["close"])
        if prev <= 0:
            return None
        return last / prev - 1.0

    def sector_of(self, symbol: str) -> Optional[str]:
        """Best-effort sector lookup from cached universes."""
        return self._universe.sector_map().get(symbol.upper())

    def latest_vix(self) -> Optional[float]:
        """Latest stored VIXCLS macro value (pre-feeds the VIX breakers)."""
        df = self._db.fetch_macro("VIXCLS")
        if df.empty:
            return None
        values = df["value"].dropna()
        if values.empty:
            return None
        return float(values.iloc[-1])
