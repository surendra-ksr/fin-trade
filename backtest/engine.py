"""Small event-driven, cost-aware backtesting kernel (updated)."""
from dataclasses import dataclass
import numpy as np
from models.baseline import evaluate_returns, Metrics
from backtest.fill_engine import execute_next_bar_fill, match_fill_series

@dataclass(frozen=True)
class BacktestResult:
    metrics: Metrics
    equity: np.ndarray


def run(prices, signals, fee_bps=1., slippage_bps=2.):
    """Run event-driven backtest using next-bar fill simulation.

    This body integrates `match_fill_series` for realistic next-bar
    execution with fees and slippage.
    """
    p = np.asarray(prices, float)
    s = np.asarray(signals, float)
    equity, returns = match_fill_series(p, s, fee_bps=fee_bps, slippage_bps=slippage_bps)
    return BacktestResult(evaluate_returns(returns), equity)
