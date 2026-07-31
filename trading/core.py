"""Paper-first broker facade. Public placement always routes through RiskGateway.

Phase 8: the real broker lives in ``trading/paper_broker.py`` (fills priced
through the shared ``backtest.fill_engine.price_fill`` core, fees, positions,
realized P&L including entry fees, 30s duplicate window and 10 orders/min
idempotency caps).

Phase 10: the unified adapter surface lives in ``trading/broker_base.py``
(ABC + retry wrapper + live gate) with paper and Alpaca adapters. This module
re-exports both so Phase-7/8 import paths keep working and there is exactly
ONE paper-broker implementation plus ONE adapter contract in the repository.
"""
from __future__ import annotations

from trading.broker_base import (  # noqa: F401
    AccountSnapshot,
    BrokerAdapter,
    BrokerError,
    LiveGateDenied,
    LiveGateEvidence,
    LiveGateResult,
    OrderResult,
    PositionSnapshot,
    RetryableBrokerError,
    TerminalBrokerError,
    build_broker,
    evaluate_live_gate,
    with_retry,
)
from trading.paper_broker import Broker, Order, PaperBroker  # noqa: F401
