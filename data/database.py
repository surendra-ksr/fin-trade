"""SQLite persistence layer for the fin-trade agent.

Design decisions
----------------
* **stdlib ``sqlite3``** — zero ORM dependency; SQL is explicit and auditable.
* **WAL journal + ``synchronous=NORMAL``** — fast concurrent reads, safe writes.
* **Thread-local connections + a writer lock** — safe for the scheduler
  threads and the watchdog running side-by-side.
* **ISO-8601 UTC text timestamps** (`...Z`) — lexicographic ordering equals
  chronological ordering, so indexes on ``timestamp`` Just Work.
* **Versioned migrations** in ``schema_migrations``; new tables/columns are
  added by appending to :data:`MIGRATIONS`, never by editing old entries.
* Tables follow Part 2 (data schema), Part 6 (pattern library) and the
  circuit-breaker persistence needs of Part 9 of the master spec.

The manager is deliberately boring: predictable SQL, exhaustive logging,
and typed, documented CRUD methods used by every agent.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Optional, Sequence

import pandas as pd

from utils.constants import DEFAULT_DATABASE_PATH, AlertLevel
from utils.helpers import ensure_directory, to_iso_z
from utils.logger import get_logger

__all__ = ["DatabaseManager", "get_database", "DatabaseError"]

_log = get_logger("app")


class DatabaseError(Exception):
    """Raised for unrecoverable database failures."""


# =============================================================================
# Schema (migration version 1)
# =============================================================================

_SCHEMA_V1: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS price_data (
        symbol      TEXT NOT NULL,
        timeframe   TEXT NOT NULL,
        timestamp   TEXT NOT NULL,
        open        REAL NOT NULL,
        high        REAL NOT NULL,
        low         REAL NOT NULL,
        close       REAL NOT NULL,
        volume      REAL NOT NULL DEFAULT 0,
        adj_close   REAL,
        source      TEXT NOT NULL DEFAULT 'yfinance',
        inserted_at TEXT NOT NULL,
        PRIMARY KEY (symbol, timeframe, timestamp)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_price_data_tf_ts ON price_data(timeframe, timestamp)",
    """
    CREATE TABLE IF NOT EXISTS fundamental_data (
        symbol      TEXT NOT NULL,
        date        TEXT NOT NULL,
        metric      TEXT NOT NULL,
        value       REAL,
        period      TEXT NOT NULL DEFAULT 'point',
        source      TEXT NOT NULL DEFAULT 'yfinance',
        inserted_at TEXT NOT NULL,
        PRIMARY KEY (symbol, date, metric, period, source)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS macro_data (
        date        TEXT NOT NULL,
        indicator   TEXT NOT NULL,
        value       REAL,
        source      TEXT NOT NULL DEFAULT 'FRED',
        inserted_at TEXT NOT NULL,
        PRIMARY KEY (date, indicator)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sentiment_data (
        symbol      TEXT NOT NULL,
        date        TEXT NOT NULL,
        source      TEXT NOT NULL,
        score       REAL,
        volume      INTEGER,
        payload     TEXT,
        inserted_at TEXT NOT NULL,
        PRIMARY KEY (symbol, date, source)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS news_events (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol          TEXT NOT NULL,
        published_at    TEXT NOT NULL,
        headline        TEXT NOT NULL,
        content         TEXT,
        url             TEXT,
        source          TEXT,
        sentiment_score REAL,
        event_type      TEXT,
        ingested_at     TEXT NOT NULL,
        UNIQUE (symbol, published_at, headline)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_news_symbol_ts ON news_events(symbol, published_at)",
    """
    CREATE TABLE IF NOT EXISTS trade_signals (
        id           TEXT PRIMARY KEY,
        symbol       TEXT NOT NULL,
        timestamp    TEXT NOT NULL,
        signal_type  TEXT NOT NULL,
        score        REAL,
        confidence   REAL,
        model_source TEXT NOT NULL,
        timeframe    TEXT,
        price        REAL,
        rationale    TEXT,
        executed     INTEGER NOT NULL DEFAULT 0,
        created_at   TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_signals_symbol_ts ON trade_signals(symbol, timestamp)",
    """
    CREATE TABLE IF NOT EXISTS paper_trades (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        portfolio_id  TEXT NOT NULL DEFAULT 'default',
        symbol        TEXT NOT NULL,
        side          TEXT NOT NULL,
        quantity      REAL NOT NULL,
        entry_time    TEXT NOT NULL,
        entry_price   REAL NOT NULL,
        exit_time     TEXT,
        exit_price    REAL,
        status        TEXT NOT NULL DEFAULT 'OPEN',
        realized_pnl  REAL,
        fees          REAL NOT NULL DEFAULT 0,
        slippage_cost REAL NOT NULL DEFAULT 0,
        strategy      TEXT,
        signal_id     TEXT,
        meta          TEXT,
        inserted_at   TEXT NOT NULL,
        updated_at    TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_paper_trades_pf_status ON paper_trades(portfolio_id, status)",
    """
    CREATE TABLE IF NOT EXISTS live_trades (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol          TEXT NOT NULL,
        side            TEXT NOT NULL,
        quantity        REAL NOT NULL,
        entry_time      TEXT NOT NULL,
        entry_price     REAL NOT NULL,
        exit_time       TEXT,
        exit_price      REAL,
        status          TEXT NOT NULL DEFAULT 'OPEN',
        realized_pnl    REAL,
        fees            REAL NOT NULL DEFAULT 0,
        strategy        TEXT,
        signal_id       TEXT,
        client_order_id TEXT UNIQUE,
        broker_order_id TEXT,
        meta            TEXT,
        inserted_at     TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS performance_metrics (
        date              TEXT NOT NULL,
        portfolio_id      TEXT NOT NULL DEFAULT 'default',
        portfolio_value   REAL NOT NULL,
        cash              REAL,
        invested_value    REAL,
        daily_return      REAL,
        cumulative_return REAL,
        drawdown          REAL,
        sharpe            REAL,
        payload           TEXT,
        created_at        TEXT NOT NULL,
        PRIMARY KEY (date, portfolio_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS circuit_breaker_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp    TEXT NOT NULL,
        category     TEXT NOT NULL,
        level        INTEGER NOT NULL,
        state_before TEXT,
        state_after  TEXT,
        trigger_type TEXT,
        action_taken TEXT NOT NULL,
        details      TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cbl_ts ON circuit_breaker_log(timestamp)",
    """
    CREATE TABLE IF NOT EXISTS automation_log (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        routine   TEXT NOT NULL,
        action    TEXT NOT NULL,
        result    TEXT NOT NULL,
        details   TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS limit_breach_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp  TEXT NOT NULL,
        limit_type TEXT NOT NULL,
        entity     TEXT,
        value      REAL,
        threshold  REAL,
        action     TEXT NOT NULL,
        details    TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS patterns_detected (
        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol                 TEXT NOT NULL,
        timeframe              TEXT NOT NULL,
        pattern_type           TEXT NOT NULL,
        detection_date         TEXT NOT NULL,
        detection_price        REAL,
        pattern_start_date     TEXT,
        pattern_end_date       TEXT,
        quality_score          REAL,
        volume_confirmation    INTEGER,
        market_regime          TEXT,
        sector                 TEXT,
        vix_level              REAL,
        outcome_5d             REAL,
        outcome_10d            REAL,
        outcome_20d            REAL,
        outcome_magnitude      REAL,
        was_successful         INTEGER,
        sentiment_at_detection REAL,
        confirming_indicators  TEXT,
        created_at             TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_patterns_symbol_date ON patterns_detected(symbol, detection_date)",
    "CREATE INDEX IF NOT EXISTS idx_patterns_type ON patterns_detected(pattern_type)",
    """
    CREATE TABLE IF NOT EXISTS breaker_state (
        id               INTEGER PRIMARY KEY CHECK (id = 1),
        state            TEXT NOT NULL,
        active_breakers  TEXT NOT NULL DEFAULT '[]',
        day_anchor       REAL,
        week_anchor      REAL,
        month_anchor     REAL,
        peak_equity      REAL,
        day_key          TEXT,
        week_key         TEXT,
        month_key        TEXT,
        locked_until     TEXT,
        recovery_start   TEXT,
        recovery_anchor  REAL,
        notes            TEXT,
        updated_at       TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS system_state (
        key        TEXT PRIMARY KEY,
        value      TEXT,
        updated_at TEXT NOT NULL
    )
    """,
)

#: Append-only migration registry: (version, description, statements).
MIGRATIONS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (1, "initial schema (price/fundamental/macro/sentiment/news/signals/"
        "trades/metrics/circuit-breaker/automation/limit-breach/patterns/"
        "breaker-state/system-state)", _SCHEMA_V1),
    # ------------------------------------------------------------------
    # Phase 13 optimisation indices (2026-07-31)
    # ------------------------------------------------------------------
    (2, "Phase 13: additional hot-query indices on price_data",
     (
         # Index for GROUP BY symbol, MAX(timestamp) — the live-trading
         # "latest bar per symbol" hot path.
         "CREATE INDEX IF NOT EXISTS idx_price_data_sym_ts ON price_data(symbol, timestamp)",
         # Covering index for the "all symbols for a timeframe" feature-
         # engineering query (timeframe → symbol → timestamp).
         "CREATE INDEX IF NOT EXISTS idx_price_data_tf_sym_ts ON price_data(timeframe, symbol, timestamp)",
     )),
)


# =============================================================================
# Database manager
# =============================================================================


class DatabaseManager:
    """Thread-safe SQLite manager exposing typed CRUD for every table.

    Args:
        path: database file path (parent directories are created).
              ``":memory:"`` is supported for tests (single-threaded use).
        busy_timeout_ms: milliseconds SQLite waits on a locked database.
    """

    def __init__(self, path: str | Path = DEFAULT_DATABASE_PATH, busy_timeout_ms: int = 5000) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            ensure_directory(Path(self.path).parent)
        self._busy_timeout_ms = int(busy_timeout_ms)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self.migrate()
        _log.debug("DatabaseManager ready at {}", self.path)

    # ------------------------------------------------------------------
    # Connection plumbing
    # ------------------------------------------------------------------
    @property
    def connection(self) -> sqlite3.Connection:
        """Per-thread shared connection (created on first use)."""
        conn: Optional[sqlite3.Connection] = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=self._busy_timeout_ms / 1000.0,
                                   check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            if self.path != ":memory:":
                conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        """Close this thread's connection (other threads keep theirs)."""
        conn: Optional[sqlite3.Connection] = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error as exc:
                _log.warning("error while closing database connection: {}", exc)
            self._local.conn = None

    def __enter__(self) -> "DatabaseManager":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        self.close()
        return False

    def _locked(self, fn: Any) -> Any:
        """Run *fn* under the writer lock with 'database is locked' retries."""
        attempts = 3
        for attempt in range(1, attempts + 1):
            with self._write_lock:
                try:
                    return fn()
                except sqlite3.OperationalError as exc:
                    if "locked" in str(exc).lower() and attempt < attempts:
                        _log.warning("database locked (attempt {}/{}); retrying", attempt, attempts)
                        time.sleep(0.05 * attempt)
                        continue
                    raise DatabaseError(f"SQLite operational error: {exc}") from exc
                except sqlite3.Error as exc:
                    raise DatabaseError(f"SQLite error: {exc}") from exc
        raise DatabaseError("unreachable retry state")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Explicit write transaction (BEGIN IMMEDIATE ... COMMIT/ROLLBACK)."""
        conn = self.connection
        with self._write_lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # ------------------------------------------------------------------
    # Generic query helpers
    # ------------------------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        """Execute a single statement (auto-commits non-SELECT writes)."""
        conn = self.connection

        def run() -> sqlite3.Cursor:
            cursor = conn.execute(sql, tuple(params))
            conn.commit()
            return cursor

        if sql.lstrip().upper().startswith("SELECT"):
            return conn.execute(sql, tuple(params))
        return self._locked(run)

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> int:
        """Bulk write; returns affected row count."""
        payload = [tuple(r) for r in rows]
        if not payload:
            return 0
        conn = self.connection

        def run() -> int:
            cursor = conn.executemany(sql, payload)
            conn.commit()
            return cursor.rowcount

        return int(self._locked(run))

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        """Run a SELECT and return a list of dict rows."""
        try:
            cursor = self.connection.execute(sql, tuple(params))
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as exc:
            raise DatabaseError(f"query failed: {exc}") from exc

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict[str, Any]]:
        """Run a SELECT expected to return at most one row."""
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def query_df(self, sql: str, params: Sequence[Any] = ()) -> pd.DataFrame:
        """Run a SELECT and return a DataFrame."""
        rows = self.query(sql, params)
        return pd.DataFrame(rows)

    def query_scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        """First column of the first row, or *default*."""
        row = self.query_one(sql, params)
        if row is None:
            return default
        return next(iter(row.values()), default)

    # ------------------------------------------------------------------
    # Migrations
    # ------------------------------------------------------------------
    def migrate(self) -> None:
        """Apply all pending migrations in order (idempotent)."""
        conn = self.connection
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version INTEGER PRIMARY KEY,"
            " description TEXT NOT NULL,"
            " applied_at TEXT NOT NULL)"
        )
        conn.commit()
        applied = {
            int(row["version"])
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        latest = max((version for version, _, _ in MIGRATIONS), default=0)
        for version, description, statements in sorted(MIGRATIONS, key=lambda m: m[0]):
            if version in applied:
                continue
            _log.info("applying database migration v{}: {}", version, description)
            with self.transaction() as tx:
                for statement in statements:
                    tx.execute(statement)
                tx.execute(
                    "INSERT INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?)",
                    (version, description, to_iso_z(pd.Timestamp.utcnow())),
                )
        current = self.query_scalar("SELECT MAX(version) FROM schema_migrations", default=0)
        if int(current or 0) > latest:
            raise DatabaseError(
                f"database schema v{current} is newer than this code supports (v{latest})"
            )

    # ------------------------------------------------------------------
    # price_data
    # ------------------------------------------------------------------
    _PRICE_COLS = ("symbol", "timeframe", "timestamp", "open", "high", "low",
                   "close", "volume", "adj_close", "source", "inserted_at")

    def upsert_price_bars(
        self,
        symbol: str,
        timeframe: str,
        bars: pd.DataFrame | Iterable[Mapping[str, Any]],
        *,
        source: str = "yfinance",
    ) -> int:
        """Insert or replace OHLCV bars for (symbol, timeframe).

        Args:
            symbol: ticker, e.g. ``"AAPL"``.
            timeframe: canonical timeframe string (``"1d"``, ``"1h"`` ...).
            bars: DataFrame with ``timestamp`` + OHLCV columns, or dict rows.
            source: data-provider label.

        Returns:
            Number of rows written.
        """
        if isinstance(bars, pd.DataFrame):
            if bars.empty:
                return 0
            frame = bars
        else:
            frame = pd.DataFrame(list(bars))
            if frame.empty:
                return 0
        required = {"timestamp", "open", "high", "low", "close"}
        missing = required - set(frame.columns)
        if missing:
            raise DatabaseError(f"bars frame missing columns: {sorted(missing)}")

        inserted_at = to_iso_z(pd.Timestamp.utcnow())
        rows: list[tuple[Any, ...]] = []
        for record in frame.to_dict(orient="records"):
            rows.append((
                symbol.upper(),
                timeframe,
                to_iso_z(record["timestamp"]),
                float(record["open"]),
                float(record["high"]),
                float(record["low"]),
                float(record["close"]),
                float(record.get("volume", 0) or 0),
                float(record["adj_close"]) if record.get("adj_close") is not None
                and pd.notna(record.get("adj_close")) else None,
                source,
                inserted_at,
            ))
        sql = (f"INSERT OR REPLACE INTO price_data ({', '.join(self._PRICE_COLS)}) "
               f"VALUES ({', '.join('?' for _ in self._PRICE_COLS)})")
        written = self.executemany(sql, rows)
        _log.debug("upserted {} price bars for {} {}", written, symbol.upper(), timeframe)
        return written

    def fetch_price_bars(
        self,
        symbol: str,
        timeframe: str,
        *,
        start: Any = None,
        end: Any = None,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """Fetch stored bars ascending by timestamp (empty frame when none)."""
        sql = ("SELECT timestamp, open, high, low, close, volume, adj_close, source "
               "FROM price_data WHERE symbol = ? AND timeframe = ?")
        params: list[Any] = [symbol.upper(), timeframe]
        if start is not None:
            sql += " AND timestamp >= ?"
            params.append(to_iso_z(start))
        if end is not None:
            sql += " AND timestamp <= ?"
            params.append(to_iso_z(end))
        sql += " ORDER BY timestamp ASC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        df = self.query_df(sql, params)
        if not df.empty:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df

    def get_last_bar_timestamp(self, symbol: str, timeframe: str) -> Optional[str]:
        """Latest stored bar timestamp for (symbol, timeframe), or None."""
        return self.query_scalar(
            "SELECT MAX(timestamp) AS ts FROM price_data WHERE symbol = ? AND timeframe = ?",
            (symbol.upper(), timeframe),
            default=None,
        )

    def get_first_bar_timestamp(self, symbol: str, timeframe: str) -> Optional[str]:
        """Earliest stored bar timestamp for (symbol, timeframe), or None."""
        return self.query_scalar(
            "SELECT MIN(timestamp) AS ts FROM price_data WHERE symbol = ? AND timeframe = ?",
            (symbol.upper(), timeframe),
            default=None,
        )

    def count_price_bars(self, symbol: str, timeframe: str) -> int:
        """Row count for (symbol, timeframe)."""
        return int(self.query_scalar(
            "SELECT COUNT(*) AS n FROM price_data WHERE symbol = ? AND timeframe = ?",
            (symbol.upper(), timeframe), default=0))

    def list_price_symbols(self, timeframe: Optional[str] = None) -> list[str]:
        """Distinct symbols with stored bars, optionally per timeframe."""
        if timeframe is None:
            rows = self.query("SELECT DISTINCT symbol FROM price_data ORDER BY symbol")
        else:
            rows = self.query(
                "SELECT DISTINCT symbol FROM price_data WHERE timeframe = ? ORDER BY symbol",
                (timeframe,))
        return [row["symbol"] for row in rows]

    # ------------------------------------------------------------------
    # fundamental_data
    # ------------------------------------------------------------------
    def upsert_fundamentals(
        self,
        symbol: str,
        date_value: Any,
        metrics: Mapping[str, Optional[float]],
        *,
        period: str = "point",
        source: str = "yfinance",
    ) -> int:
        """Upsert (symbol, date, metric) fundamental values."""
        inserted_at = to_iso_z(pd.Timestamp.utcnow())
        day = to_iso_z(date_value)
        rows = [
            (symbol.upper(), day, str(metric), None if value is None else float(value),
             period, source, inserted_at)
            for metric, value in metrics.items()
        ]
        return self.executemany(
            "INSERT OR REPLACE INTO fundamental_data "
            "(symbol, date, metric, value, period, source, inserted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

    def fetch_fundamentals(
        self,
        symbol: str,
        metric: Optional[str] = None,
        *,
        limit: int = 500,
    ) -> pd.DataFrame:
        """Fetch fundamental rows newest-first."""
        sql = ("SELECT symbol, date, metric, value, period, source FROM fundamental_data "
               "WHERE symbol = ?")
        params: list[Any] = [symbol.upper()]
        if metric is not None:
            sql += " AND metric = ?"
            params.append(metric)
        sql += " ORDER BY date DESC LIMIT ?"
        params.append(int(limit))
        return self.query_df(sql, params)

    # ------------------------------------------------------------------
    # macro_data
    # ------------------------------------------------------------------
    def upsert_macro(
        self,
        indicator: str,
        rows: Iterable[tuple[Any, Optional[float]]],
        *,
        source: str = "FRED",
    ) -> int:
        """Upsert (date, indicator) macro observations."""
        inserted_at = to_iso_z(pd.Timestamp.utcnow())
        payload = [
            (to_iso_z(day), indicator, None if value is None else float(value), source, inserted_at)
            for day, value in rows
        ]
        return self.executemany(
            "INSERT OR REPLACE INTO macro_data (date, indicator, value, source, inserted_at) "
            "VALUES (?, ?, ?, ?, ?)",
            payload,
        )

    def fetch_macro(self, indicator: str, *, start: Any = None, end: Any = None) -> pd.DataFrame:
        """Fetch a macro series ascending by date."""
        sql = "SELECT date, indicator, value, source FROM macro_data WHERE indicator = ?"
        params: list[Any] = [indicator]
        if start is not None:
            sql += " AND date >= ?"
            params.append(to_iso_z(start))
        if end is not None:
            sql += " AND date <= ?"
            params.append(to_iso_z(end))
        sql += " ORDER BY date ASC"
        df = self.query_df(sql, params)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"], utc=True)
        return df

    # ------------------------------------------------------------------
    # sentiment_data
    # ------------------------------------------------------------------
    def upsert_sentiment(
        self,
        symbol: str,
        date_value: Any,
        source: str,
        score: Optional[float],
        volume: Optional[int] = None,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> int:
        """Upsert a (symbol, date, source) sentiment observation."""
        return self.executemany(
            "INSERT OR REPLACE INTO sentiment_data "
            "(symbol, date, source, score, volume, payload, inserted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(symbol.upper(), to_iso_z(date_value), source,
              None if score is None else float(score), volume,
              json.dumps(payload) if payload is not None else None,
              to_iso_z(pd.Timestamp.utcnow()))],
        )

    def fetch_sentiment(
        self,
        symbol: Optional[str] = None,
        source: Optional[str] = None,
        *,
        start: Any = None,
        end: Any = None,
        limit: int = 1000,
    ) -> pd.DataFrame:
        """Fetch sentiment rows newest-first with optional filters."""
        sql = "SELECT symbol, date, source, score, volume, payload FROM sentiment_data WHERE 1=1"
        params: list[Any] = []
        if symbol is not None:
            sql += " AND symbol = ?"
            params.append(symbol.upper())
        if source is not None:
            sql += " AND source = ?"
            params.append(source)
        if start is not None:
            sql += " AND date >= ?"
            params.append(to_iso_z(start))
        if end is not None:
            sql += " AND date <= ?"
            params.append(to_iso_z(end))
        sql += " ORDER BY date DESC LIMIT ?"
        params.append(int(limit))
        return self.query_df(sql, params)

    # ------------------------------------------------------------------
    # news_events
    # ------------------------------------------------------------------
    def insert_news(
        self,
        symbol: str,
        published_at: Any,
        headline: str,
        *,
        content: Optional[str] = None,
        url: Optional[str] = None,
        source: Optional[str] = None,
        sentiment_score: Optional[float] = None,
        event_type: Optional[str] = None,
    ) -> int:
        """Insert a news item (duplicates on the natural key are ignored)."""
        cursor = self.execute(
            "INSERT OR IGNORE INTO news_events "
            "(symbol, published_at, headline, content, url, source, sentiment_score, "
            " event_type, ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol.upper(), to_iso_z(published_at), headline, content, url, source,
             sentiment_score, event_type, to_iso_z(pd.Timestamp.utcnow())),
        )
        # rowcount is 0 when the natural key deduped the insert; lastrowid is
        # unreliable (stale) for ignored inserts, so guard on rowcount.
        return int(cursor.lastrowid or 0) if cursor.rowcount and cursor.rowcount > 0 else 0

    def fetch_news(
        self,
        symbol: Optional[str] = None,
        *,
        start: Any = None,
        end: Any = None,
        limit: int = 200,
    ) -> pd.DataFrame:
        """Fetch news newest-first."""
        sql = ("SELECT id, symbol, published_at, headline, content, url, source, "
               "sentiment_score, event_type FROM news_events WHERE 1=1")
        params: list[Any] = []
        if symbol is not None:
            sql += " AND symbol = ?"
            params.append(symbol.upper())
        if start is not None:
            sql += " AND published_at >= ?"
            params.append(to_iso_z(start))
        if end is not None:
            sql += " AND published_at <= ?"
            params.append(to_iso_z(end))
        sql += " ORDER BY published_at DESC LIMIT ?"
        params.append(int(limit))
        return self.query_df(sql, params)

    # ------------------------------------------------------------------
    # trade_signals
    # ------------------------------------------------------------------
    def insert_signal(
        self,
        signal_id: str,
        symbol: str,
        timestamp: Any,
        signal_type: str,
        model_source: str,
        *,
        score: Optional[float] = None,
        confidence: Optional[float] = None,
        timeframe: Optional[str] = None,
        price: Optional[float] = None,
        rationale: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Insert a generated trade signal (PK dedupes identical ids)."""
        self.execute(
            "INSERT OR REPLACE INTO trade_signals "
            "(id, symbol, timestamp, signal_type, score, confidence, model_source, "
            " timeframe, price, rationale, executed, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (signal_id, symbol.upper(), to_iso_z(timestamp), signal_type, score,
             confidence, model_source, timeframe, price,
             json.dumps(rationale) if rationale is not None else None,
             to_iso_z(pd.Timestamp.utcnow())),
        )

    def mark_signal_executed(self, signal_id: str) -> None:
        """Flag a signal as acted upon by the execution path."""
        self.execute("UPDATE trade_signals SET executed = 1 WHERE id = ?", (signal_id,))

    def recent_signals(self, symbol: Optional[str] = None, *, limit: int = 100) -> pd.DataFrame:
        """Newest signals, optionally filtered by symbol."""
        sql = ("SELECT id, symbol, timestamp, signal_type, score, confidence, model_source, "
               "timeframe, price, executed FROM trade_signals")
        params: list[Any] = []
        if symbol is not None:
            sql += " WHERE symbol = ?"
            params.append(symbol.upper())
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(int(limit))
        return self.query_df(sql, params)

    # ------------------------------------------------------------------
    # paper_trades / live_trades
    # ------------------------------------------------------------------
    def insert_paper_trade(
        self,
        symbol: str,
        side: str,
        quantity: float,
        entry_time: Any,
        entry_price: float,
        *,
        portfolio_id: str = "default",
        strategy: Optional[str] = None,
        signal_id: Optional[str] = None,
        fees: float = 0.0,
        slippage_cost: float = 0.0,
        meta: Optional[Mapping[str, Any]] = None,
    ) -> int:
        """Open a simulated trade; returns the trade id."""
        now = to_iso_z(pd.Timestamp.utcnow())
        cursor = self.execute(
            "INSERT INTO paper_trades "
            "(portfolio_id, symbol, side, quantity, entry_time, entry_price, status, "
            " fees, slippage_cost, strategy, signal_id, meta, inserted_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?)",
            (portfolio_id, symbol.upper(), side.upper(), float(quantity),
             to_iso_z(entry_time), float(entry_price), float(fees), float(slippage_cost),
             strategy, signal_id, json.dumps(meta) if meta is not None else None, now, now),
        )
        return int(cursor.lastrowid)

    def split_paper_trade(self, trade_id: int, close_quantity: float) -> Optional[int]:
        """Carve a ``close_quantity`` slice out of an OPEN paper trade.

        The original row keeps the remaining quantity and a proportionally
        reduced fee/slippage balance; a NEW open row is inserted for the
        slice with the same entry reference and its proportional share of
        entry fees/slippage. Returns the new row's id, or None when the
        trade is not open or ``close_quantity`` is not a strict partial
        (callers use ``close_paper_trade`` for full closes).

        This keeps every ``paper_trades`` row's fee/slippage balance
        consistent so ``close_paper_trade`` computes realized P&L that
        includes the correct proportional entry costs for partial closes.
        """
        row = self.query_one(
            "SELECT portfolio_id, symbol, side, quantity, entry_time, entry_price, status, "
            "fees, slippage_cost, strategy, signal_id, meta "
            "FROM paper_trades WHERE id = ?",
            (trade_id,))
        if row is None or row["status"] != "OPEN":
            return None
        remaining = float(row["quantity"])
        if not (0.0 < float(close_quantity) < remaining):
            return None
        share = float(close_quantity) / remaining
        slice_fee = float(row["fees"]) * share
        slice_slip = float(row["slippage_cost"]) * share
        now = to_iso_z(pd.Timestamp.utcnow())
        # Shrink the original row (fees/slippage stay proportional to qty).
        self.execute(
            "UPDATE paper_trades SET quantity = ?, fees = ?, slippage_cost = ?, "
            "updated_at = ? WHERE id = ?",
            (remaining - float(close_quantity), float(row["fees"]) - slice_fee,
             float(row["slippage_cost"]) - slice_slip, now, trade_id),
        )
        # Insert the slice as its own open row with proportional costs.
        cursor = self.execute(
            "INSERT INTO paper_trades "
            "(portfolio_id, symbol, side, quantity, entry_time, entry_price, status, "
            " fees, slippage_cost, strategy, signal_id, meta, inserted_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?)",
            (row["portfolio_id"], row["symbol"], row["side"],
             float(close_quantity), row["entry_time"], row["entry_price"],
             slice_fee, slice_slip, row["strategy"], row["signal_id"], row["meta"],
             now, now),
        )
        return int(cursor.lastrowid)

    def close_paper_trade(
        self,
        trade_id: int,
        exit_time: Any,
        exit_price: float,
        *,
        fees: float = 0.0,
        slippage_cost: float = 0.0,
    ) -> Optional[float]:
        """Close an open paper trade; computes and stores realized P&L.

        Returns:
            Realized P&L, or None when the trade id does not exist/is closed.
        """
        row = self.query_one(
            "SELECT side, quantity, entry_price, status, fees, slippage_cost "
            "FROM paper_trades WHERE id = ?",
            (trade_id,))
        if row is None or row["status"] != "OPEN":
            return None
        direction = 1.0 if row["side"] == "BUY" else -1.0
        gross = direction * (float(exit_price) - float(row["entry_price"])) * float(row["quantity"])
        # realized P&L nets out ALL costs: entry fees booked at open plus the
        # exit fees/slippage passed to this close
        realized = (gross - float(row["fees"]) - float(row["slippage_cost"])
                    - float(fees) - float(slippage_cost))
        self.execute(
            "UPDATE paper_trades SET exit_time = ?, exit_price = ?, status = 'CLOSED', "
            "realized_pnl = ?, fees = fees + ?, slippage_cost = slippage_cost + ?, "
            "updated_at = ? WHERE id = ?",
            (to_iso_z(exit_time), float(exit_price), realized, float(fees),
             float(slippage_cost), to_iso_z(pd.Timestamp.utcnow()), trade_id),
        )
        return realized

    def fetch_open_paper_trades(self, portfolio_id: str = "default") -> pd.DataFrame:
        """All open simulated positions for a portfolio."""
        return self.query_df(
            "SELECT * FROM paper_trades WHERE portfolio_id = ? AND status = 'OPEN' "
            "ORDER BY entry_time ASC",
            (portfolio_id,))

    def fetch_paper_trades(
        self,
        portfolio_id: str = "default",
        *,
        status: Optional[str] = None,
        limit: int = 1000,
    ) -> pd.DataFrame:
        """Paper trades, newest entries first."""
        sql = "SELECT * FROM paper_trades WHERE portfolio_id = ?"
        params: list[Any] = [portfolio_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status.upper())
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        return self.query_df(sql, params)

    # ------------------------------------------------------------------
    # performance_metrics
    # ------------------------------------------------------------------
    def upsert_performance_metric(
        self,
        date_value: Any,
        portfolio_value: float,
        *,
        portfolio_id: str = "default",
        cash: Optional[float] = None,
        invested_value: Optional[float] = None,
        daily_return: Optional[float] = None,
        cumulative_return: Optional[float] = None,
        drawdown: Optional[float] = None,
        sharpe: Optional[float] = None,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> int:
        """Upsert one portfolio snapshot row keyed by (date, portfolio)."""
        return self.executemany(
            "INSERT OR REPLACE INTO performance_metrics "
            "(date, portfolio_id, portfolio_value, cash, invested_value, daily_return, "
            " cumulative_return, drawdown, sharpe, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(to_iso_z(date_value), portfolio_id, float(portfolio_value), cash, invested_value,
              daily_return, cumulative_return, drawdown, sharpe,
              json.dumps(payload) if payload is not None else None,
              to_iso_z(pd.Timestamp.utcnow()))],
        )

    def fetch_performance_metrics(
        self,
        portfolio_id: str = "default",
        *,
        start: Any = None,
        end: Any = None,
    ) -> pd.DataFrame:
        """Portfolio snapshots ascending by date."""
        sql = ("SELECT date, portfolio_id, portfolio_value, cash, invested_value, "
               "daily_return, cumulative_return, drawdown, sharpe FROM performance_metrics "
               "WHERE portfolio_id = ?")
        params: list[Any] = [portfolio_id]
        if start is not None:
            sql += " AND date >= ?"
            params.append(to_iso_z(start))
        if end is not None:
            sql += " AND date <= ?"
            params.append(to_iso_z(end))
        sql += " ORDER BY date ASC"
        df = self.query_df(sql, params)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"], utc=True)
        return df

    # ------------------------------------------------------------------
    # circuit_breaker_log / automation_log / limit_breach_log
    # ------------------------------------------------------------------
    def log_circuit_breaker_event(
        self,
        category: str,
        action_taken: str,
        *,
        level: AlertLevel | int = AlertLevel.INFO,
        state_before: Optional[str] = None,
        state_after: Optional[str] = None,
        trigger_type: Optional[str] = None,
        timestamp: Any = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> int:
        """Append an immutable circuit-breaker event (Part 9 audit trail)."""
        cursor = self.execute(
            "INSERT INTO circuit_breaker_log "
            "(timestamp, category, level, state_before, state_after, trigger_type, "
            " action_taken, details) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (to_iso_z(timestamp if timestamp is not None else pd.Timestamp.utcnow()),
             category, int(level), state_before, state_after, trigger_type, action_taken,
             json.dumps(details) if details is not None else None),
        )
        return int(cursor.lastrowid or 0)

    def fetch_breaker_events(
        self,
        *,
        category: Optional[str] = None,
        limit: int = 200,
    ) -> pd.DataFrame:
        """Newest circuit-breaker events."""
        sql = ("SELECT id, timestamp, category, level, state_before, state_after, "
               "trigger_type, action_taken, details FROM circuit_breaker_log")
        params: list[Any] = []
        if category is not None:
            sql += " WHERE category = ?"
            params.append(category)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        return self.query_df(sql, params)

    def log_automation(
        self,
        routine: str,
        action: str,
        result: str,
        *,
        details: Optional[Mapping[str, Any]] = None,
        timestamp: Any = None,
    ) -> int:
        """Record one automation step outcome (ok|skipped|error...)."""
        cursor = self.execute(
            "INSERT INTO automation_log (timestamp, routine, action, result, details) "
            "VALUES (?, ?, ?, ?, ?)",
            (to_iso_z(timestamp if timestamp is not None else pd.Timestamp.utcnow()),
             routine, action, result,
             json.dumps(details) if details is not None else None),
        )
        return int(cursor.lastrowid or 0)

    def fetch_automation_log(self, routine: Optional[str] = None, *, limit: int = 200) -> pd.DataFrame:
        """Newest automation events."""
        sql = "SELECT id, timestamp, routine, action, result, details FROM automation_log"
        params: list[Any] = []
        if routine is not None:
            sql += " WHERE routine = ?"
            params.append(routine)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        return self.query_df(sql, params)

    def log_limit_breach(
        self,
        limit_type: str,
        action: str,
        *,
        entity: Optional[str] = None,
        value: Optional[float] = None,
        threshold: Optional[float] = None,
        details: Optional[Mapping[str, Any]] = None,
        timestamp: Any = None,
    ) -> int:
        """Record an order-limit gateway rejection/alert."""
        cursor = self.execute(
            "INSERT INTO limit_breach_log "
            "(timestamp, limit_type, entity, value, threshold, action, details) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (to_iso_z(timestamp if timestamp is not None else pd.Timestamp.utcnow()),
             limit_type, entity, value, threshold, action,
             json.dumps(details) if details is not None else None),
        )
        return int(cursor.lastrowid or 0)

    def fetch_limit_breaches(self, *, limit: int = 200) -> pd.DataFrame:
        """Newest limit-breach records."""
        return self.query_df(
            "SELECT id, timestamp, limit_type, entity, value, threshold, action, details "
            "FROM limit_breach_log ORDER BY id DESC LIMIT ?",
            (int(limit),))

    # ------------------------------------------------------------------
    # patterns_detected
    # ------------------------------------------------------------------
    _PATTERN_COLS = (
        "symbol", "timeframe", "pattern_type", "detection_date", "detection_price",
        "pattern_start_date", "pattern_end_date", "quality_score", "volume_confirmation",
        "market_regime", "sector", "vix_level", "outcome_5d", "outcome_10d",
        "outcome_20d", "outcome_magnitude", "was_successful", "sentiment_at_detection",
        "confirming_indicators",
    )

    def insert_pattern(self, pattern: Mapping[str, Any]) -> int:
        """Persist a detected pattern; returns its id."""
        now = to_iso_z(pd.Timestamp.utcnow())
        values: dict[str, Any] = {col: pattern.get(col) for col in self._PATTERN_COLS}
        values["symbol"] = str(values["symbol"]).upper()
        if values.get("detection_date") is not None:
            values["detection_date"] = to_iso_z(values["detection_date"])
        indicators = values.get("confirming_indicators")
        if indicators is not None and not isinstance(indicators, str):
            values["confirming_indicators"] = json.dumps(indicators)
        cols = [c for c in self._PATTERN_COLS if values.get(c) is not None] + ["created_at"]
        row = [values.get(c) for c in cols[:-1]] + [now]
        cursor = self.execute(
            f"INSERT INTO patterns_detected ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})",
            row,
        )
        return int(cursor.lastrowid)

    def update_pattern_outcomes(
        self,
        pattern_id: int,
        *,
        outcome_5d: Optional[float] = None,
        outcome_10d: Optional[float] = None,
        outcome_20d: Optional[float] = None,
        outcome_magnitude: Optional[float] = None,
        was_successful: Optional[bool] = None,
    ) -> None:
        """Self-labeling step: record what happened after a pattern.

        Outcomes are fractional forward returns (0.03 == +3%). ``was_successful``
        is tri-state: True/False, or None while the label horizon is pending.
        """
        updates: list[str] = []
        params: list[Any] = []
        for col, val in (("outcome_5d", outcome_5d), ("outcome_10d", outcome_10d),
                         ("outcome_20d", outcome_20d), ("outcome_magnitude", outcome_magnitude)):
            if val is not None:
                updates.append(f"{col} = ?")
                params.append(float(val))
        if was_successful is not None:
            updates.append("was_successful = ?")
            params.append(1 if was_successful else 0)
        if not updates:
            return
        params.append(int(pattern_id))
        self.execute(f"UPDATE patterns_detected SET {', '.join(updates)} WHERE id = ?", params)

    def fetch_patterns(
        self,
        *,
        symbol: Optional[str] = None,
        pattern_type: Optional[str] = None,
        unlabeled_only: bool = False,
        limit: int = 1000,
    ) -> pd.DataFrame:
        """Fetch pattern rows, newest first."""
        sql = "SELECT * FROM patterns_detected WHERE 1=1"
        params: list[Any] = []
        if symbol is not None:
            sql += " AND symbol = ?"
            params.append(symbol.upper())
        if pattern_type is not None:
            sql += " AND pattern_type = ?"
            params.append(pattern_type)
        if unlabeled_only:
            sql += " AND was_successful IS NULL"
        sql += " ORDER BY detection_date DESC LIMIT ?"
        params.append(int(limit))
        return self.query_df(sql, params)

    def pattern_success_stats(self, pattern_type: Optional[str] = None) -> pd.DataFrame:
        """Live success-rate aggregates per pattern (Part 6 self-learning stats).

        Returns one row per (pattern_type, market_regime) with labeled count,
        success rate, and mean outcome magnitudes.
        """
        sql = (
            "SELECT pattern_type, COALESCE(market_regime, 'unknown') AS market_regime, "
            "COUNT(*) AS labeled_count, "
            "AVG(was_successful) AS success_rate, "
            "AVG(outcome_5d) AS avg_outcome_5d, "
            "AVG(outcome_10d) AS avg_outcome_10d, "
            "AVG(outcome_20d) AS avg_outcome_20d, "
            "AVG(outcome_magnitude) AS avg_magnitude "
            "FROM patterns_detected WHERE was_successful IS NOT NULL"
        )
        params: list[Any] = []
        if pattern_type is not None:
            sql += " AND pattern_type = ?"
            params.append(pattern_type)
        sql += " GROUP BY pattern_type, market_regime ORDER BY labeled_count DESC"
        return self.query_df(sql, params)

    # ------------------------------------------------------------------
    # breaker_state (single-row circuit breaker persistence)
    # ------------------------------------------------------------------
    def save_breaker_state(self, state: Mapping[str, Any]) -> None:
        """Persist the circuit-breaker state machine snapshot (upsert, id=1)."""
        payload = dict(state)
        payload["updated_at"] = to_iso_z(pd.Timestamp.utcnow())
        if not isinstance(payload.get("active_breakers", "[]"), str):
            payload["active_breakers"] = json.dumps(payload.get("active_breakers", []))
        columns = ["id"] + sorted(payload.keys())
        values: list[Any] = [1] + [payload[key] for key in sorted(payload.keys())]
        self.execute(
            f"INSERT OR REPLACE INTO breaker_state ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            values,
        )

    def load_breaker_state(self) -> Optional[dict[str, Any]]:
        """Load the persisted breaker snapshot, or None on first run."""
        row = self.query_one("SELECT * FROM breaker_state WHERE id = 1")
        if row is None:
            return None
        try:
            row["active_breakers"] = json.loads(row.get("active_breakers") or "[]")
        except json.JSONDecodeError:
            row["active_breakers"] = []
        return row

    # ------------------------------------------------------------------
    # system_state (generic key/value)
    # ------------------------------------------------------------------
    def kv_set(self, key: str, value: Any) -> None:
        """Store JSON-serializable *value* under *key*."""
        self.execute(
            "INSERT OR REPLACE INTO system_state (key, value, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(value), to_iso_z(pd.Timestamp.utcnow())),
        )

    def kv_get(self, key: str, default: Any = None) -> Any:
        """Read a key, returning *default* when missing/corrupt."""
        raw = self.query_scalar("SELECT value FROM system_state WHERE key = ?", (key,), default=None)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return default

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    def integrity_check(self) -> bool:
        """PRAGMA integrity_check; True when the database is healthy."""
        result = self.query_scalar("PRAGMA integrity_check", default="error")
        ok = str(result).lower() == "ok"
        if not ok:
            _log.error("database integrity check failed: {}", result)
        return ok

    def optimize(self) -> None:
        """ANALYZE + incremental vacuum (safe during WAL)."""
        conn = self.connection
        conn.execute("PRAGMA optimize")
        conn.execute("ANALYZE")
        conn.commit()
        _log.debug("database optimized")

    def backup(self, destination: str | Path) -> Path:
        """Consistent online backup to *destination* using sqlite's backup API."""
        dest = Path(destination)
        ensure_directory(dest.parent)
        if self.path == ":memory:":
            with sqlite3.connect(str(dest)) as target:
                self.connection.backup(target)
        else:
            with sqlite3.connect(self.path) as source, sqlite3.connect(str(dest)) as target:
                source.backup(target)
        _log.info("database backed up to {}", dest)
        return dest

    def table_stats(self) -> dict[str, int]:
        """Row counts for every user table."""
        tables = self.query(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")
        return {
            str(row["name"]): int(self.query_scalar(
                f"SELECT COUNT(*) AS n FROM {row['name']}", default=0))
            for row in tables
        }

    def list_tables(self) -> list[str]:
        """Names of all user tables."""
        return list(self.table_stats().keys())

    def export_table_csv(self, table: str, destination: str | Path) -> Path:
        """Dump a table to CSV (used by report generators)."""
        df = self.query_df(f"SELECT * FROM {table}")
        dest = Path(destination)
        ensure_directory(dest.parent)
        df.to_csv(dest, index=False)
        return dest

    def reset_file(self) -> None:
        """Delete the database files (DANGER: only used by tests/reset tooling)."""
        self.close()
        if self.path == ":memory:":
            return
        for suffix in ("", "-wal", "-shm"):
            target = Path(self.path + suffix)
            if target.exists():
                target.unlink()
        _log.warning("database file {} deleted via reset_file()", self.path)

    def copy_to(self, destination: str | Path) -> Path:
        """Simple file copy of the main DB file (lock-free best effort)."""
        dest = Path(destination)
        ensure_directory(dest.parent)
        shutil.copy2(self.path, dest)
        return dest


# =============================================================================
# Cached accessor
# =============================================================================

_DB_CACHE: dict[str, DatabaseManager] = {}
_DB_CACHE_LOCK = threading.Lock()


def get_database(path: str | Path | None = None, *, reload: bool = False) -> DatabaseManager:
    """Process-wide cached :class:`DatabaseManager`.

    When *path* is None the location is taken from the master configuration
    (``data.database_path``, resolved against the config directory).
    """
    if path is None:
        try:
            from utils.config import get_config

            resolved = str(get_config().resolve_path(get_config().data.database_path))
        except Exception:  # config unavailable (early bootstrap) -> default
            resolved = DEFAULT_DATABASE_PATH
    else:
        resolved = str(path)
    key = resolved if resolved == ":memory:" else str(Path(resolved).expanduser().resolve())
    with _DB_CACHE_LOCK:
        if reload or key not in _DB_CACHE:
            _DB_CACHE[key] = DatabaseManager(resolved)
        return _DB_CACHE[key]
