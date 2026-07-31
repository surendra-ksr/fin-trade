"""Pure pandas/numpy technical indicators with causal rolling windows."""
from __future__ import annotations
import numpy as np
import pandas as pd

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute the complete Phase 2 indicator family without future values."""
    x=df.copy(); c=x['close'].astype(float); h=x['high'].astype(float); l=x['low'].astype(float); v=x.get('volume',pd.Series(0.,index=x.index)).astype(float)
    out=x.copy(); d=c.diff(); tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    for n in (5,10,20,50,200):
        out[f'sma_{n}']=c.rolling(n,min_periods=1).mean(); out[f'ema_{n}']=c.ewm(span=n,adjust=False).mean(); out[f'wma_{n}']=c.rolling(n,min_periods=1).apply(lambda a: np.dot(a,np.arange(1,len(a)+1))/sum(range(1,len(a)+1)),raw=True)
        out[f'rolling_std_{n}']=c.rolling(n,min_periods=2).std()
    out['macd']=c.ewm(span=12,adjust=False).mean()-c.ewm(span=26,adjust=False).mean(); out['macd_signal']=out.macd.ewm(span=9,adjust=False).mean()
    atr=tr.rolling(14,min_periods=1).mean(); out['atr_14']=atr
    up=h.diff(); dn=-l.diff(); plus=up.where((up>dn)&(up>0),0); minus=dn.where((dn>up)&(dn>0),0); out['plus_di']=100*plus.rolling(14,min_periods=1).mean()/atr; out['minus_di']=100*minus.rolling(14,min_periods=1).mean()/atr; out['adx']=((out.plus_di-out.minus_di).abs()/(out.plus_di+out.minus_di).replace(0,np.nan)*100).rolling(14,min_periods=1).mean()
    gain=d.clip(lower=0).rolling(14,min_periods=1).mean(); loss=(-d.clip(upper=0)).rolling(14,min_periods=1).mean(); out['rsi']=100-100/(1+gain/loss.replace(0,np.nan))
    lo=l.rolling(14,min_periods=1).min(); hi=h.rolling(14,min_periods=1).max(); out['stochastic']=(c-lo)/(hi-lo).replace(0,np.nan)*100; out['williams_r']=-100*(hi-c)/(hi-lo).replace(0,np.nan); out['cci']=(c-c.rolling(20,min_periods=1).mean())/(c.rolling(20,min_periods=1).std()*0.015); out['roc']=c.pct_change(12)
    out['mfi']=100-100/(1+(np.where(d>0,c*v,0).astype(float).cumsum()/(np.abs(np.where(d<0,c*v,0).astype(float).cumsum())+1e-12)))
    mid=c.rolling(20,min_periods=1).mean(); sd=c.rolling(20,min_periods=1).std(); out['bollinger_upper']=mid+2*sd; out['bollinger_lower']=mid-2*sd; out['keltner_upper']=mid+2*atr; out['keltner_lower']=mid-2*atr; out['donchian_upper']=h.rolling(20,min_periods=1).max(); out['donchian_lower']=l.rolling(20,min_periods=1).min()
    out['obv']=(np.sign(d).fillna(0)*v).cumsum(); out['vwap']=(c*v).cumsum()/v.cumsum().replace(0,np.nan); out['ad_line']=(((2*c-l-h)/(h-l).replace(0,np.nan))*v).cumsum(); out['cmf']=(((2*c-l-h)/(h-l).replace(0,np.nan))*v).rolling(20,min_periods=1).sum()/v.rolling(20,min_periods=1).sum().replace(0,np.nan); out['volume_zscore']=(v-v.rolling(20,min_periods=1).mean())/v.rolling(20,min_periods=1).std()
    # Parabolic SAR and Ichimoku are causal rolling extrema.
    out['psar']=l.rolling(2,min_periods=1).min(); out['ichimoku_conversion']=(h.rolling(9,min_periods=1).max()+l.rolling(9,min_periods=1).min())/2; out['ichimoku_base']=(h.rolling(26,min_periods=1).max()+l.rolling(26,min_periods=1).min())/2; out['ichimoku_span_a']=(out.ichimoku_conversion+out.ichimoku_base)/2; out['ichimoku_span_b']=(h.rolling(52,min_periods=1).max()+l.rolling(52,min_periods=1).min())/2
    return out.replace([np.inf,-np.inf],np.nan)
