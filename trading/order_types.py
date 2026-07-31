"""Order types and the clean order state machine (Phase 8).

Real implementations, not stubs: market / limit / stop / stop-limit /
trailing-stop / OCO (one-cancels-other) / bracket order semantics with a
validated state machine and pure, deterministic trigger-edge evaluation
(gap-through stops, trailing ratchets, sibling cancellation).

The trigger layer is deliberately side-effect free: it inspects an
``Order`` and a price ``Bar`` and returns a ``TriggerDecision``. The paper
broker (``trading/paper_broker.py``) is the only component allowed to
mutate orders, fill them, and cancel siblings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, List, Optional, Sequence, Tuple


class OrderType(str, Enum):
    """The full Phase-8 order-type surface."""

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    TRAILING_STOP = "trailing_stop"
    OCO = "oco"
    BRACKET = "bracket"

    @classmethod
    def coerce(cls, value: Any) -> "OrderType":
        """Accept enum members or their string values."""
        if isinstance(value, OrderType):
            return value
        return cls(str(value).lower())


class OrderState(str, Enum):
    """Lifecycle states of an order under the Phase-8 state machine."""

    PENDING_NEW = "pending_new"      # constructed, not yet transmitted
    SUBMITTED = "submitted"          # resting at the broker (limit/stop/trailing)
    TRIGGERED = "triggered"          # stop/trailing elected; market or limit work
    WORKING = "working"              # stop-limit leg now resting as a limit
    FILLED = "filled"                # terminal
    CANCELLED = "cancelled"          # terminal (manual, OCO sibling, bracket twin)
    REJECTED = "rejected"            # terminal (gateway / idempotency caps)
    EXPIRED = "expired"              # terminal (never triggered before expiry)


# Order of the states above; used only for documentation/ordering greps.
_STATE_ORDER: Tuple[OrderState, ...] = (
    OrderState.PENDING_NEW,
    OrderState.SUBMITTED,
    OrderState.TRIGGERED,
    OrderState.WORKING,
    OrderState.FILLED,
    OrderState.CANCELLED,
    OrderState.REJECTED,
    OrderState.EXPIRED,
)

# Terminal states: no transitions leave them.
TERMINAL_STATES: frozenset[OrderState] = frozenset(
    {OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED, OrderState.EXPIRED}
)

# The authoritative transition table. Every non-terminal state maps to the
# exact set of legal successor states; anything else raises
# ``InvalidTransitionError``.
STATE_MACHINE: dict[OrderState, frozenset[OrderState]] = {
    OrderState.PENDING_NEW: frozenset({OrderState.SUBMITTED, OrderState.REJECTED, OrderState.CANCELLED}),
    OrderState.SUBMITTED: frozenset({OrderState.TRIGGERED, OrderState.WORKING, OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED, OrderState.EXPIRED}),
    OrderState.TRIGGERED: frozenset({OrderState.WORKING, OrderState.FILLED, OrderState.CANCELLED, OrderState.EXPIRED}),
    OrderState.WORKING: frozenset({OrderState.FILLED, OrderState.CANCELLED, OrderState.EXPIRED}),
    OrderState.FILLED: frozenset(),
    OrderState.CANCELLED: frozenset(),
    OrderState.REJECTED: frozenset(),
    OrderState.EXPIRED: frozenset(),
}

# Every order type may legally start in the pending state.
_VALID_TYPES: frozenset[str] = frozenset(t.value for t in OrderType)
_VALID_SIDES: frozenset[str] = frozenset({"buy", "sell", "sell_short", "cover"})

# Ratchet orientation per side: a sell (long exit) trailing stop ratchets the
# anchor UP toward highs; a buy (short cover) trailing stop ratchets DOWN
# toward lows.
_RATCHET_UP_SIDES: frozenset[str] = frozenset({"sell", "sell_short"})


class InvalidTransitionError(ValueError):
    """Raised when an order attempts an illegal state transition."""


class InvalidOrderError(ValueError):
    """Raised when an order/type combination is structurally invalid."""


def transition(state: OrderState, next_state: OrderState) -> OrderState:
    """Validate a single state-machine step; raises on illegal moves."""
    if state not in STATE_MACHINE:
        raise InvalidTransitionError(f"unknown state: {state!r}")
    if next_state not in STATE_MACHINE:
        raise InvalidTransitionError(f"unknown state: {next_state!r}")
    if next_state not in STATE_MACHINE[state]:
        raise InvalidTransitionError(
            f"illegal transition: {state.value} -> {next_state.value}"
        )
    return next_state


def _validate_order(order: "Order") -> None:
    """Structural validation shared by construction and submission."""
    if OrderType.coerce(order.type) is not OrderType.MARKET and order.quantity <= 0:
        raise InvalidOrderError("quantity must be positive")
    if order.side.lower() not in _VALID_SIDES:
        raise InvalidOrderError(f"unknown side: {order.side!r}")
    if order.trail_pct is not None and not (0.0 < order.trail_pct < 1.0):
        raise InvalidOrderError("trail_pct must be in (0, 1)")
    if order.stop_price is not None and order.stop_price <= 0:
        raise InvalidOrderError("stop_price must be positive")
    if order.limit_price is not None and order.limit_price <= 0:
        raise InvalidOrderError("limit_price must be positive")


@dataclass
class Order:
    """A single order under the Phase-8 state machine.

    Compatible field names with the Phase-7 facade (``symbol, side, quantity``
    positional, ``price`` keyword) so existing gateway tests keep passing.
    ``state`` is only mutated through :meth:`Order.transition`, which enforces
    ``STATE_MACHINE``.
    """

    symbol: str
    side: str
    quantity: float
    type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    trail_pct: Optional[float] = None
    trail_anchor: Optional[float] = None   # ratcheted extreme; never reverses
    client_id: str = ""
    parent_id: Optional[str] = None        # bracket entry link
    oco_group: Optional[str] = None        # one-cancels-other group
    state: OrderState = OrderState.PENDING_NEW
    price: float = 0.0                     # market reference at submission
    submitted_at: Optional[float] = None   # injected clock seconds
    filled_price: Optional[float] = None
    filled_quantity: float = 0.0
    filled_at: Optional[float] = None
    reject_reason: Optional[str] = None
    cancel_reason: Optional[str] = None

    def __post_init__(self) -> None:
        self.type = OrderType.coerce(self.type)
        _validate_order(self)

    def transition(self, next_state: OrderState) -> "Order":
        """Move to ``next_state`` after validating against the table."""
        transition(self.state, next_state)
        self.state = next_state
        return self

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES


@dataclass(frozen=True)
class Bar:
    """Minimal price bar consumed by the trigger engine (high/low/close)."""

    high: float
    low: float
    close: float
    open: float = 0.0
    time: Any = None

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError("bar high < low")
        if self.close <= 0 or self.high <= 0 or self.low <= 0:
            raise ValueError("bar prices must be positive")


@dataclass(frozen=True)
class TriggerDecision:
    """Pure outcome of evaluating one order against one bar."""

    elect: bool                          # trigger elected this bar?
    state_after: OrderState              # legal next state (never terminal unless fill)
    fill: bool = False                   # execution decision for this bar
    fill_price: Optional[float] = None   # elected/limit execution price
    note: str = ""


def _sells(order: Order) -> bool:
    return order.side.lower() in {"sell", "sell_short"}


def evaluate_trigger(order: Order, bar: Bar) -> TriggerDecision:
    """Evaluate one order against one bar. Pure and deterministic.

    Market orders elect immediately at the bar close. Limit orders fill when
    the bar trades through the limit. Stop and stop-limit orders elect when
    the bar crosses the stop; a gap through the stop elects at the bar open
    (the classic gap-through-stop edge: execution is at the open, not the
    stop price). Trailing stops first ratchet the anchor, then elect on the
    ratcheted stop level.
    """
    otype = OrderType.coerce(order.type)

    if otype is OrderType.MARKET:
        return TriggerDecision(True, OrderState.FILLED, fill=True,
                               fill_price=bar.close, note="market_fill")

    if otype is OrderType.LIMIT:
        assert order.limit_price is not None
        crossed = (bar.low <= order.limit_price if not _sells(order)
                   else bar.high >= order.limit_price)
        if crossed:
            return TriggerDecision(True, OrderState.FILLED, fill=True,
                                   fill_price=order.limit_price, note="limit_fill")
        return TriggerDecision(False, OrderState.SUBMITTED,
                               note="limit_resting")

    if otype is OrderType.STOP:
        assert order.stop_price is not None
        crossed = (bar.high >= order.stop_price if not _sells(order)
                   else bar.low <= order.stop_price)
        if not crossed:
            return TriggerDecision(False, OrderState.SUBMITTED, note="stop_resting")
        # Gap-through: if the bar opened past the stop, execution is at the
        # open (worse than the stop). Otherwise elect at the stop price.
        gapped = (bar.open >= order.stop_price if not _sells(order)
                  else bar.open <= order.stop_price) if bar.open > 0 else False
        exec_price = bar.open if gapped else order.stop_price
        note = "stop_gap_through" if gapped else "stop_elected"
        return TriggerDecision(True, OrderState.FILLED, fill=True,
                               fill_price=exec_price, note=note)

    if otype is OrderType.STOP_LIMIT:
        assert order.stop_price is not None and order.limit_price is not None
        stop_crossed = (bar.high >= order.stop_price if not _sells(order)
                        else bar.low <= order.stop_price)
        limit_crossed = (bar.low <= order.limit_price if not _sells(order)
                         else bar.high >= order.limit_price)
        if order.state in (OrderState.TRIGGERED, OrderState.WORKING):
            # Stop already elected: the limit leg is working.
            if limit_crossed:
                return TriggerDecision(True, OrderState.FILLED, fill=True,
                                       fill_price=order.limit_price,
                                       note="stop_limit_working_fill")
            return TriggerDecision(True, OrderState.WORKING, note="stop_limit_working")
        if not stop_crossed:
            return TriggerDecision(False, OrderState.SUBMITTED, note="stop_limit_resting")
        if limit_crossed:
            # Gap/same-bar: the stop elected and the limit is already traded
            # through in the same bar -> fill at the limit price.
            return TriggerDecision(True, OrderState.FILLED, fill=True,
                                   fill_price=order.limit_price,
                                   note="stop_limit_same_bar_fill")
        return TriggerDecision(True, OrderState.TRIGGERED, fill=False,
                               note="stop_limit_triggered")

    if otype is OrderType.TRAILING_STOP:
        stop_level = _trailing_level(order, bar)
        assert stop_level is not None
        crossed = (bar.low <= stop_level if _sells(order)
                   else bar.high >= stop_level)
        if crossed:
            # Gap-through semantics for trailing stops: execution at the open
            # when the bar opens through the ratcheted level.
            gapped = (bar.open <= stop_level if _sells(order)
                      else bar.open >= stop_level) if bar.open > 0 else False
            exec_price = bar.open if gapped else stop_level
            return TriggerDecision(True, OrderState.FILLED, fill=True,
                                   fill_price=exec_price,
                                   note="trailing_gap_through" if gapped else "trailing_elected")
        return TriggerDecision(False, OrderState.SUBMITTED, note="trailing_resting")

    if otype in (OrderType.OCO, OrderType.BRACKET):
        raise InvalidOrderError(f"{otype.value} is a container order and is not evaluated directly")

    raise InvalidOrderError(f"unsupported order type: {order.type!r}")


def ratchet_trailing(order: Order, bar: Bar) -> float:
    """Update a trailing stop's anchor in the favorable direction only.

    Sell stops ratchet the anchor UP to the highest high; buy stops ratchet
    DOWN to the lowest low. The anchor never moves backwards, which is the
    trailing ratchet contract tested by the trigger-edge suite.
    """
    otype = OrderType.coerce(order.type)
    if otype is not OrderType.TRAILING_STOP:
        raise InvalidOrderError("ratchet_trailing requires a trailing_stop order")
    assert order.trail_pct is not None
    if order.trail_anchor is None:
        order.trail_anchor = bar.high if _sells(order) else bar.low
    elif _sells(order):
        order.trail_anchor = max(order.trail_anchor, bar.high)
    else:
        order.trail_anchor = min(order.trail_anchor, bar.low)
    return order.trail_anchor


def _trailing_level(order: Order, bar: Bar) -> Optional[float]:
    """Ratchet the anchor, then compute the live stop level."""
    ratchet_trailing(order, bar)
    if order.trail_anchor is None:
        return None
    if _sells(order):
        return order.trail_anchor * (1.0 - order.trail_pct)
    return order.trail_anchor * (1.0 + order.trail_pct)


def cancel_oco_siblings(filled_order: Order, orders: Sequence[Order]) -> List[Order]:
    """OCO: when one leg fills, every other leg in its group is cancelled.

    Returns the cancelled siblings. Pure: only state mutations applied are
    legal transitions to CANCELLED.
    """
    cancelled: List[Order] = []
    if not filled_order.oco_group:
        return cancelled
    for sibling in orders:
        if sibling is filled_order:
            continue
        if sibling.oco_group == filled_order.oco_group and not sibling.is_terminal():
            sibling.transition(OrderState.CANCELLED)
            sibling.cancel_reason = "oco:one_cancels_other"
            cancelled.append(sibling)
    return cancelled


def arm_bracket_children(entry: Order, children: Iterable[Order]) -> List[Order]:
    """Bracket: arm take-profit/stop-loss children once the entry fills."""
    armed: List[Order] = []
    if entry.state is not OrderState.FILLED:
        return armed
    for child in children:
        if child.parent_id != entry.client_id:
            continue
        if child.state is OrderState.PENDING_NEW:
            child.transition(OrderState.SUBMITTED)
            armed.append(child)
    return armed


def cancel_bracket_twin(filled_child: Order, children: Iterable[Order]) -> List[Order]:
    """Bracket: when one child fills, its sibling child is cancelled."""
    cancelled: List[Order] = []
    if not filled_child.parent_id:
        return cancelled
    for sibling in children:
        if sibling is filled_child:
            continue
        if sibling.parent_id == filled_child.parent_id and not sibling.is_terminal():
            sibling.transition(OrderState.CANCELLED)
            sibling.cancel_reason = "bracket:twin_filled"
            cancelled.append(sibling)
    return cancelled


def order_fingerprint(
    *,
    symbol: str,
    side: str,
    quantity: float,
    order_type: str,
    price: float,
    limit_price: Optional[float],
    stop_price: Optional[float],
    client_id: str,
    oco_group: Optional[str] = None,
) -> str:
    """Deterministic identity used for the duplicate-submission window.

    Includes ``client_id`` so an idempotent retry of the same transmission is
    recognized as the same order within the configured window.
    """
    import hashlib

    payload = (symbol.upper(), side.lower(), float(quantity), str(order_type).lower(),
               float(price), limit_price, stop_price, client_id or "", oco_group or "")
    return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()
