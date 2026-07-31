"""Small event-driven, cost-aware backtesting kernel."""
from dataclasses import dataclass
import numpy as np
from models.baseline import evaluate_returns, Metrics
@dataclass(frozen=True)
class BacktestResult:
    metrics: Metrics
    equity: np.ndarray

def run(prices, signals, fee_bps=1., slippage_bps=2.):
    p=np.asarray(prices,float); s=np.asarray(signals,float); n=min(len(p),len(s))
    returns=np.zeros(n); asset=np.divide(p[1:n],p[:n-1],out=np.ones(n-1),where=p[:n-1]!=0)-1
    turnover=np.abs(np.diff(np.r_[0,s[:n-1]])); returns[1:]=s[:n-1]*asset-turnover*(fee_bps+slippage_bps)/10000
    equity=np.cumprod(1+returns); return BacktestResult(evaluate_returns(returns),equity)
