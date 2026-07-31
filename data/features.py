"""Deterministic, dependency-light technical feature engineering (Phase 2).

All functions are causal: a row only uses the current row and observations
before it.  This makes the module safe to use for walk-forward experiments.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def _ohlc(frame):
    return frame.rename(columns={c.lower(): c.lower() for c in frame.columns})


def add_indicators(frame: pd.DataFrame, periods=(3, 5, 8, 10, 14, 20, 30, 50, 75, 100, 150, 200)) -> pd.DataFrame:
    """Return OHLCV plus a broad, stable indicator suite (100+ columns)."""
    if frame.empty: return frame.copy()
    x = _ohlc(frame.copy()); close=x['close'].astype(float); high=x['high'].astype(float); low=x['low'].astype(float)
    volume=x.get('volume', pd.Series(0., index=x.index)).astype(float)
    r=close.pct_change(); out=x.copy()
    for n in periods:
        ma=close.rolling(n, min_periods=1).mean(); std=close.rolling(n, min_periods=1).std().fillna(0)
        out[f'sma_{n}']=ma; out[f'ema_{n}']=close.ewm(span=n, adjust=False).mean()
        out[f'volatility_{n}']=r.rolling(n, min_periods=2).std()*np.sqrt(252)
        out[f'zscore_{n}']=(close-ma).div(std.replace(0,np.nan))
        out[f'volume_sma_{n}']=volume.rolling(n,min_periods=1).mean()
        out[f'volume_ratio_{n}']=volume.div(out[f'volume_sma_{n}'].replace(0,np.nan))
    tr=pd.concat([high-low,(high-close.shift()).abs(),(low-close.shift()).abs()],axis=1).max(axis=1)
    out['true_range']=tr; out['atr_14']=tr.rolling(14,min_periods=1).mean()
    delta=close.diff(); gain=delta.clip(lower=0); loss=-delta.clip(upper=0)
    rs=gain.rolling(14,min_periods=1).mean().div(loss.rolling(14,min_periods=1).mean().replace(0,np.nan))
    out['rsi_14']=100-100/(1+rs)
    fast=close.ewm(span=12,adjust=False).mean(); slow=close.ewm(span=26,adjust=False).mean(); macd=fast-slow
    out['macd']=macd; out['macd_signal']=macd.ewm(span=9,adjust=False).mean(); out['macd_hist']=macd-out['macd_signal']
    mid=close.rolling(20,min_periods=1).mean(); band=close.rolling(20,min_periods=1).std().fillna(0)*2
    out['bollinger_mid']=mid; out['bollinger_upper']=mid+band; out['bollinger_lower']=mid-band; out['bollinger_pctb']=(close-(mid-band)).div((2*band).replace(0,np.nan))
    ll=low.rolling(14,min_periods=1).min(); hh=high.rolling(14,min_periods=1).max(); stoch=(close-ll).div((hh-ll).replace(0,np.nan))*100
    out['stoch_k']=stoch; out['stoch_d']=stoch.rolling(3,min_periods=1).mean()
    out['roc_12']=close.pct_change(12); out['momentum_10']=close-close.shift(10)
    out['obv']=(np.sign(delta).fillna(0)*volume).cumsum(); out['vwap']=(close*volume).cumsum().div(volume.cumsum().replace(0,np.nan))
    out['high_low_range']=(high-low).div(close.replace(0,np.nan)); out['close_position']=(close-low).div((high-low).replace(0,np.nan))
    for lag in (1,2,3,5,10,20): out[f'return_{lag}']=close.pct_change(lag)
    return out.replace([np.inf,-np.inf],np.nan)


class FeaturePipeline:
    """Fit-free pipeline; ``transform`` preserves index and never leaks future data."""
    def __init__(self, fill_value=0.0): self.fill_value=fill_value
    def transform(self, frame):
        result=add_indicators(frame)
        return result.replace([np.inf,-np.inf],np.nan).ffill().fillna(self.fill_value)
    fit = lambda self, frame: self
    fit_transform = lambda self, frame: self.transform(frame)

__all__=['add_indicators','FeaturePipeline']
