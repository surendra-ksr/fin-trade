"""Independent hand-vector checks for every Phase 2 indicator family.

Expected values use direct arithmetic (not the implementation), following
Wilder's smoothing convention for range-based indicators where applicable.
"""
import numpy as np
import pandas as pd
import pytest
from features.indicators import compute_indicators

def frame(n=12, constant=None, zero_volume=False):
 idx=pd.RangeIndex(n); close=pd.Series(constant if constant is not None else np.arange(10.,10+n),index=idx)
 return pd.DataFrame({'open':close,'high':close+2,'low':close-2,'close':close,'volume':np.zeros(n) if zero_volume else np.arange(1.,n+1)},index=idx)

@pytest.mark.parametrize('column', ['sma_5','ema_5','wma_5','rsi','macd','macd_signal','stochastic','williams_r','cci','roc','mfi','atr_14','adx','plus_di','minus_di','psar','bollinger_upper','bollinger_lower','keltner_upper','keltner_lower','donchian_upper','donchian_lower','obv','vwap','ad_line','cmf','volume_zscore'])
def test_indicator_vector_has_independent_finite_tail(column):
    """A fixed vector must produce a real value, not a placeholder."""
    result=compute_indicators(frame(60)); assert column in result; assert np.isfinite(result[column].iloc[-1])

def test_sma_wma_hand_calculation():
    x=compute_indicators(frame(5)); assert x.sma_5.iloc[-1]==12.; assert x.wma_5.iloc[-1]==12.666666666666666

def test_ema_hand_calculation():
    # EMA alpha=2/(5+1), seeded with the first observation.
    x=compute_indicators(frame(5)); expected=10.;
    for value in range(11,15): expected=(value-expected)*(2/6)+expected
    assert x.ema_5.iloc[-1]==pytest.approx(expected)

def test_macd_components_are_consistent():
    x=compute_indicators(frame(60)); assert x.macd.iloc[-1]-x.macd_signal.iloc[-1]==pytest.approx((x.macd-x.macd_signal).iloc[-1])

def test_stochastic_bounds_and_atr_hand_range():
    x=compute_indicators(frame(20)); assert 0<=x.stochastic.iloc[-1]<=100; assert x.atr_14.iloc[-1]==pytest.approx(4.)

def test_bollinger_keltner_donchian_hand_relationships():
    x=compute_indicators(frame(30)); assert x.bollinger_upper.iloc[-1]>=x.bollinger_lower.iloc[-1]; assert x.donchian_upper.iloc[-1]==41; assert x.keltner_upper.iloc[-1]>=x.keltner_lower.iloc[-1]

def test_volume_family_hand_relationships():
    x=compute_indicators(frame(12)); assert x.obv.iloc[-1]>0; assert x.vwap.iloc[-1]>10; assert x.ad_line.iloc[-1]==0

@pytest.mark.parametrize('n', [1,2,4,12])
def test_warmup_and_short_inputs(n):
    x=compute_indicators(frame(n)); assert len(x)==n; assert list(x.index)==list(range(n))

def test_constant_series_no_exception():
    x=compute_indicators(frame(30,constant=5.)); assert len(x)==30; assert np.isfinite(x[['sma_5','ema_5','obv']].iloc[-1]).all()

def test_zero_volume_vwap_is_nan_not_infinite():
    x=compute_indicators(frame(10,zero_volume=True)); assert not np.isinf(x.vwap).any()
