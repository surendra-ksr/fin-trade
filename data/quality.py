"""Reusable data-quality checks for market and feature datasets."""
from __future__ import annotations
from dataclasses import dataclass, field
import pandas as pd
import numpy as np

@dataclass
class QualityReport:
    rows: int
    valid: bool
    issues: list[str] = field(default_factory=list)
    null_counts: dict[str,int] = field(default_factory=dict)
    duplicate_rows: int = 0
    outlier_rows: int = 0

class DataQuality:
    required=('open','high','low','close','volume')
    def inspect(self, frame: pd.DataFrame, *, max_return_sigma=8.0) -> QualityReport:
        issues=[]; nulls={c:int(frame[c].isna().sum()) for c in frame.columns if frame[c].isna().any()}
        missing=[c for c in self.required if c not in frame.columns]
        if missing: issues.append('missing columns: '+','.join(missing))
        dup=int(frame.index.duplicated().sum()); dup and issues.append(f'duplicate timestamps: {dup}')
        if not missing and len(frame):
            bad=((frame.high<frame.low)|(frame.high<frame.close)|(frame.high<frame.open)|(frame.low>frame.close)|(frame.low>frame.open)).sum()
            bad and issues.append(f'invalid OHLC relationships: {int(bad)}')
            zero=int((frame.volume<=0).sum()); zero and issues.append(f'non-positive volume: {zero}')
            ret=frame.close.pct_change().replace([np.inf,-np.inf],np.nan).dropna()
            out=int((ret-ret.median()).abs().gt(max_return_sigma*ret.std()).sum()) if len(ret)>1 else 0
            out and issues.append(f'return outliers: {out}')
        issues.extend(f'null values: {c}={n}' for c,n in nulls.items())
        return QualityReport(len(frame), not issues, issues, nulls, dup, locals().get('out',0))
    def clean(self, frame):
        return frame[~frame.index.duplicated(keep='last')].sort_index().copy()

__all__=['DataQuality','QualityReport']
