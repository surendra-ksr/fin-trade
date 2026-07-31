"""Paper-first broker facade. Public placement always routes through RiskGateway.

Phase 8: the real broker lives in ``trading/paper_broker.py`` (fills priced
through the shared ``backtest.fill_engine.price_fill`` core, fees, positions,
realized P&L including entry fees, 30s duplicate window and 10 orders/min
idempotency caps). This module re-exports it unchanged so Phase-7 import
paths (`from trading.core import Order, PaperBroker`) keep working and there
is exactly ONE broker implementation in the repository.
"""
from __future__ import annotations

from trading.paper_broker import Broker, Order, PaperBroker  # noqa: F401
