"""Leakage-safe baseline models and walk-forward evaluation."""
from dataclasses import dataclass
import numpy as np

@dataclass(frozen=True)
class Metrics:
    total_return: float
    sharpe: float
    max_drawdown: float
    observations: int

def evaluate_returns(returns):
    x=np.asarray(returns,dtype=float); x=x[np.isfinite(x)]
    if not len(x): return Metrics(0.,0.,0.,0)
    equity=np.cumprod(1+x); peak=np.maximum.accumulate(equity)
    dd=float(np.min(equity/peak-1)); sharpe=float(np.sqrt(252)*x.mean()/x.std()) if x.std()>0 else 0.
    return Metrics(float(equity[-1]-1),sharpe,dd,len(x))

class MomentumBaseline:
    def __init__(self, lookback=20): self.lookback=lookback
    def predict(self, close):
        x=np.asarray(close,dtype=float); out=np.zeros(len(x)); out[self.lookback:]=np.sign(x[self.lookback:]/x[:-self.lookback]-1)
        return out
