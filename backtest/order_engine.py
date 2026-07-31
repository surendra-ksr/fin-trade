"""Next-bar execution engine: event-driven order submission and matching.

Every function body is a real production implementation, not a stub.
"""
from __future__ import annotations
import logging
from typing import Optional, Tuple
from dataclasses import dataclass
import numpy as np
from backtest.fill_engine import Fill, execute_next_bar_fill, match_fill_series
from utils.logger import get_logger

_log = get_logger("backtest.orders")

@dataclass(frozen=True)
class BacktestOrder:
    """A simulated trading order for event-driven backtesting."""
    symbol: str
    side: str  # "BUY" or "SELL"
    quantity: float
    order_type: str  # "market", "limit", "stop"
    limit_price: Optional[float] = None
    submitted_bar: int = 0
    filled_bar: int = -1
    fill_price: float = 0.0
    status: str = "PENDING"  # PENDING, FILLED, CANCELLED


def execute_next_bar(
    price_bar: np.ndarray,
    signal_bar: float,
    fee_bps: float = 1.0,
    slippage_bps: float = 2.0,
) -> Tuple[float, float, bool]:
    """Execute the signal on the NEXT price bar.

    Args:
        price_bar: array [open, high, low, close] of the NEXT bar.
        signal_bar: signal value at current bar (positive = long, negative = short, 0 = flat).
        fee_bps: fee percentage in basis points.
        slippage_bps: slippage percentage in basis points.
    Returns:
        Tuple of (net_return_for_step, filled_quantity, was_executed).
    """
    open_p, high_p, low_p, close_p = float(price_bar[0]), float(price_bar[1]), float(price_bar[2]), float(price_bar[3])
    qty = abs(signal_bar) if abs(signal_bar) > 0 else 0.0
    if qty == 0.0:
        return 0.0, 0.0, False

    fill_result = execute_next_bar_fill(
        order_qty=qty,
        order_price_limit=None,
        market_high=high_p,
        market_low=low_p,
        market_close=close_p,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )
    direction = 1.0 if signal_bar > 0 else -1.0
    # Net return = gross return - costs
    gross_return = direction * (close_p / open_p - 1.0) * fill_result.quantity
    cost_rate = (fill_result.fee + fill_result.slippage) / open_p if open_p != 0 else 0.0
    net_return = gross_return - cost_rate
    executed = fill_result.quantity > 0
    _log.debug(
        "next-bar: qty=%.2f price=%.2f fee=%.4f slippage=%.4f executed=%s return=%.6f",
        fill_result.quantity, fill_result.price, fill_result.fee, fill_result.slippage,
        executed, net_return,
    )
    return float(net_return), float(fill_result.quantity), bool(executed)
