"""Phase 8 order types: clean state machine + trigger-edge tests.

Behavioral tests on the REAL ``trading/order_types.py`` bodies: the full
market/limit/stop/stop-limit/trailing/OCO/bracket surface, gap-through stop
execution, the trailing ratchet contract, one-cancels-other sibling
cancellation, bracket child arming/twin cancellation, and the transition
table (legal moves only; illegal moves raise).
"""
import pytest

from trading.order_types import (
    TERMINAL_STATES,
    STATE_MACHINE,
    Bar,
    InvalidOrderError,
    InvalidTransitionError,
    Order,
    OrderState,
    OrderType,
    arm_bracket_children,
    cancel_bracket_twin,
    cancel_oco_siblings,
    evaluate_trigger,
    ratchet_trailing,
    transition,
)


def bar(high, low, close, open=0.0):
    return Bar(high=high, low=low, close=close, open=open)


# ----------------------------------------------------------------------
# State machine
# ----------------------------------------------------------------------

def test_state_machine_clean_lifecycle():
    """PENDING_NEW -> SUBMITTED -> TRIGGERED -> WORKING -> FILLED is legal."""
    order = Order("AAPL", "buy", 10)
    order.transition(OrderState.SUBMITTED)
    order.transition(OrderState.TRIGGERED)
    order.transition(OrderState.WORKING)
    order.transition(OrderState.FILLED)
    assert order.state is OrderState.FILLED


def test_state_machine_illegal_transitions_raise():
    with pytest.raises(InvalidTransitionError):
        Order("AAPL", "buy", 10).transition(OrderState.FILLED)  # pending -> filled
    order = Order("AAPL", "buy", 10)
    order.transition(OrderState.SUBMITTED)
    with pytest.raises(InvalidTransitionError):
        order.transition(OrderState.PENDING_NEW)  # no going back
    with pytest.raises(InvalidTransitionError):
        order.transition(OrderState.SUBMITTED)    # no self-transition


def test_terminal_states_accept_no_transitions():
    for terminal in (OrderState.FILLED, OrderState.CANCELLED,
                     OrderState.REJECTED, OrderState.EXPIRED):
        assert STATE_MACHINE[terminal] == frozenset()
        order = Order("AAPL", "buy", 10, state=terminal)
        assert order.is_terminal()
        for target in OrderState:
            if target is terminal:
                continue
            with pytest.raises(InvalidTransitionError):
                order.transition(target)


def test_every_state_has_transitions_entry():
    for state in OrderState:
        assert state in STATE_MACHINE, f"missing transition entry for {state}"
        for target in STATE_MACHINE[state]:
            assert state is not target, f"self transition {state}"
            assert target in OrderState


def test_transition_function_validates_both_ends():
    assert transition(OrderState.SUBMITTED, OrderState.FILLED) is OrderState.FILLED
    with pytest.raises(InvalidTransitionError):
        transition(OrderState.FILLED, OrderState.SUBMITTED)


# ----------------------------------------------------------------------
# Market / limit
# ----------------------------------------------------------------------

def test_market_order_fills_immediately_at_bar_price():
    order = Order("AAPL", "buy", 10, type=OrderType.MARKET)
    decision = evaluate_trigger(order, bar(101, 99, 100))
    assert decision.elect and decision.fill
    assert decision.fill_price == 100.0
    assert decision.state_after is OrderState.FILLED


def test_limit_buy_fills_when_low_touches_limit():
    order = Order("AAPL", "buy", 10, type=OrderType.LIMIT, limit_price=100.0)
    decision = evaluate_trigger(order, bar(101, 99.5, 100.5))
    assert decision.fill and decision.fill_price == 100.0


def test_limit_buy_rests_when_price_stays_above_limit():
    order = Order("AAPL", "buy", 10, type=OrderType.LIMIT, limit_price=100.0)
    decision = evaluate_trigger(order, bar(102, 100.5, 101.0))
    assert not decision.fill
    assert decision.state_after is OrderState.SUBMITTED


def test_limit_sell_fills_when_high_touches_limit():
    order = Order("AAPL", "sell", 10, type=OrderType.LIMIT, limit_price=110.0)
    decision = evaluate_trigger(order, bar(110.0, 108.0, 109.0))
    assert decision.fill and decision.fill_price == 110.0


def test_limit_sell_rests_when_price_stays_below_limit():
    order = Order("AAPL", "sell", 10, type=OrderType.LIMIT, limit_price=110.0)
    decision = evaluate_trigger(order, bar(109.0, 107.0, 108.0))
    assert not decision.fill


# ----------------------------------------------------------------------
# Stop orders + gap-through edge
# ----------------------------------------------------------------------

def test_stop_buy_triggers_on_high_cross():
    order = Order("AAPL", "buy", 10, type=OrderType.STOP, stop_price=105.0)
    decision = evaluate_trigger(order, bar(106.0, 103.0, 104.0, open=103.0))
    assert decision.fill
    assert decision.fill_price == 105.0  # no gap: fill at the stop


def test_stop_sell_triggers_on_low_cross():
    order = Order("AAPL", "sell", 10, type=OrderType.STOP, stop_price=95.0)
    decision = evaluate_trigger(order, bar(97.0, 94.0, 95.5, open=96.0))
    assert decision.fill and decision.fill_price == 95.0


def test_stop_not_triggered_when_range_stays_above_stop():
    order = Order("AAPL", "buy", 10, type=OrderType.STOP, stop_price=105.0)
    decision = evaluate_trigger(order, bar(104.9, 103.0, 104.0, open=103.5))
    assert not decision.elect and not decision.fill


def test_stop_gap_through_buy_executes_at_open():
    """Gap-through: the bar OPENS past the buy stop, so execution is at the
    open (worse than the stop price), not at the stop level."""
    order = Order("AAPL", "buy", 10, type=OrderType.STOP, stop_price=105.0)
    decision = evaluate_trigger(order, bar(112.0, 109.0, 111.0, open=110.0))
    assert decision.fill
    assert decision.fill_price == 110.0
    assert decision.note == "stop_gap_through"


def test_stop_gap_through_sell_executes_at_open():
    order = Order("AAPL", "sell", 10, type=OrderType.STOP, stop_price=95.0)
    decision = evaluate_trigger(order, bar(94.0, 88.0, 90.0, open=90.0))
    assert decision.fill
    assert decision.fill_price == 90.0
    assert decision.note == "stop_gap_through"


# ----------------------------------------------------------------------
# Stop-limit
# ----------------------------------------------------------------------

def test_stop_limit_triggers_then_works_as_limit():
    order = Order("AAPL", "buy", 10, type=OrderType.STOP_LIMIT,
                  stop_price=105.0, limit_price=106.0)
    order.transition(OrderState.SUBMITTED)  # broker submission
    # Bar gaps up through the stop (open 106.3) but never trades at/below
    # the 106 limit (low 106.2): stop elects, limit leg starts working.
    first = evaluate_trigger(order, bar(107.0, 106.2, 106.5, open=106.3))
    assert first.elect and not first.fill
    assert first.state_after is OrderState.TRIGGERED
    order.transition(OrderState.TRIGGERED)
    second = evaluate_trigger(order, bar(107.5, 105.5, 106.5))
    assert second.fill
    assert second.fill_price == 106.0


def test_stop_limit_working_not_filled_when_limit_not_met():
    order = Order("AAPL", "buy", 10, type=OrderType.STOP_LIMIT,
                  stop_price=105.0, limit_price=106.0)
    order.transition(OrderState.SUBMITTED)
    order.transition(OrderState.TRIGGERED)
    decision = evaluate_trigger(order, bar(107.0, 106.2, 106.5))
    assert not decision.fill
    assert decision.state_after is OrderState.WORKING
    order.transition(OrderState.WORKING)
    # Still working: limit leg keeps evaluating while state == WORKING and
    # never trades through the 106 limit.
    decision = evaluate_trigger(order, bar(106.4, 106.1, 106.3))
    assert not decision.fill
    assert decision.state_after is OrderState.WORKING


def test_stop_limit_same_bar_fill_when_limit_already_crossed():
    """Stop and limit both trade through in the same bar -> fill at the limit."""
    order = Order("AAPL", "buy", 10, type=OrderType.STOP_LIMIT,
                  stop_price=105.0, limit_price=105.5)
    decision = evaluate_trigger(order, bar(107.0, 105.0, 106.0, open=105.0))
    assert decision.fill and decision.fill_price == 105.5
    assert decision.note == "stop_limit_same_bar_fill"


def test_stop_limit_rests_when_stop_not_crossed():
    order = Order("AAPL", "buy", 10, type=OrderType.STOP_LIMIT,
                  stop_price=105.0, limit_price=106.0)
    decision = evaluate_trigger(order, bar(104.0, 103.0, 103.5))
    assert not decision.elect and not decision.fill


# ----------------------------------------------------------------------
# Trailing stop: the ratchet contract
# ----------------------------------------------------------------------

def test_trailing_sell_ratchets_up_and_never_back():
    order = Order("AAPL", "sell", 10, type=OrderType.TRAILING_STOP, trail_pct=0.02)
    # anchor starts at the first bar's high
    assert evaluate_trigger(order, bar(101.0, 99.0, 100.0)).state_after is OrderState.SUBMITTED
    assert order.trail_anchor == 101.0
    evaluate_trigger(order, bar(103.0, 101.5, 102.0))   # ratchet to 103
    evaluate_trigger(order, bar(104.0, 102.0, 103.0))   # ratchet to 104
    assert order.trail_anchor == 104.0
    # A pullback bar with a lower high must NOT ratchet the anchor back.
    evaluate_trigger(order, bar(103.5, 102.0, 102.5))
    assert order.trail_anchor == 104.0
    # Stop level = 104 * (1 - 0.02) = 101.92
    decision = evaluate_trigger(order, bar(102.0, 101.9, 102.0))
    assert decision.fill and decision.fill_price == 101.92
    assert decision.note == "trailing_elected"


def test_trailing_buy_ratchets_down_and_never_back():
    order = Order("AAPL", "buy", 10, type=OrderType.TRAILING_STOP, trail_pct=0.02)
    evaluate_trigger(order, bar(99.0, 97.0, 98.0))
    assert order.trail_anchor == 97.0
    evaluate_trigger(order, bar(98.0, 95.0, 96.0))     # ratchet to 95
    evaluate_trigger(order, bar(96.0, 95.5, 95.8))
    assert order.trail_anchor == 95.0                  # never moves back up
    # Stop level = 95 * (1 + 0.02) = 96.90
    decision = evaluate_trigger(order, bar(97.0, 95.4, 96.5))
    assert decision.fill and decision.fill_price == 96.90


def test_trailing_gap_through_executes_at_open():
    order = Order("AAPL", "sell", 10, type=OrderType.TRAILING_STOP, trail_pct=0.02)
    evaluate_trigger(order, bar(101.0, 99.0, 100.0))   # anchor 101, stop 98.98
    decision = evaluate_trigger(order, bar(100.0, 97.0, 98.0, open=97.5))
    assert decision.fill
    assert decision.fill_price == 97.5
    assert decision.note == "trailing_gap_through"


def test_ratchet_trailing_requires_trailing_type():
    order = Order("AAPL", "sell", 10, type=OrderType.STOP, stop_price=95.0)
    with pytest.raises(InvalidOrderError):
        ratchet_trailing(order, bar(100, 99, 99.5))


# ----------------------------------------------------------------------
# OCO: one cancels the other
# ----------------------------------------------------------------------

def test_oco_one_cancels_other_on_fill():
    tp = Order("AAPL", "sell", 10, type=OrderType.LIMIT, limit_price=110.0,
               client_id="tp", oco_group="g1", state=OrderState.SUBMITTED)
    sl = Order("AAPL", "sell", 10, type=OrderType.STOP, stop_price=95.0,
               client_id="sl", oco_group="g1", state=OrderState.SUBMITTED)
    tp.transition(OrderState.FILLED)
    cancelled = cancel_oco_siblings(tp, [tp, sl])
    assert sl.state is OrderState.CANCELLED
    assert sl.cancel_reason == "oco:one_cancels_other"
    assert cancelled == [sl]


def test_oco_unrelated_orders_are_untouched():
    tp = Order("AAPL", "sell", 10, type=OrderType.LIMIT, limit_price=110.0,
               client_id="tp", oco_group="g1", state=OrderState.SUBMITTED)
    other = Order("MSFT", "sell", 10, type=OrderType.LIMIT, limit_price=300.0,
                  client_id="other", oco_group="g2", state=OrderState.SUBMITTED)
    tp.transition(OrderState.FILLED)
    assert cancel_oco_siblings(tp, [tp, other]) == []
    assert other.state is OrderState.SUBMITTED


# ----------------------------------------------------------------------
# Bracket: entry arms children; child fill cancels the twin
# ----------------------------------------------------------------------

def test_bracket_children_arm_only_after_entry_fill():
    entry = Order("AAPL", "buy", 10, client_id="e1")
    tp = Order("AAPL", "sell", 10, type=OrderType.LIMIT, limit_price=110.0,
               parent_id="e1")
    sl = Order("AAPL", "sell", 10, type=OrderType.STOP, stop_price=95.0,
               parent_id="e1")
    # Entry still pending: children must NOT be armed.
    assert arm_bracket_children(entry, [tp, sl]) == []
    assert tp.state is OrderState.PENDING_NEW
    # Entry fills (through the legal SUBMITTED step): children arm.
    entry.transition(OrderState.SUBMITTED)
    entry.transition(OrderState.FILLED)
    armed = arm_bracket_children(entry, [tp, sl])
    assert len(armed) == 2
    assert tp.state is OrderState.SUBMITTED
    assert sl.state is OrderState.SUBMITTED


def test_bracket_child_fill_cancels_twin():
    tp = Order("AAPL", "sell", 10, type=OrderType.LIMIT, limit_price=110.0,
               parent_id="e1", state=OrderState.SUBMITTED)
    sl = Order("AAPL", "sell", 10, type=OrderType.STOP, stop_price=95.0,
               parent_id="e1", state=OrderState.SUBMITTED)
    tp.transition(OrderState.FILLED)
    cancelled = cancel_bracket_twin(tp, [tp, sl])
    assert sl.state is OrderState.CANCELLED
    assert sl.cancel_reason == "bracket:twin_filled"
    assert cancelled == [sl]


# ----------------------------------------------------------------------
# Structural validation
# ----------------------------------------------------------------------

def test_invalid_order_parameters_rejected():
    with pytest.raises(InvalidOrderError):
        Order("AAPL", "buy", 0, type=OrderType.LIMIT, limit_price=100.0)
    with pytest.raises(InvalidOrderError):
        Order("AAPL", "buy", 10, trail_pct=1.5)
    with pytest.raises(InvalidOrderError):
        Order("AAPL", "sideways", 10)
    with pytest.raises(InvalidOrderError):
        Order("AAPL", "buy", 10, type=OrderType.STOP, stop_price=-5.0)


def test_container_types_are_never_evaluated_directly():
    order = Order("AAPL", "buy", 10, type=OrderType.OCO)
    with pytest.raises(InvalidOrderError):
        evaluate_trigger(order, bar(101, 99, 100))
    bracket = Order("AAPL", "buy", 10, type=OrderType.BRACKET)
    with pytest.raises(InvalidOrderError):
        evaluate_trigger(bracket, bar(101, 99, 100))
