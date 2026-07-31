"""Phase 8 paper broker: real fills, fees, positions, realized P&L including
entry fees, and the idempotency caps (30s duplicate window + 10 orders/min).

All fills are priced through the shared ``backtest.fill_engine.price_fill``
core — the identity test below proves the broker reuses the backtester's
pricing path instead of duplicating it.
"""
import pytest

import backtest.fill_engine as fill_engine
import trading.paper_broker as paper_broker_mod
from risk.position_limits import PortfolioSnapshot
from trading.order_types import Order, OrderState, OrderType
from trading.paper_broker import PaperBroker


def snapshot(**kwargs):
    return PortfolioSnapshot(equity=100000, cash=100000, **kwargs)


# ----------------------------------------------------------------------
# Shared fill-pricing path (one core, no divergent duplicate)
# ----------------------------------------------------------------------

def test_fills_reuse_the_single_shared_pricing_core():
    """The broker must price fills through backtest.fill_engine.price_fill —
    the same function the backtester uses — never a private copy."""
    assert paper_broker_mod.price_fill is fill_engine.price_fill


def test_market_order_fills_with_fee_and_cash_impact():
    broker = PaperBroker(clock=lambda: 0.0, fee_bps=10.0, slippage_bps=0.0)
    order = broker.place_order(Order("AAPL", "buy", 10, price=100.0))
    assert order.state is OrderState.FILLED
    assert order.filled_quantity == 10.0
    assert order.filled_price == 100.0
    fee = 10 * 100.0 * 10 / 10000.0   # 10 bps of notional
    assert broker.cash == pytest.approx(100000.0 - 1000.0 - fee)
    assert broker.positions == {"AAPL": 10.0}
    assert len(broker.fills) == 1
    assert broker.fills[0].fee == pytest.approx(fee)


def test_market_sell_reduces_position_and_adds_cash():
    broker = PaperBroker(clock=lambda: 0.0, fee_bps=0.0, slippage_bps=0.0)
    broker.place_order(Order("AAPL", "buy", 10, price=100.0))
    sell = broker.place_order(Order("AAPL", "sell", 4, price=102.0))
    assert sell.state is OrderState.FILLED
    assert broker.positions == {"AAPL": 6.0}
    assert broker.cash == pytest.approx(100000.0 - 10 * 100.0 + 4 * 102.0)


def test_limit_order_rests_then_fills_on_mark():
    broker = PaperBroker(clock=lambda: 0.0, fee_bps=10.0, slippage_bps=0.0)
    order = broker.place_order(Order("MSFT", "buy", 5, type=OrderType.LIMIT,
                                     limit_price=90.0, price=100.0))
    assert order.state is OrderState.SUBMITTED
    assert order.filled_quantity == 0.0
    broker.mark_price("MSFT", 91.0, high=92.0, low=88.0)
    assert order.state is OrderState.FILLED
    assert order.filled_price == 90.0
    assert broker.positions == {"MSFT": 5.0}


def test_stop_order_triggers_on_mark_cross():
    broker = PaperBroker(clock=lambda: 0.0, fee_bps=0.0, slippage_bps=0.0)
    broker.place_order(Order("NVDA", "buy", 10, price=100.0))
    stop = broker.place_order(Order("NVDA", "sell", 10, type=OrderType.STOP,
                                    stop_price=95.0, price=100.0))
    assert stop.state is OrderState.SUBMITTED
    broker.mark_price("NVDA", 94.0)
    assert stop.state is OrderState.FILLED
    assert broker.positions == {}


def test_trailing_stop_ratchets_in_broker_and_triggers():
    broker = PaperBroker(clock=lambda: 0.0, fee_bps=0.0, slippage_bps=0.0)
    broker.place_order(Order("NVDA", "buy", 10, price=100.0))
    trail = broker.place_order(Order("NVDA", "sell", 10, type=OrderType.TRAILING_STOP,
                                     trail_pct=0.02, price=100.0))
    broker.mark_price("NVDA", 103.0)
    broker.mark_price("NVDA", 104.0)
    broker.mark_price("NVDA", 103.5)          # pullback: anchor must hold 104
    assert trail.trail_anchor == 104.0
    assert trail.state is OrderState.SUBMITTED
    broker.mark_price("NVDA", 101.8, low=101.8)  # 104 * 0.98 = 101.92
    assert trail.state is OrderState.FILLED
    assert broker.positions == {}


# ----------------------------------------------------------------------
# Realized P&L including entry fees (via db.close_paper_trade)
# ----------------------------------------------------------------------

def test_realized_pnl_long_includes_entry_and_exit_fees(mem_db):
    broker = PaperBroker(db=mem_db, clock=lambda: 0.0, fee_bps=10.0, slippage_bps=0.0)
    broker.place_order(Order("AAPL", "buy", 10, price=100.0))   # fee 1.00
    broker.place_order(Order("AAPL", "sell", 10, price=105.0))  # fee 1.05
    expected = 10 * (105.0 - 100.0) - 1.00 - 1.05               # 47.95
    assert broker.realized_pnl == pytest.approx(expected)
    assert broker.positions == {}
    trades = mem_db.fetch_paper_trades(status="CLOSED")
    assert len(trades) == 1
    assert float(trades.iloc[0]["realized_pnl"]) == pytest.approx(expected)
    assert float(trades.iloc[0]["fees"]) == pytest.approx(1.00 + 1.05)


def test_realized_pnl_short_includes_entry_fees(mem_db):
    broker = PaperBroker(db=mem_db, clock=lambda: 0.0, fee_bps=10.0, slippage_bps=0.0)
    broker.place_order(Order("TSLA", "sell_short", 5, price=200.0))  # fee 1.00
    broker.place_order(Order("TSLA", "buy", 5, price=190.0))          # fee 0.95
    expected = 5 * (200.0 - 190.0) - 1.00 - 0.95                     # 48.05
    assert broker.realized_pnl == pytest.approx(expected)
    trades = mem_db.fetch_paper_trades(status="CLOSED")
    assert float(trades.iloc[0]["realized_pnl"]) == pytest.approx(expected)


def test_partial_close_allocates_entry_fees_proportionally(mem_db):
    broker = PaperBroker(db=mem_db, clock=lambda: 0.0, fee_bps=10.0, slippage_bps=0.0)
    broker.place_order(Order("AAPL", "buy", 10, price=100.0))   # entry fee 1.00
    broker.place_order(Order("AAPL", "sell", 4, price=110.0))   # exit fee 0.44
    expected = 4 * (110.0 - 100.0) - 1.00 * (4 / 10) - 0.44     # 39.16
    assert broker.realized_pnl == pytest.approx(expected)
    assert broker.positions == {"AAPL": 6.0}
    closed = mem_db.fetch_paper_trades(status="CLOSED")
    assert len(closed) == 1
    assert float(closed.iloc[0]["realized_pnl"]) == pytest.approx(expected)
    assert float(closed.iloc[0]["quantity"]) == 4.0
    open_trades = mem_db.fetch_open_paper_trades()
    assert float(open_trades.iloc[0]["quantity"]) == 6.0
    assert float(open_trades.iloc[0]["fees"]) == pytest.approx(0.6)


def test_db_paper_trade_rows_round_trip_through_broker(mem_db):
    broker = PaperBroker(db=mem_db, clock=lambda: 0.0, fee_bps=0.0, slippage_bps=0.0)
    broker.place_order(Order("AAPL", "buy", 10, price=100.0, client_id="o1"))
    broker.place_order(Order("AAPL", "sell", 10, price=110.0, client_id="o2"))
    rows = mem_db.fetch_paper_trades()
    assert len(rows) == 1
    assert rows.iloc[0]["status"] == "CLOSED"
    assert float(rows.iloc[0]["entry_price"]) == 100.0
    assert float(rows.iloc[0]["exit_price"]) == 110.0


def test_in_memory_realized_matches_db_math_with_slippage(mem_db):
    """The no-DB path and the DB path must produce identical realized P&L."""
    a = PaperBroker(db=None, clock=lambda: 0.0, fee_bps=10.0, slippage_bps=10.0,
                    rng=__import__("numpy").random.default_rng(7))
    b = PaperBroker(db=mem_db, clock=lambda: 0.0, fee_bps=10.0, slippage_bps=10.0,
                    rng=__import__("numpy").random.default_rng(7))
    for broker in (a, b):
        broker.place_order(Order("AAPL", "buy", 10, price=100.0))
        broker.place_order(Order("AAPL", "sell", 10, price=105.0))
    assert a.realized_pnl == pytest.approx(b.realized_pnl)


# ----------------------------------------------------------------------
# Idempotent submission: 30s duplicate window
# ----------------------------------------------------------------------

def test_duplicate_order_window_blocks_resubmission():
    clock = {"t": 100.0}

    def tick():
        return clock["t"]

    broker = PaperBroker(clock=tick, fee_bps=0.0, slippage_bps=0.0)
    first = broker.place_order(Order("AAPL", "buy", 1, price=100.0))
    assert first.state is OrderState.FILLED
    clock["t"] = 110.0  # 10s later: inside the 30s window
    dup = broker.place_order(Order("AAPL", "buy", 1, price=100.0))
    assert dup.state is OrderState.REJECTED
    assert "duplicate_order:within_30s_window" in dup.reject_reason
    assert broker.positions == {"AAPL": 1.0}   # no double execution


def test_duplicate_window_expiry_allows_resubmission():
    clock = {"t": 100.0}

    def tick():
        return clock["t"]

    broker = PaperBroker(clock=tick, fee_bps=0.0, slippage_bps=0.0)
    broker.place_order(Order("AAPL", "buy", 1, price=100.0))
    clock["t"] = 131.0  # 31s later: outside the 30s window
    again = broker.place_order(Order("AAPL", "buy", 1, price=100.0))
    assert again.state is OrderState.FILLED
    assert broker.positions == {"AAPL": 2.0}


def test_same_client_id_is_idempotent_within_window():
    clock = {"t": 0.0}

    def tick():
        return clock["t"]

    broker = PaperBroker(clock=tick, fee_bps=0.0, slippage_bps=0.0)
    first = broker.place_order(Order("AAPL", "buy", 10, price=100.0, client_id="ord-1"))
    assert first.state is OrderState.FILLED
    clock["t"] = 5.0
    retry = broker.place_order(Order("AAPL", "buy", 10, price=100.0, client_id="ord-1"))
    assert retry.state is OrderState.REJECTED
    assert "duplicate" in retry.reject_reason   # fingerprint or client-id duplicate
    assert broker.positions == {"AAPL": 10.0}   # no double execution


def test_duplicate_window_does_not_block_different_orders():
    clock = {"t": 0.0}

    def tick():
        return clock["t"]

    broker = PaperBroker(clock=tick, fee_bps=0.0, slippage_bps=0.0)
    broker.place_order(Order("AAPL", "buy", 1, price=100.0))
    other = broker.place_order(Order("AAPL", "buy", 1, price=101.0))  # different price
    assert other.state is OrderState.FILLED


# ----------------------------------------------------------------------
# Idempotent submission: 10 orders/min cap
# ----------------------------------------------------------------------

def test_order_rate_cap_10_per_minute_fires():
    clock = {"t": 0.0}

    def tick():
        return clock["t"]

    broker = PaperBroker(clock=tick, fee_bps=0.0, slippage_bps=0.0)
    # A fresh, position-less portfolio snapshot per order keeps the rate-cap
    # test isolated from the gateway's max_open_positions limit.
    symbols = [chr(ord("A") + i) for i in range(10)]
    for i, sym in enumerate(symbols):
        order = broker.place_order(Order(sym, "buy", 1, price=100.0), snapshot())
        assert order.state is OrderState.FILLED, f"{sym} should be accepted"
    clock["t"] = 1.0
    eleventh = broker.place_order(Order("K", "buy", 1, price=100.0), snapshot())
    assert eleventh.state is OrderState.REJECTED
    assert "order_rate:10/min_exceeded" in eleventh.reject_reason


def test_order_rate_cap_is_a_rolling_window():
    clock = {"t": 0.0}

    def tick():
        return clock["t"]

    broker = PaperBroker(clock=tick, fee_bps=0.0, slippage_bps=0.0)
    symbols = [chr(ord("A") + i) for i in range(10)]
    for sym in symbols:
        broker.place_order(Order(sym, "buy", 1, price=100.0), snapshot())
    clock["t"] = 61.0  # first submission is now older than 60s
    again = broker.place_order(Order("K", "buy", 1, price=100.0), snapshot())
    assert again.state is OrderState.FILLED


# ----------------------------------------------------------------------
# Gateway-only transmission (regression)
# ----------------------------------------------------------------------

class CountingBroker(PaperBroker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.submit_calls = 0

    def submit(self, request):
        self.submit_calls += 1
        return super().submit(request)


def test_gateway_denial_blocks_low_level_submit():
    broker = CountingBroker(clock=lambda: 0.0, fee_bps=0.0, slippage_bps=0.0)
    with pytest.raises(PermissionError, match="denied"):
        broker.place_order(Order("AAPL", "buy", 1, price=100.0),
                           snapshot(breaker_state="HALTED"))
    assert broker.submit_calls == 0      # submit never reached without the gateway
    assert broker.orders == []           # nothing transmitted


def test_place_order_routes_through_gateway_transmit():
    calls = {"transmits": 0}
    gateway = paper_broker_mod.RiskGateway()
    original = gateway.transmit

    def counting_transmit(broker, request, portfolio):
        calls["transmits"] += 1
        return original(broker, request, portfolio)

    gateway.transmit = counting_transmit
    broker = PaperBroker(gateway=gateway, clock=lambda: 0.0,
                         fee_bps=0.0, slippage_bps=0.0)
    broker.place_order(Order("AAPL", "buy", 1, price=100.0))
    assert calls["transmits"] == 1


# ----------------------------------------------------------------------
# OCO + bracket end-to-end in the broker
# ----------------------------------------------------------------------

def test_oco_one_cancels_other_via_broker():
    broker = PaperBroker(clock=lambda: 0.0, fee_bps=0.0, slippage_bps=0.0)
    broker.place_order(Order("AAPL", "buy", 10, price=100.0))
    tp, sl = broker.place_oco([
        Order("AAPL", "sell", 10, type=OrderType.LIMIT, limit_price=110.0, price=100.0),
        Order("AAPL", "sell", 10, type=OrderType.STOP, stop_price=95.0, price=100.0),
    ])
    assert tp.state is OrderState.SUBMITTED
    assert sl.state is OrderState.SUBMITTED
    broker.mark_price("AAPL", 111.0)   # take-profit leg fills
    assert tp.state is OrderState.FILLED
    assert sl.state is OrderState.CANCELLED
    assert sl.cancel_reason == "oco:one_cancels_other"
    assert broker.positions == {}


def test_oco_same_bar_cross_fills_only_first_leg():
    broker = PaperBroker(clock=lambda: 0.0, fee_bps=0.0, slippage_bps=0.0)
    broker.place_order(Order("AAPL", "buy", 10, price=100.0))
    tp, sl = broker.place_oco([
        Order("AAPL", "sell", 10, type=OrderType.LIMIT, limit_price=110.0, price=100.0),
        Order("AAPL", "sell", 10, type=OrderType.STOP, stop_price=95.0, price=100.0),
    ])
    # One bar trades through BOTH levels: exactly one leg may fill.
    broker.mark_price("AAPL", 112.0, high=115.0, low=90.0)
    filled = [o for o in (tp, sl) if o.state is OrderState.FILLED]
    cancelled = [o for o in (tp, sl) if o.state is OrderState.CANCELLED]
    assert len(filled) == 1
    assert len(cancelled) == 1
    assert broker.positions == {}


def test_bracket_arms_children_on_entry_fill_and_cancels_twin():
    broker = PaperBroker(clock=lambda: 0.0, fee_bps=0.0, slippage_bps=0.0)
    entry, tp, sl = broker.place_bracket(
        Order("MSFT", "buy", 10, price=100.0), take_profit=110.0, stop_loss=95.0)
    assert entry.state is OrderState.FILLED
    assert tp is not None and sl is not None
    assert tp.state is OrderState.SUBMITTED
    assert sl.state is OrderState.SUBMITTED
    broker.mark_price("MSFT", 94.0)   # stop-loss leg fills
    assert sl.state is OrderState.FILLED
    assert tp.state is OrderState.CANCELLED
    assert tp.cancel_reason == "bracket:twin_filled"
    assert broker.positions == {}


def test_bracket_with_resting_limit_entry_arms_children_later():
    broker = PaperBroker(clock=lambda: 0.0, fee_bps=0.0, slippage_bps=0.0)
    entry, tp, sl = broker.place_bracket(
        Order("MSFT", "buy", 10, type=OrderType.LIMIT, limit_price=99.0, price=100.0),
        take_profit=110.0, stop_loss=95.0)
    assert entry.state is OrderState.SUBMITTED
    assert tp is None and sl is None          # children not yet transmitted
    broker.mark_price("MSFT", 98.0, low=98.0)  # entry limit fills
    assert entry.state is OrderState.FILLED
    children = [o for o in broker.orders if o.parent_id == entry.client_id]
    assert len(children) == 2
    assert all(c.state is OrderState.SUBMITTED for c in children)
    broker.mark_price("MSFT", 111.0)          # take-profit fills, stop cancels
    tp_child = next(c for c in children if c.type is OrderType.LIMIT)
    sl_child = next(c for c in children if c.type is OrderType.STOP)
    assert tp_child.state is OrderState.FILLED
    assert sl_child.state is OrderState.CANCELLED
    assert sl_child.cancel_reason == "bracket:twin_filled"
    assert broker.positions == {}
    assert broker.realized_pnl == pytest.approx(10 * (110.0 - 99.0))


# ----------------------------------------------------------------------
# Miscellaneous ledger behavior
# ----------------------------------------------------------------------

def test_short_position_round_trip_cash():
    broker = PaperBroker(clock=lambda: 0.0, fee_bps=0.0, slippage_bps=0.0)
    short = broker.place_order(Order("TSLA", "sell_short", 5, price=200.0))
    assert short.state is OrderState.FILLED
    assert broker.positions == {"TSLA": -5.0}
    assert broker.cash == pytest.approx(100000.0 + 5 * 200.0)
    cover = broker.place_order(Order("TSLA", "buy", 5, price=190.0))
    assert cover.state is OrderState.FILLED
    assert broker.positions == {}
    assert broker.cash == pytest.approx(100000.0 + 5 * 200.0 - 5 * 190.0)
    assert broker.realized_pnl == pytest.approx(50.0)


def test_non_positive_quantity_rejected():
    """The gateway denies quantity<=0 before any transmission occurs."""
    broker = PaperBroker(clock=lambda: 0.0)
    with pytest.raises(PermissionError, match="quantity"):
        broker.place_order(Order("AAPL", "buy", 0, price=100.0))
    assert broker.orders == []
