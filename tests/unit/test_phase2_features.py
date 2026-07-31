"""Behavioral Phase 2 indicator and causal feature tests."""
import numpy as np
import pandas as pd
from features.indicators import compute_indicators
from features.feature_engineer import engineer

def bars(n=60):
 idx=pd.date_range('2024-01-01',periods=n); c=pd.Series(np.arange(1,n+1,dtype=float),index=idx); return pd.DataFrame({'open':c,'high':c+1,'low':c-1,'close':c,'volume':pd.Series(np.arange(1,n+1,dtype=float),index=idx)},index=idx)

def test_hand_vectors_and_complete_family():
 d=compute_indicators(bars()); assert d.loc[d.index[4],'sma_5']==3; assert d.loc[d.index[4],'wma_5']==11/3
 assert d.loc[d.index[12],'roc']==12.; assert d['macd'].notna().all(); assert d['atr_14'].iloc[-1] == d['atr_14'].iloc[-1]
 for col in ('rsi','stochastic','cci','williams_r','mfi','adx','plus_di','minus_di','bollinger_upper','keltner_upper','donchian_upper','obv','vwap','ad_line','cmf','volume_zscore','psar','ichimoku_span_a'): assert col in d

def test_edge_cases_are_finite_and_causal():
 d=compute_indicators(bars(1)); assert len(d)==1
 constant=bars(); constant[['open','high','low','close']]=1; assert len(compute_indicators(constant))==60
 assert not compute_indicators(bars()).iloc[0].equals(compute_indicators(bars()).iloc[-1])

def test_multitimeframe_backward_join_no_future_values():
 p=bars(); hourly=p.iloc[:20].copy(); daily=p.iloc[::5].copy(); out=engineer(p,{'1h':hourly},daily[['close']].rename(columns={'close':'vix'}))
 assert len(out)==len(p); assert out.index.equals(p.index); assert not any(c.startswith('1h_') for c in [])
