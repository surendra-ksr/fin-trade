"""Causal feature engineering for multi-timeframe market research.

All joins use the latest observation at or before the target timestamp.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from .indicators import compute_indicators

@dataclass(frozen=True)
class FeatureConfig:
    """Configuration for causal derived features."""
    lags: tuple[int, ...] = (1, 2, 5, 10, 20)
    windows: tuple[int, ...] = (5, 20, 50)

def _causal_join(left: pd.DataFrame, right: pd.DataFrame, suffix: str) -> pd.DataFrame:
    """Backward as-of join, rejecting unsorted inputs."""
    a=left.sort_index(); b=right.sort_index()
    if not a.index.is_monotonic_increasing or not b.index.is_monotonic_increasing: raise ValueError("feature indexes must be sorted")
    return pd.merge_asof(a,b,left_index=True,right_index=True,direction="backward",suffixes=("",f"_{suffix}"))

def add_derived_features(frame: pd.DataFrame, config: FeatureConfig=FeatureConfig()) -> pd.DataFrame:
    """Add returns, strictly-past lags, and causal rolling statistics."""
    out=frame.copy(); close=out["close"].astype(float)
    for lag in config.lags: out[f"return_{lag}"]=close.pct_change(lag); out[f"close_lag_{lag}"]=close.shift(lag)
    for window in config.windows:
        roll=close.rolling(window,min_periods=window); out[f"return_std_{window}"]=close.pct_change().rolling(window,min_periods=window).std(); out[f"close_mean_{window}"]=roll.mean(); out[f"close_std_{window}"]=roll.std()
    return out

def add_timeframe_features(base: pd.DataFrame, timeframes: dict[str,pd.DataFrame]) -> pd.DataFrame:
    """Compute each timeframe independently, then backward-join its columns."""
    out=base.sort_index()
    for name, frame in timeframes.items():
        features=compute_indicators(frame.sort_index()).add_prefix(f"{name}_")
        out=_causal_join(out,features,name)
    return out

def add_intermarket_features(frame: pd.DataFrame, benchmarks: dict[str,pd.DataFrame]) -> pd.DataFrame:
    """Add benchmark returns, rolling beta and correlation without look-ahead."""
    out=frame.sort_index(); asset=out.close.pct_change()
    for name, benchmark in benchmarks.items():
        b=benchmark.sort_index().close.astype(float); joined=_causal_join(pd.DataFrame({"asset_return":asset},index=out.index),pd.DataFrame({"benchmark_close":b},index=b.index),name); br=joined.benchmark_close.pct_change(); out[f"{name}_return"]=br; out[f"{name}_beta"]=asset.rolling(60,min_periods=20).cov(br)/br.rolling(60,min_periods=20).var(); out[f"{name}_correlation"]=asset.rolling(60,min_periods=20).corr(br)
    return out

def add_macro_features(frame: pd.DataFrame, macro: pd.DataFrame) -> pd.DataFrame:
    """Backward-join macro observations and add causal changes."""
    out=_causal_join(frame.sort_index(),macro.sort_index(),"macro")
    for column in macro.columns:
        if pd.api.types.is_numeric_dtype(macro[column]): out[f"{column}_change"]=out[column].diff()
    return out

def engineer(price: pd.DataFrame, markets: dict[str,pd.DataFrame]|None=None, macro: pd.DataFrame|None=None, timeframes: dict[str,pd.DataFrame]|None=None, config: FeatureConfig=FeatureConfig()) -> pd.DataFrame:
    """Run indicators, derived features, causal timeframe/market joins and macro joins."""
    out=compute_indicators(price.sort_index()); out=add_derived_features(out,config)
    if timeframes: out=add_timeframe_features(out,timeframes)
    if markets: out=add_intermarket_features(out,markets)
    if macro is not None: out=add_macro_features(out,macro)
    return out
