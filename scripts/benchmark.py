#!/usr/bin/env python3
"""Phase 13 benchmark harness — measure hot paths before and after optimisation.

Run from the repository root:
    .venv/bin/python scripts/benchmark.py

Outputs a Markdown table for the evidence pack.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import timeit
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Ensure the repo root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Test data generators
# ---------------------------------------------------------------------------

SEED = 42
N_DAYS = 500
N_SYMBOLS = 10

RNG = np.random.default_rng(SEED)


def _make_ohlcv(n: int = N_DAYS) -> pd.DataFrame:
    """Synthetic OHLCV with a random walk."""
    close = 100.0 + np.cumsum(RNG.normal(0, 0.5, n))
    high = close + RNG.uniform(0.1, 0.5, n)
    low = close - RNG.uniform(0.1, 0.5, n)
    open_ = close - RNG.uniform(-0.3, 0.3, n)
    volume = RNG.integers(1_000_000, 10_000_000, n).astype(float)
    idx = pd.date_range("2023-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _make_macro(n: int = 500) -> pd.DataFrame:
    idx = pd.date_range("2023-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame(
        {"vix": RNG.lognormal(3.3, 0.1, n), "dxy": 100 + np.cumsum(RNG.normal(0, 0.1, n))},
        index=idx,
    )


# ---------------------------------------------------------------------------
# Benchmarked paths
# ---------------------------------------------------------------------------

def bench_indicator_pipeline(df: pd.DataFrame, iterations: int = 10) -> float:
    from features.indicators import compute_indicators  # noqa: F811
    stmt = "compute_indicators(df)"
    timer = timeit.Timer(stmt, globals={"compute_indicators": compute_indicators, "df": df})
    # Run once to warm JIT/caches, then measure
    compute_indicators(df)
    return timer.timeit(number=iterations) / iterations


def bench_feature_engineering(
    df: pd.DataFrame, timeframes: dict, macro: pd.DataFrame, iterations: int = 5
) -> float:
    from features.feature_engineer import engineer  # noqa: F811
    stmt = "engineer(df, timeframes=timeframes, macro=macro)"
    ns = {"engineer": engineer, "df": df, "timeframes": timeframes, "macro": macro}
    timer = timeit.Timer(stmt, globals=ns)
    engineer(df, timeframes=timeframes, macro=macro)
    return timer.timeit(number=iterations) / iterations


def bench_backtest_replay(prices: np.ndarray, signals: np.ndarray, iterations: int = 10) -> float:
    from backtest.fill_engine import match_fill_series  # noqa: F811
    stmt = "match_fill_series(prices, signals, fee_bps=1.0, slippage_bps=2.0)"
    ns = {"match_fill_series": match_fill_series, "prices": prices, "signals": signals}
    timer = timeit.Timer(stmt, globals=ns)
    match_fill_series(prices, signals)
    return timer.timeit(number=iterations) / iterations


def bench_hot_db_queries(db_path: str, iterations: int = 50) -> dict[str, float]:
    """Measure the three hottest query patterns."""
    results: dict[str, float] = {}
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    # Query 1: latest prices per symbol (hot path for live trading)
    stmt1 = """
        SELECT symbol, MAX(timestamp) AS latest
        FROM price_data
        GROUP BY symbol
    """
    timer1 = timeit.Timer(
        "cur.execute(stmt)", globals={"cur": cur, "stmt": stmt1}
    )
    cur.execute(stmt1).fetchall()  # warm
    results["latest_prices"] = timer1.timeit(number=iterations) / iterations

    # Query 2: price window for backtesting (ORDER BY timestamp)
    stmt2 = """
        SELECT timestamp, open, high, low, close, volume
        FROM price_data
        WHERE symbol = 'AAPL' AND timeframe = '1d'
        ORDER BY timestamp
    """
    timer2 = timeit.Timer(
        "cur.execute(stmt)", globals={"cur": cur, "stmt": stmt2}
    )
    cur.execute(stmt2).fetchall()  # warm
    results["price_window"] = timer2.timeit(number=iterations) / iterations

    # Query 3: all symbols for a timeframe (feature engineering)
    stmt3 = """
        SELECT symbol, timestamp, open, high, low, close, volume
        FROM price_data
        WHERE timeframe = '1d'
        ORDER BY symbol, timestamp
    """
    timer3 = timeit.Timer(
        "cur.execute(stmt)", globals={"cur": cur, "stmt": stmt3}
    )
    cur.execute(stmt3).fetchall()  # warm
    results["all_symbols_tf"] = timer3.timeit(number=iterations) / iterations

    conn.close()
    return results


# ---------------------------------------------------------------------------
# DB seeding helper
# ---------------------------------------------------------------------------

def seed_db(db_path: str, n_symbols: int = N_SYMBOLS, n_days: int = N_DAYS) -> None:
    """Create a temporary db with realistic price_data rows."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS price_data (
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL DEFAULT 0,
            adj_close REAL,
            source TEXT NOT NULL DEFAULT 'yfinance',
            inserted_at TEXT NOT NULL,
            PRIMARY KEY (symbol, timeframe, timestamp)
        )"""
    )
    # Existing indices
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_price_data_tf_ts ON price_data(timeframe, timestamp)"
    )
    symbols = [f"STOCK_{i:02d}" for i in range(n_symbols)]
    rows: list[tuple] = []
    base = pd.Timestamp("2023-01-01", tz="UTC")
    for sym in symbols:
        rng = np.random.default_rng(hash(sym) % (2**31))
        close = 100.0 + np.cumsum(rng.normal(0, 0.5, n_days))
        for day in range(n_days):
            ts = (base + pd.Timedelta(days=day)).isoformat()
            c = float(close[day])
            rows.append(
                (
                    sym,
                    "1d",
                    ts,
                    c - rng.uniform(0, 0.3),
                    c + rng.uniform(0.1, 0.5),
                    c - rng.uniform(0.1, 0.5),
                    c,
                    float(rng.integers(1_000_000, 10_000_000)),
                    c,
                    "benchmark",
                    ts,
                )
            )
    cur.executemany(
        "INSERT OR REPLACE INTO price_data VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# EXPLAIN helper
# ---------------------------------------------------------------------------

def explain_queries(db_path: str) -> dict[str, str]:
    """Capture EXPLAIN QUERY PLAN for the hot queries (after indices)."""
    plans: dict[str, str] = {}
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    queries = {
        "latest_prices": """
            EXPLAIN QUERY PLAN
            SELECT symbol, MAX(timestamp) AS latest
            FROM price_data
            GROUP BY symbol
        """,
        "price_window": """
            EXPLAIN QUERY PLAN
            SELECT timestamp, open, high, low, close, volume
            FROM price_data
            WHERE symbol = 'AAPL' AND timeframe = '1d'
            ORDER BY timestamp
        """,
        "all_symbols_tf": """
            EXPLAIN QUERY PLAN
            SELECT symbol, timestamp, open, high, low, close, volume
            FROM price_data
            WHERE timeframe = '1d'
            ORDER BY symbol, timestamp
        """,
    }

    for name, q in queries.items():
        plan_rows = cur.execute(q).fetchall()
        plans[name] = "\n".join(
            f"  {r[0]}|{r[1]}|{r[2]}" for r in plan_rows
        )

    conn.close()
    return plans


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 70)
    print("Phase 13 — Benchmark Harness")
    print("=" * 70)

    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)

    try:
        # 1. Seed DB
        print("\n[1/5] Seeding DB …")
        seed_db(db_path)
        print(f"      DB {db_path} ({N_SYMBOLS} symbols × {N_DAYS} days)")

        # 2. Explain query plans BEFORE extra indices
        print("\n[2/5] EXPLAIN QUERY PLAN (existing indices)")
        plans_before = explain_queries(db_path)
        for name, plan in plans_before.items():
            print(f"\n  --- {name} ---")
            print(plan)

        # 3. Measure indicator pipeline
        print("\n[3/5] Indicator pipeline …")
        df = _make_ohlcv()
        t_ind = bench_indicator_pipeline(df)
        print(f"      compute_indicators: {t_ind*1000:8.2f} ms/call")

        # 4. Measure feature engineering
        print("\n[4/5] Feature engineering …")
        # Make timeframe dict with a single other timeframe
        hourly = _make_ohlcv(2000)  # 2000 hours
        timeframes = {"1h": hourly}
        macro = _make_macro()
        t_feat = bench_feature_engineering(df, timeframes, macro)
        print(f"      engineer (multi-tf + macro): {t_feat*1000:8.2f} ms/call")

        # 5. Backtest replay
        print("\n[5/5] Backtest replay …")
        prices = df["close"].to_numpy(dtype=float)
        signals = np.where(RNG.random(len(prices)) < 0.05, RNG.choice([1, -1], len(prices)), 0)
        t_bt = bench_backtest_replay(prices, signals)
        print(f"      match_fill_series ({len(prices)} bars): {t_bt*1000:8.2f} ms/call")

        # 6. Hot DB queries
        print("\n[6/5] Hot DB queries …")
        db_times = bench_hot_db_queries(db_path)
        for qname, qtime in db_times.items():
            print(f"      {qname}: {qtime*1000:8.2f} ms/call")

        # Print summary table
        print("\n" + "=" * 70)
        print("BEFORE/AFTER placeholder — rerun after optimisations")
        print("=" * 70)
        print(
            f"""
| Benchmark | Baseline (ms) | Optimized (ms) | Delta |
|---|---:|---:|---:|
| Indicator pipeline (500d) | {t_ind*1000:.2f} | — | — |
| Feature engineering (multi-tf+macro) | {t_feat*1000:.2f} | — | — |
| Backtest replay (500 bars) | {t_bt*1000:.2f} | — | — |
| DB: latest_prices | {db_times['latest_prices']*1000:.2f} | — | — |
| DB: price_window | {db_times['price_window']*1000:.2f} | — | — |
| DB: all_symbols_tf | {db_times['all_symbols_tf']*1000:.2f} | — | — |
"""
        )

        # JSON dump for machine consumption
        result = {
            "indicator_pipeline_ms": t_ind * 1000,
            "feature_engineering_ms": t_feat * 1000,
            "backtest_replay_ms": t_bt * 1000,
            "db_queries_ms": {k: v * 1000 for k, v in db_times.items()},
            "explain_plans": plans_before,
        }
        print("\n--- JSON ---")
        print(json.dumps(result, indent=2))

    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


if __name__ == "__main__":
    main()
