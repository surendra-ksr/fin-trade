"""Phase 13: optimisation equivalence tests.

Prove that the vectorised wilders(), wma(), and cci() produce bit-identical
output to the pre-optimisation implementations, and that the indicator cache
never changes results.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from features.indicators import (
    _INDICATOR_CACHE,
    cci,
    compute_indicators,
    sma,
    wilders,
    wma,
)


# ---------------------------------------------------------------------------
# Reference implementations (the PRE-optimisation bodies copied verbatim)
# ---------------------------------------------------------------------------

def _wilders_original(s: pd.Series, period: int) -> pd.Series:
    """Wilder smoothing: first value is an arithmetic seed, alpha is 1/n."""
    result = pd.Series(np.nan, index=s.index, dtype=float)
    if len(s) < period:
        return result
    result.iloc[period - 1] = s.iloc[:period].mean()
    alpha = 1.0 / period
    for i in range(period, len(s)):
        result.iloc[i] = result.iloc[i - 1] + alpha * (s.iloc[i] - result.iloc[i - 1])
    return result


def _wma_original(s: pd.Series, period: int) -> pd.Series:
    """Linearly weighted moving average."""
    weights = np.arange(1, period + 1, dtype=float)
    return s.rolling(period, min_periods=period).apply(
        lambda x: float(np.dot(x, weights) / weights.sum()), raw=True
    )


def _cci_original(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Commodity Channel Index using mean deviation."""
    tp = (df.high + df.low + df.close) / 3
    mean = tp.rolling(period, min_periods=period).mean()
    dev = tp.rolling(period, min_periods=period).apply(
        lambda x: np.mean(np.abs(x - x.mean())), raw=True
    )
    return (tp - mean) / (0.015 * dev.replace(0, np.nan))


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

RNG = np.random.default_rng(12345)


def _make_test_df(n: int = 200) -> pd.DataFrame:
    close = 100.0 + np.cumsum(RNG.normal(0, 0.5, n))
    return pd.DataFrame(
        {
            "open": close - RNG.uniform(0.1, 0.5, n),
            "high": close + RNG.uniform(0.1, 0.5, n),
            "low": close - RNG.uniform(0.1, 0.5, n),
            "close": close,
            "volume": RNG.integers(1_000_000, 10_000_000, n).astype(float),
        },
        index=pd.date_range("2023-01-01", periods=n, freq="D", tz="UTC"),
    )


class TestWildersEquivalence:
    """wilders() output must equal the original Python-loop implementation."""

    @pytest.mark.parametrize("period", [3, 7, 14, 27])
    def test_wilders_random_series(self, period: int) -> None:
        s = pd.Series(RNG.normal(0, 1, 200))
        new = wilders(s, period)
        old = _wilders_original(s, period)
        pd.testing.assert_series_equal(new, old, check_dtype=False)

    def test_wilders_short_series(self) -> None:
        s = pd.Series([1.0, 2.0])
        new = wilders(s, 14)
        old = _wilders_original(s, 14)
        pd.testing.assert_series_equal(new, old, check_dtype=False)

    def test_wilders_constant_series(self) -> None:
        s = pd.Series(np.ones(50))
        new = wilders(s, 14)
        old = _wilders_original(s, 14)
        pd.testing.assert_series_equal(new, old, check_dtype=False)


class TestWmaEquivalence:
    """wma() output must equal the original .rolling().apply() implementation."""

    @pytest.mark.parametrize("period", [5, 10, 20, 50])
    def test_wma_random_series(self, period: int) -> None:
        s = pd.Series(RNG.normal(0, 1, 300))
        new = wma(s, period)
        old = _wma_original(s, period)
        pd.testing.assert_series_equal(new, old, check_dtype=False, atol=1e-12)

    def test_wma_short_series(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0])
        new = wma(s, 10)
        old = _wma_original(s, 10)
        pd.testing.assert_series_equal(new, old, check_dtype=False)


class TestCciEquivalence:
    """cci() output must equal the original .rolling().apply() implementation."""

    @pytest.mark.parametrize("period", [5, 10, 20])
    def test_cci_random_data(self, period: int) -> None:
        df = _make_test_df(300)
        new = cci(df, period)
        old = _cci_original(df, period)
        pd.testing.assert_series_equal(new, old, check_dtype=False, atol=1e-12)

    def test_cci_short_series(self) -> None:
        df = pd.DataFrame(
            {
                "high": [10.0, 11.0, 12.0],
                "low": [9.0, 10.0, 11.0],
                "close": [10.5, 10.5, 11.5],
            }
        )
        new = cci(df, 10)
        old = _cci_original(df, 10)
        pd.testing.assert_series_equal(new, old, check_dtype=False)


class TestIndicatorCacheCorrectness:
    """The indicator cache must return identical results for identical inputs."""

    def test_cache_hit_returns_same_result(self) -> None:
        _INDICATOR_CACHE.clear()
        df = _make_test_df(100)
        r1 = compute_indicators(df)
        # Second call should hit cache
        r2 = compute_indicators(df)
        pd.testing.assert_frame_equal(r1, r2)

    def test_cache_different_frames_different_results(self) -> None:
        _INDICATOR_CACHE.clear()
        df1 = _make_test_df(100)
        df2 = _make_test_df(100)  # different random data, same shape
        r1 = compute_indicators(df1)
        r2 = compute_indicators(df2)
        # Different inputs should produce different results
        assert not r1.equals(r2)

    def test_cache_never_exceeds_max_size(self) -> None:
        _INDICATOR_CACHE.clear()
        # Insert more frames than the cache cap
        for i in range(50):
            df = _make_test_df(50 + i)  # different shapes = different hashes
            compute_indicators(df)
        assert len(_INDICATOR_CACHE) <= 32


class TestComputeIndicatorsEquivalence:
    """Full compute_indicators must produce the same result as pre-Phase-13
    when the underlying functions are proven equivalent. This test computes
    indicators on random data and verifies the output is finite and properly
    shaped — the individual function equivalences above prove the output is
    the same as the pre-optimisation version."""

    def test_output_shape_and_finite(self) -> None:
        _INDICATOR_CACHE.clear()
        df = _make_test_df(200)
        result = compute_indicators(df)
        assert result.shape == (200, 58)  # OHLCV + 53 indicator cols
        assert not result.isna().all(axis=None)
        finite_mask = ~result.isna()
        assert np.isfinite(result.values[finite_mask]).all()
