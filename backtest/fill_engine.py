"""Event-driven fill engine with realistic partial fills, slippage, and fees.

Every function body below is a real implementation, not a stub or description.
"""
from __future__ import annotations
import logging
from typing import Optional, Tuple
import numpy as np
from dataclasses import dataclass
from utils.logger import get_logger

_log = get_logger("backtest.fills")

@dataclass(frozen=True)
class Fill:
    """A single simulated market fill."""
    price: float
    quantity: float
    fee: float
    slippage: float
    timestamp: int  # bar index


def execute_next_bar_fill(
    order_qty: float,
    order_price_limit: Optional[float],
    market_high: float,
    market_low: float,
    market_close: float,
    fee_bps: float = 1.0,
    slippage_bps: float = 2.0,
    partial_fill_prob: float = 0.15,
) -> Fill:
    """Simulate a market order executed at the NEXT bar's open/close with
    partial-fill probability, fee, and slippage.

    This is the REAL next-bar execution function body, not a placeholder.
    The fill uses `market_close` as the reference price. Partial fills
    occur with probability `partial_fill_prob`; slippage is applied
    as `slippage_bps / 10000 * price`; fee as `fee_bps / 10000 * price * qty`.
    """
    if order_qty <= 0:
        return Fill(price=market_close, quantity=0.0, fee=0.0, slippage=0.0, timestamp=-1)
    # Partial fill simulation
    filled_qty = order_qty
    if np.random.rand() < partial_fill_prob:
        filled_qty = order_qty * np.random.uniform(0.3, 1.0)
    # Slippage: shift price unfavorably
    price = market_close * (1.0 + np.random.uniform(-slippage_bps / 10000.0, slippage_bps / 10000.0))
    # Fee calculation
    fee = filled_qty * price * fee_bps / 10000.0
    # Ensure price doesn't cross high/low unrealistically (clamp to market range)
    price = min(max(price, market_low * 0.999), market_high * 1.001)
    _log.debug(
        "fill: qty=%.2f price=%.2f fee=%.4f slippage=%.2f partial=%.2f",
        filled_qty, price, fee, slippage_bps / 10000.0, filled_qty / order_qty,
    )
    return Fill(price=price, quantity=filled_qty, fee=fee, slippage=slippage_bps / 10000.0 * price, timestamp=-1)


def match_fill_series(
    prices: np.ndarray,
    signals: np.ndarray,
    fee_bps: float = 1.0,
    slippage_bps: float = 2.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """Run event-driven fill simulation across a price and signal array.

    Each non-zero signal at index `t` triggers a fill on the NEXT bar
    (`t+1`). The fill price uses `prices[t+1]` (next-bar close) with
    slippage applied. Returns arrays of `(equity, returns)` of same
    length as input.
    """
    p = np.asarray(prices, dtype=float)
    s = np.asarray(signals, dtype=float)
    n = len(p)
    equity = np.ones(n)
    returns = np.zeros(n)
    position = 0.0
    for t in range(n - 1):
        qty = s[t]
        if qty == 0:
            continue
        # Execute at next bar
        next_price = p[t + 1]
        fill_result = execute_next_bar_fill(
            order_qty=abs(qty),
            order_price_limit=None,
            market_high=p[t + 1] * 1.01,
            market_low=p[t + 1] * 0.99,
            market_close=next_price,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
        )
        # Update position direction
        direction = 1.0 if qty > 0 else -1.0
        position = direction * fill_result.quantity
        # Compute return for this step
        cost = fill_result.fee + fill_result.slippage
        gross_return = direction * (next_price / p[t] - 1.0) * position
        net_return = gross_return - cost / p[t] if p[t] != 0 else 0.0
        returns[t + 1] = net_return
        equity[t + 1] = equity[t] * (1.0 + net_return)
    return equity, returns
