"""Market-regime detector: rule-based labels from VIX, trend, and volatility.

Labels are persisted to the DB (`regime_labels` table) for downstream
use. The detector runs on price data and produces a categorical regime
for each observation.
"""
from __future__ import annotations
import logging
from typing import Optional, List
import numpy as np
import pandas as pd

from data.database import get_database
from utils.logger import get_logger

_log = get_logger("models.regime")

_REGIME_INIT_SQL = """
CREATE TABLE IF NOT EXISTS regime_labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    regime TEXT NOT NULL,
    vix_level REAL,
    trend_score REAL,
    vol_score REAL,
    details_json TEXT,
    inserted_at TEXT NOT NULL,
    UNIQUE(symbol, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_regime_symbol_ts ON regime_labels(symbol, timestamp);
CREATE INDEX IF NOT EXISTS idx_regime_regime ON regime_labels(regime);
"""

try:
    _db_regime = get_database()
    with _db_regime.transaction() as tx:
        tx.execute("CREATE TABLE IF NOT EXISTS regime_labels (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, symbol TEXT NOT NULL, regime TEXT NOT NULL, vix_level REAL, trend_score REAL, vol_score REAL, details_json TEXT, inserted_at TEXT NOT NULL, UNIQUE(symbol, timestamp))")
        tx.execute("CREATE INDEX IF NOT EXISTS idx_regime_symbol_ts ON regime_labels(symbol, timestamp)")
        tx.execute("CREATE INDEX IF NOT EXISTS idx_regime_regime ON regime_labels(regime)")
except Exception as exc:
    _log.warning("regime DB init deferred: {}", exc)


class RegimeDetector:
    """Rule-based market regime detector.

    Regimes:
        - ``calm``: low VIX (<20), positive trend, low volatility
        - ``volatile``: high VIX (>=25) or high volatility
        - ``trending``: positive trend with moderate volatility
        - ``crash``: extreme VIX (>=35) + negative trend
        - ``bear``: negative trend + moderate/high VIX
    """

    version = "4.1-regime"

    def __init__(
        self,
        vix_low: float = 20.0,
        vix_high: float = 25.0,
        vix_extreme: float = 35.0,
        vol_period: int = 20,
        trend_period: int = 50,
    ) -> None:
        self.vix_low = float(vix_low)
        self.vix_high = float(vix_high)
        self.vix_extreme = float(vix_extreme)
        self.vol_period = int(vol_period)
        self.trend_period = int(trend_period)
        _log.info(
            "RegimeDetector initialized: vix_low=%.1f vix_high=%.1f vix_extreme=%.1f",
            self.vix_low, self.vix_high, self.vix_extreme,
        )

    def detect(
        self,
        price_df: pd.DataFrame,
        vix_series: Optional[pd.Series] = None,
    ) -> pd.Series:
        """Return regime labels indexed like ``price_df``."""
        close = price_df["close"].astype(float)
        # Trend: positive if close > EMA(trend_period)
        ema_trend = close.ewm(span=self.trend_period, adjust=False).mean()
        trend_score = (close > ema_trend).astype(float)
        # Volatility: rolling std of returns
        returns = close.pct_change()
        vol = returns.rolling(self.vol_period, min_periods=self.vol_period).std()
        vol_score = vol / vol.rolling(60, min_periods=10).mean()
        # Regime rules
        regimes = pd.Series(index=price_df.index, dtype=object)
        # Default to calm
        regimes[:] = "calm"
        # Crash: extreme VIX + negative trend
        if vix_series is not None:
            high_vix = vix_series > self.vix_extreme
            crash_mask = (trend_score < 0.5) & high_vix
            regimes[crash_mask] = "crash"
        # Volatile: high VIX or very high relative volatility
        volatile_mask = ((vix_series > self.vix_high) if vix_series is not None else (vol_score > 2.0))
        regimes[volatile_mask & (regimes != "crash")] = "volatile"
        # Trending: positive trend with moderate vol
        trending_mask = (trend_score > 0.5) & (vol_score <= 2.0) & (regimes != "crash")
        regimes[trending_mask] = "trending"
        # Bear: negative trend with moderate/high VIX or vol
        bear_mask = (trend_score < 0.5) & ((vix_series > self.vix_low) if vix_series is not None else (vol_score > 1.0)) & (regimes != "crash")
        regimes[bear_mask] = "bear"
        return regimes

    def persist(
        self,
        symbol: str,
        price_df: pd.DataFrame,
        vix_series: Optional[pd.Series] = None,
    ) -> int:
        """Detect and persist regime labels to DB; return count of rows."""
        regimes = self.detect(price_df, vix_series)
        db = get_database()
        rows = 0
        for idx, reg in regimes.items():
            row = price_df.loc[idx] if idx in price_df.index else None
            vix_val = float(vix_series[idx]) if (vix_series is not None and idx in vix_series.index) else None
            trend_score = None
            vol_score = None
            cursor = db.execute(
                "INSERT OR IGNORE INTO regime_labels (timestamp, symbol, regime, vix_level, trend_score, vol_score, details_json, inserted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(idx), symbol.upper(), reg,
                    vix_val, trend_score, vol_score,
                    None, pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                ),
            )
            if cursor.rowcount is not None and cursor.rowcount > 0:
                rows += 1
        _log.info("persisted %d regime labels for %s", rows, symbol.upper())
        return rows

    def fetch_regimes(
        self,
        symbol: str,
        *,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        db = get_database()
        sql = "SELECT * FROM regime_labels WHERE symbol = ?"
        params: List[str] = [symbol.upper()]
        if start is not None:
            sql += " AND timestamp >= ?"
            params.append(start)
        if end is not None:
            sql += " AND timestamp <= ?"
            params.append(end)
        sql += " ORDER BY timestamp ASC"
        return db.query_df(sql, params)
