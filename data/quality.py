"""Market-data quality checks: duplicates, gaps, stale bars and jumps."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import timedelta
import numpy as np
import pandas as pd

@dataclass
class QualityReport:
    """Structured result of a quality inspection."""
    rows:int; valid:bool; issues:list[str]=field(default_factory=list); null_counts:dict[str,int]=field(default_factory=dict); duplicate_rows:int=0; outlier_rows:int=0

class DataQuality:
    """Deterministic quality checks with no network or global state."""
    required=('open','high','low','close','volume')
    def inspect(self, frame:pd.DataFrame, *, max_return_sigma:float=8.0, stale_after:timedelta|None=None, expected_interval:timedelta|None=None, corporate_jump:float=.20)->QualityReport:
        """Inspect OHLCV sanity, duplicate/gap/stale rows and corporate jumps."""
        issues=[]; nulls={c:int(frame[c].isna().sum()) for c in frame.columns if frame[c].isna().any()}; missing=[c for c in self.required if c not in frame.columns]
        if missing: issues.append('missing columns: '+','.join(missing))
        duplicate=int(frame.index.duplicated().sum()); duplicate and issues.append(f'duplicate timestamps: {duplicate}')
        out=0
        if not missing and len(frame):
            bad=((frame.high<frame.low)|(frame.high<frame.close)|(frame.high<frame.open)|(frame.low>frame.close)|(frame.low>frame.open)).sum(); bad and issues.append(f'invalid OHLC relationships: {int(bad)}')
            zero=int((frame.volume<=0).sum()); zero and issues.append(f'non-positive volume: {zero}')
            ret=frame.close.pct_change(); finite=ret.replace([np.inf,-np.inf],np.nan).dropna(); scale=finite.std(); out=int((finite-finite.median()).abs().gt(max_return_sigma*scale).sum()) if len(finite)>1 and scale else 0; out and issues.append(f'return outliers: {out}')
            jumps=int(ret.abs().gt(corporate_jump).sum()); jumps and issues.append(f'corporate-action jumps: {jumps}')
            if expected_interval is not None and len(frame)>1:
                gaps=int((frame.index.to_series().diff().dropna()>expected_interval).sum()); gaps and issues.append(f'gaps: {gaps}')
            if stale_after is not None and len(frame) and isinstance(frame.index,pd.DatetimeIndex):
                last=pd.Timestamp(frame.index[-1]); last=last.tz_localize('UTC') if last.tzinfo is None else last; age=pd.Timestamp.now(tz='UTC')-last; stale_after is not None and age>stale_after and issues.append(f'stale data: age={age}')
        issues.extend(f'null values: {c}={n}' for c,n in nulls.items())
        return QualityReport(len(frame),not issues,issues,nulls,duplicate,out)
    def clean(self, frame:pd.DataFrame)->pd.DataFrame:
        """Sort timestamps and retain the last duplicate row."""
        return frame[~frame.index.duplicated(keep='last')].sort_index().copy()

__all__=['DataQuality','QualityReport']
