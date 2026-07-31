"""Causal multi-market feature joins."""
from __future__ import annotations
import pandas as pd
from .indicators import compute_indicators

def engineer(price: pd.DataFrame, markets: dict[str,pd.DataFrame]|None=None, macro:pd.DataFrame|None=None) -> pd.DataFrame:
    """Join only backward observations using ``merge_asof`` and causal features."""
    out=compute_indicators(price.sort_index()); close=out.close
    out['return_1']=close.pct_change(); out['return_5']=close.pct_change(5)
    for lag in (1,5,20): out[f'lag_{lag}']=close.shift(lag)
    if markets:
        for name,frame in markets.items():
            z=frame.sort_index()[['close']].rename(columns={'close':f'{name}_close'}); out=pd.merge_asof(out.sort_index(),z,left_index=True,right_index=True,direction='backward'); out[f'{name}_return']=out[f'{name}_close'].pct_change()
    if macro is not None: out=pd.merge_asof(out.sort_index(),macro.sort_index(),left_index=True,right_index=True,direction='backward')
    return out
