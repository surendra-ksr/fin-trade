"""Causal technical indicators implemented with pandas and NumPy only.

The public functions use conventional rolling definitions.  No function reads
future rows: rolling windows are right-aligned and the PSAR state machine is
processed strictly from left to right.
"""
from __future__ import annotations
import logging
import numpy as np
import pandas as pd
log = logging.getLogger(__name__)

def sma(s: pd.Series, period: int) -> pd.Series:
    """Simple moving average with NaN warm-up."""
    return s.rolling(period, min_periods=period).mean()

def ema(s: pd.Series, period: int) -> pd.Series:
    """Exponential moving average seeded by the first observation."""
    return s.ewm(span=period, adjust=False, min_periods=1).mean()

def wma(s: pd.Series, period: int) -> pd.Series:
    """Linearly weighted moving average."""
    weights=np.arange(1, period+1, dtype=float)
    return s.rolling(period, min_periods=period).apply(lambda x: float(np.dot(x,weights)/weights.sum()), raw=True)

def true_range(df: pd.DataFrame) -> pd.Series:
    """True range including the previous close gap."""
    return pd.concat([df.high-df.low,(df.high-df.close.shift()).abs(),(df.low-df.close.shift()).abs()],axis=1).max(axis=1)

def wilders(s: pd.Series, period: int) -> pd.Series:
    """Wilder smoothing: first value is an arithmetic seed, alpha is 1/n."""
    result=pd.Series(np.nan,index=s.index,dtype=float)
    if len(s)<period: return result
    result.iloc[period-1]=s.iloc[:period].mean()
    alpha=1.0/period
    for i in range(period,len(s)): result.iloc[i]=result.iloc[i-1]+alpha*(s.iloc[i]-result.iloc[i-1])
    return result

def rsi(close: pd.Series, period: int=14) -> pd.Series:
    """Wilder RSI from separated gain and loss series."""
    d=close.diff(); gain=wilders(d.clip(lower=0).fillna(0),period); loss=wilders((-d.clip(upper=0)).fillna(0),period)
    return (100-100/(1+gain/loss.replace(0,np.nan))).where(loss.ne(0),100.0)

def atr(df: pd.DataFrame, period: int=14) -> pd.Series:
    """Wilder average true range."""
    return wilders(true_range(df),period)

def macd(close: pd.Series, fast=12, slow=26, signal=9) -> pd.DataFrame:
    """MACD line, EMA signal, and histogram."""
    line=ema(close,fast)-ema(close,slow); sig=ema(line,signal)
    return pd.DataFrame({'macd':line,'macd_signal':sig,'macd_hist':line-sig})

def stochastic(df: pd.DataFrame, period=14, smooth=3) -> pd.DataFrame:
    """Fast stochastic %K and its causal %D average."""
    lo=df.low.rolling(period,min_periods=period).min(); hi=df.high.rolling(period,min_periods=period).max(); k=100*(df.close-lo)/(hi-lo).replace(0,np.nan)
    return pd.DataFrame({'stochastic':k,'stochastic_d':k.rolling(smooth,min_periods=smooth).mean()})

def cci(df: pd.DataFrame, period=20) -> pd.Series:
    """Commodity Channel Index using mean deviation."""
    tp=(df.high+df.low+df.close)/3; mean=tp.rolling(period,min_periods=period).mean(); dev=tp.rolling(period,min_periods=period).apply(lambda x: np.mean(np.abs(x-x.mean())),raw=True)
    return (tp-mean)/(0.015*dev.replace(0,np.nan))

def roc(close: pd.Series, period=12) -> pd.Series:
    """Rate of change as a fractional return."""
    return close.pct_change(period)

def williams_r(df: pd.DataFrame, period=14) -> pd.Series:
    """Williams percent R."""
    lo=df.low.rolling(period,min_periods=period).min(); hi=df.high.rolling(period,min_periods=period).max(); return -100*(hi-df.close)/(hi-lo).replace(0,np.nan)

def mfi(df: pd.DataFrame, period=14) -> pd.Series:
    """Money Flow Index from positive and negative raw money flow."""
    tp=(df.high+df.low+df.close)/3; flow=tp*df.volume; sign=tp.diff().fillna(0); pos=flow.where(sign>0,0).rolling(period,min_periods=period).sum(); neg=flow.where(sign<0,0).rolling(period,min_periods=period).sum(); return (100-100/(1+pos/neg.replace(0,np.nan))).where(neg.ne(0),100.0)

def adx_dmi(df: pd.DataFrame, period=14) -> pd.DataFrame:
    """Wilder +DI, -DI and ADX."""
    tr=wilders(true_range(df),period); up=df.high.diff(); down=-df.low.diff(); plus=wilders(up.where((up>down)&(up>0),0).fillna(0),period); minus=wilders(down.where((down>up)&(down>0),0).fillna(0),period); p=100*plus/tr; m=100*minus/tr; dx=100*(p-m).abs()/(p+m); return pd.DataFrame({'plus_di':p,'minus_di':m,'adx':wilders(dx.fillna(0),period)})

def parabolic_sar(df: pd.DataFrame, step=.02, maximum=.2) -> pd.Series:
    """Parabolic SAR with explicit trend reversal and acceleration-factor loop."""
    n=len(df); out=np.full(n,np.nan); 
    if not n: return pd.Series(out,index=df.index)
    rising=True; out[0]=df.low.iloc[0]; extreme=df.high.iloc[0]; af=step
    for i in range(1,n):
        sar=out[i-1]+af*(extreme-out[i-1])
        if rising:
            sar=min(sar,df.low.iloc[i-1],df.low.iloc[i-2] if i>1 else df.low.iloc[i-1])
            if df.low.iloc[i]<sar: rising=False; sar=extreme; extreme=df.low.iloc[i]; af=step
            elif df.high.iloc[i]>extreme: extreme=df.high.iloc[i]; af=min(maximum,af+step)
        else:
            sar=max(sar,df.high.iloc[i-1],df.high.iloc[i-2] if i>1 else df.high.iloc[i-1])
            if df.high.iloc[i]>sar: rising=True; sar=extreme; extreme=df.high.iloc[i]; af=step
            elif df.low.iloc[i]<extreme: extreme=df.low.iloc[i]; af=min(maximum,af+step)
        out[i]=sar
    return pd.Series(out,index=df.index,name='psar')

def bollinger(close: pd.Series, period=20, deviations=2) -> pd.DataFrame:
    """Bollinger middle, upper and lower bands."""
    mid=sma(close,period); sd=close.rolling(period,min_periods=period).std(); return pd.DataFrame({'bollinger_mid':mid,'bollinger_upper':mid+deviations*sd,'bollinger_lower':mid-deviations*sd})

def keltner(df: pd.DataFrame, period=20, multiplier=2) -> pd.DataFrame:
    """Keltner channel using EMA center and Wilder ATR."""
    mid=ema(df.close,period); a=atr(df,period); return pd.DataFrame({'keltner_mid':mid,'keltner_upper':mid+multiplier*a,'keltner_lower':mid-multiplier*a})

def donchian(df: pd.DataFrame, period=20) -> pd.DataFrame:
    """Causal Donchian high, low and midpoint channels."""
    hi=df.high.rolling(period,min_periods=period).max(); lo=df.low.rolling(period,min_periods=period).min(); return pd.DataFrame({'donchian_upper':hi,'donchian_lower':lo,'donchian_mid':(hi+lo)/2})

def volume_indicators(df: pd.DataFrame, period=20) -> pd.DataFrame:
    """OBV, VWAP, accumulation/distribution, CMF and volume z-score."""
    d=df.close.diff(); obv=(np.sign(d).fillna(0)*df.volume).cumsum(); vwap=(df.close*df.volume).cumsum()/df.volume.cumsum().replace(0,np.nan); mfm=(2*df.close-df.low-df.high)/(df.high-df.low).replace(0,np.nan); ad=(mfm*df.volume).cumsum(); cmf=(mfm*df.volume).rolling(period,min_periods=period).sum()/df.volume.rolling(period,min_periods=period).sum().replace(0,np.nan); z=(df.volume-df.volume.rolling(period,min_periods=period).mean())/df.volume.rolling(period,min_periods=period).std(); return pd.DataFrame({'obv':obv,'vwap':vwap,'ad_line':ad,'cmf':cmf,'volume_zscore':z})

def ichimoku(df: pd.DataFrame) -> pd.DataFrame:
    """Ichimoku lines with spans explicitly displaced 26 rows forward."""
    conv=(df.high.rolling(9,min_periods=9).max()+df.low.rolling(9,min_periods=9).min())/2; base=(df.high.rolling(26,min_periods=26).max()+df.low.rolling(26,min_periods=26).min())/2; a=((conv+base)/2).shift(26); b=((df.high.rolling(52,min_periods=52).max()+df.low.rolling(52,min_periods=52).min())/2).shift(26); return pd.DataFrame({'ichimoku_conversion':conv,'ichimoku_base':base,'ichimoku_span_a':a,'ichimoku_span_b':b})

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute and concatenate the complete causal Phase 2 indicator suite."""
    x=df.copy(); out=x.copy(); close=x.close.astype(float)
    for p in (5,10,20,50,200): out[f'sma_{p}']=sma(close,p); out[f'ema_{p}']=ema(close,p); out[f'wma_{p}']=wma(close,p); out[f'rolling_std_{p}']=close.rolling(p,min_periods=p).std()
    parts=[macd(close),stochastic(x),pd.DataFrame({'rsi':rsi(close)}),pd.DataFrame({'cci':cci(x),'roc':roc(close),'williams_r':williams_r(x),'mfi':mfi(x),'atr_14':atr(x)}),adx_dmi(x),pd.DataFrame({'psar':parabolic_sar(x)}),bollinger(close),keltner(x),donchian(x),volume_indicators(x),ichimoku(x)]
    return pd.concat([out,*parts],axis=1).replace([np.inf,-np.inf],np.nan)
