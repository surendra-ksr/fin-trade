# Phase 8 atomic evidence pack

2026-07-31T11:05:00Z

## Git state

Working tree clean before evidence capture (verified below), HEAD `060d550c31d3fbb1d4e21544fd94c49cf367b8f9`:
```text
?? docs/PHASE8_EVIDENCE.md
060d550 Phase 8: update architecture roadmap and audit report
0d84395 Phase 8: mark BUILD_PLAN phase 8 implemented
8b3b2c7 Phase 8: order types and real paper broker

## New module stats
```text
  390 trading/order_types.py
  476 trading/paper_broker.py
   12 trading/core.py
  159 backtest/fill_engine.py
 1037 total
trading/order_types.py lines=390 docstrings=25 defs=15
trading/paper_broker.py lines=476 docstrings=21 defs=18
```

## Required verbatim bodies

### 1. Shared fill-pricing core (`backtest/fill_engine.py` — ONE path for backtest + paper broker)
```python
def price_fill(
    order_qty: float,
    *,
    side: str = "buy",
    ref_price: float,
    fee_bps: float = 1.0,
    slippage_bps: float = 2.0,
    partial_fill_prob: float = 0.15,
    market_low: Optional[float] = None,
    market_high: Optional[float] = None,
    rng: Optional[np.random.Generator] = None,
) -> Tuple[float, float, float, float]:
    """THE shared fill-pricing core (backtest + paper broker).

    Args:
        order_qty: requested quantity (> 0 for an executable fill).
        side: buy/cover execute at/above the reference; sell/sell_short at/below.
        ref_price: the market reference price the order is priced against
            (next-bar close for the backtester; live mark for the paper broker).
        fee_bps: commission in basis points of filled notional.
        slippage_bps: half-spread slippage in basis points. Buys are filled
            unfavorably at ``ref * (1 + slip)``, sells at ``ref * (1 - slip)``.
        partial_fill_prob: probability of a partial fill (fraction in [0.3, 1)).
        market_low/market_high: clamp bounds; default ±1% around the reference.
        rng: optional ``numpy.random.Generator`` for deterministic tests.

    Returns:
        ``(fill_price, filled_qty, fee, slippage_cost)``.
    """
    if order_qty <= 0:
        return ref_price, 0.0, 0.0, 0.0
    gen: np.random.Generator = rng if rng is not None else np.random  # type: ignore[assignment]
    # Partial fill simulation
    filled_qty = order_qty
    if gen.random() < partial_fill_prob:
        filled_qty = order_qty * gen.uniform(0.3, 1.0)
    # Slippage: shift price unfavorably by side (buy up, sell down)
    direction = 1.0 if side.lower() in {"buy", "cover"} else -1.0
    price = ref_price * (1.0 + direction * gen.uniform(0.0, slippage_bps) / 10000.0)
    # Fee calculation
    fee = filled_qty * price * fee_bps / 10000.0
    # Ensure price doesn't cross high/low unrealistically (clamp to market range)
    low = market_low if market_low is not None else ref_price * 0.99
    high = market_high if market_high is not None else ref_price * 1.01
    price = min(max(price, low * 0.999), high * 1.001)
    slippage_cost = abs(price - ref_price) * filled_qty
    _log.debug(
        "fill: qty=%.2f price=%.2f fee=%.4f slippage=%.2f partial=%.2f",
        filled_qty, price, fee, slippage_cost, filled_qty / order_qty,
    )
    return price, filled_qty, fee, slippage_cost
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
    The fill uses `market_close` as the reference price and delegates all
    pricing to the shared ``price_fill`` core so the backtester and the paper
    broker can never diverge.
    """
    if order_qty <= 0:
        return Fill(price=market_close, quantity=0.0, fee=0.0, slippage=0.0, timestamp=-1)
    price, filled_qty, fee, slippage_cost = price_fill(
        order_qty,
        side="buy",
        ref_price=market_close,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        partial_fill_prob=partial_fill_prob,
        market_low=market_low,
        market_high=market_high,
    )
    return Fill(price=price, quantity=filled_qty, fee=fee,
```

### 2. Paper broker `submit` + idempotency caps (`trading/paper_broker.py`)
```python
    def submit(self, request: OrderRequest) -> Order:
        """Low-level transmission target; callers must use ``RiskGateway``."""
        if request.quantity <= 0:
            raise ValueError("quantity must be positive")
        now = float(self._clock())
        rejected = self._idempotency_check(request, now)
        if rejected is not None:
            return rejected
        order = Order(
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
            type=request.order_type,
            limit_price=request.limit_price,
            stop_price=request.stop_price,
            trail_pct=request.trail_pct,
            client_id=request.client_id or uuid4().hex,
            parent_id=request.parent_id,
            oco_group=request.oco_group,
            price=request.price,
            submitted_at=now,
        )
        order.transition(OrderState.SUBMITTED)
        self.orders.append(order)
        self._submission_times.append(now)
        # The fingerprint is computed from the REQUEST (pre-randomization), so
        # an identical retransmission or a retry with the same client_id is
        # recognized as a duplicate within the configured window.
        self._duplicate_map[order_fingerprint(
            symbol=request.symbol, side=request.side, quantity=request.quantity,
            order_type=request.order_type, price=request.price,
            limit_price=request.limit_price, stop_price=request.stop_price,
            client_id=request.client_id, oco_group=request.oco_group,
        )] = now
        if request.client_id:
            self._duplicate_map["client:" + request.client_id] = now
        self._evaluate_order_fill(order, price=request.price, now=now)
        return order
    def _idempotency_check(self, request: OrderRequest, now: float) -> Optional[Order]:
        max_per_min = int(self.config.circuit_breakers.technical.max_orders_per_minute)
        self._submission_times = [t for t in self._submission_times if now - t < 60.0]
        if len(self._submission_times) >= max_per_min:
            return self._reject(request, f"order_rate:{max_per_min}/min_exceeded")
        dup_window = float(self.config.order_limits.per_order.duplicate_order_window_seconds)
        fp = order_fingerprint(
            symbol=request.symbol, side=request.side, quantity=request.quantity,
            order_type=request.order_type, price=request.price,
            limit_price=request.limit_price, stop_price=request.stop_price,
            client_id=request.client_id or "", oco_group=request.oco_group,
        )
        if fp in self._duplicate_map and now - self._duplicate_map[fp] < dup_window:
            return self._reject(request, f"duplicate_order:within_{dup_window:.0f}s_window")
        if request.client_id:
            client_key = "client:" + request.client_id
            if client_key in self._duplicate_map and now - self._duplicate_map[client_key] < dup_window:
                return self._reject(request, f"duplicate_client_id:within_{dup_window:.0f}s_window")
        return None
    def _reject(self, request: OrderRequest, reason: str) -> Order:
        order = Order(
            symbol=request.symbol, side=request.side, quantity=request.quantity,
            type=request.order_type, limit_price=request.limit_price,
            stop_price=request.stop_price, trail_pct=request.trail_pct,
            client_id=request.client_id or uuid4().hex, price=request.price,
            state=OrderState.REJECTED, reject_reason=reason,
        )
        self.orders.append(order)
        return order
```

### 3. Fill execution through the shared core (`_fill_order`)
```python
    def _fill_order(self, order: Order, ref_price: float, now: float) -> None:
        remaining = order.quantity - order.filled_quantity
        fprice, fqty, fee, slip = price_fill(
            remaining,
            side=order.side,
            ref_price=ref_price,
            fee_bps=self.fee_bps,
            slippage_bps=self.slippage_bps,
            partial_fill_prob=self.partial_fill_prob,
            rng=self._rng,
        )
        if fqty <= 0:
            return
        order.transition(OrderState.FILLED)
        order.filled_price = fprice
        order.filled_quantity = fqty
        order.filled_at = now
        self.fills.append(FillRecord(
            symbol=order.symbol.upper(), side=order.side, quantity=fqty,
            price=fprice, fee=fee, slippage=slip, time=now, order_id=order.client_id,
        ))
        direction = 1.0 if order.side.lower() in {"buy", "cover"} else -1.0
        self.cash -= direction * (fprice * fqty) + fee
        self._apply_position(order, ref_price=ref_price, qty=fqty, fee=fee, slip=slip, now=now)
        # OCO: a filled leg cancels every other leg in its group (they stay in
        # the ledger as CANCELLED); a filled child cancels its bracket twin;
        # a filled entry transmits any pending bracket children.
        cancel_oco_siblings(order, self.orders)
        cancel_bracket_twin(order, self.orders)
```

### 4. Realized P&L INCLUDING entry fees (`_close_lot` + `_realized_formula` + `db.close_paper_trade` call sites)
```python
    def _close_lot(
        self, symbol: str, trade: OpenTrade, qty: float,
        exit_ref: float, exit_fee: float, exit_slip: float, now: float,
    ) -> float:
        """Close ``qty`` of ``trade``; realized P&L INCLUDES the proportional
        entry fees/slippage (via ``db.close_paper_trade`` when a DB exists)."""
        if trade.remaining <= 0.0:
            return 0.0
        close_qty = min(qty, trade.remaining)
        if self.db is not None and trade.trade_id is not None:
            if close_qty < trade.remaining:
                slice_id = self.db.split_paper_trade(trade.trade_id, close_qty)
                if slice_id is None:
                    raise RuntimeError("paper trade split failed")
                realized = self.db.close_paper_trade(
                    slice_id, self._iso(now), exit_ref, fees=exit_fee, slippage_cost=exit_slip,
                )
            else:
                realized = self.db.close_paper_trade(
                    trade.trade_id, self._iso(now), exit_ref, fees=exit_fee, slippage_cost=exit_slip,
                )
            if realized is None:
                raise RuntimeError("paper trade close failed")
        else:
            realized = self._realized_formula(trade, close_qty, exit_ref, exit_fee, exit_slip)
        # Mirror the DB row math in the in-memory lot.
        slice_fee = trade.entry_fee * (close_qty / trade.remaining)
        slice_slip = trade.entry_slip * (close_qty / trade.remaining)
        trade.entry_fee -= slice_fee
        trade.entry_slip -= slice_slip
        trade.remaining -= close_qty
        if trade.remaining <= 0.0:
            self.open_trades.setdefault(symbol, []).pop(0)
        return float(realized)
    def _realized_formula(trade: OpenTrade, qty: float, exit_ref: float, exit_fee: float, exit_slip: float) -> float:
        """In-memory realized P&L — identical math to ``db.close_paper_trade``:
        directional gross minus entry fees, entry slippage, exit fees and exit
        slippage (entry costs allocated proportionally to the closed slice)."""
        direction = 1.0 if trade.side == "BUY" else -1.0
        gross = direction * (float(exit_ref) - trade.entry_ref) * qty
        slice_fee = trade.entry_fee * (qty / trade.remaining)
        slice_slip = trade.entry_slip * (qty / trade.remaining)
        return gross - slice_fee - slice_slip - exit_fee - exit_slip
    def close_paper_trade(
        self,
        trade_id: int,
        exit_time: Any,
        exit_price: float,
        *,
        fees: float = 0.0,
        slippage_cost: float = 0.0,
    ) -> Optional[float]:
        """Close an open paper trade; computes and stores realized P&L.

        Returns:
            Realized P&L, or None when the trade id does not exist/is closed.
        """
        row = self.query_one(
            "SELECT side, quantity, entry_price, status, fees, slippage_cost "
            "FROM paper_trades WHERE id = ?",
            (trade_id,))
        if row is None or row["status"] != "OPEN":
            return None
        direction = 1.0 if row["side"] == "BUY" else -1.0
        gross = direction * (float(exit_price) - float(row["entry_price"])) * float(row["quantity"])
        # realized P&L nets out ALL costs: entry fees booked at open plus the
        # exit fees/slippage passed to this close
        realized = (gross - float(row["fees"]) - float(row["slippage_cost"])
                    - float(fees) - float(slippage_cost))
        self.execute(
            "UPDATE paper_trades SET exit_time = ?, exit_price = ?, status = 'CLOSED', "
            "realized_pnl = ?, fees = fees + ?, slippage_cost = slippage_cost + ?, "
            "updated_at = ? WHERE id = ?",
            (to_iso_z(exit_time), float(exit_price), realized, float(fees),
             float(slippage_cost), to_iso_z(pd.Timestamp.utcnow()), trade_id),
        )
        return realized
    def split_paper_trade(self, trade_id: int, close_quantity: float) -> Optional[int]:
        """Carve a ``close_quantity`` slice out of an OPEN paper trade.

        The original row keeps the remaining quantity and a proportionally
        reduced fee/slippage balance; a NEW open row is inserted for the
        slice with the same entry reference and its proportional share of
        entry fees/slippage. Returns the new row's id, or None when the
        trade is not open or ``close_quantity`` is not a strict partial
        (callers use ``close_paper_trade`` for full closes).

        This keeps every ``paper_trades`` row's fee/slippage balance
        consistent so ``close_paper_trade`` computes realized P&L that
        includes the correct proportional entry costs for partial closes.
        """
        row = self.query_one(
            "SELECT portfolio_id, symbol, side, quantity, entry_time, entry_price, status, "
            "fees, slippage_cost, strategy, signal_id, meta "
            "FROM paper_trades WHERE id = ?",
            (trade_id,))
        if row is None or row["status"] != "OPEN":
            return None
        remaining = float(row["quantity"])
        if not (0.0 < float(close_quantity) < remaining):
            return None
        share = float(close_quantity) / remaining
        slice_fee = float(row["fees"]) * share
        slice_slip = float(row["slippage_cost"]) * share
        now = to_iso_z(pd.Timestamp.utcnow())
        # Shrink the original row (fees/slippage stay proportional to qty).
        self.execute(
            "UPDATE paper_trades SET quantity = ?, fees = ?, slippage_cost = ?, "
            "updated_at = ? WHERE id = ?",
            (remaining - float(close_quantity), float(row["fees"]) - slice_fee,
             float(row["slippage_cost"]) - slice_slip, now, trade_id),
        )
        # Insert the slice as its own open row with proportional costs.
        cursor = self.execute(
            "INSERT INTO paper_trades "
            "(portfolio_id, symbol, side, quantity, entry_time, entry_price, status, "
            " fees, slippage_cost, strategy, signal_id, meta, inserted_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?)",
            (row["portfolio_id"], row["symbol"], row["side"],
             float(close_quantity), row["entry_time"], row["entry_price"],
             slice_fee, slice_slip, row["strategy"], row["signal_id"], row["meta"],
             now, now),
        )
        return int(cursor.lastrowid)
```

### 5. Trigger engine (`trading/order_types.py` — gap-through stop + trailing ratchet + limit/stop/stop-limit)
```python
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
```

### 6. OCO one-cancels-other + bracket arming/twin cancellation
```python
def cancel_oco_siblings(filled_order: Order, orders: Sequence[Order]) -> List[Order]:
    """OCO: when one leg fills, every other leg in its group is cancelled.

    Returns the cancelled siblings. Pure: only state mutations applied are
    legal transitions to CANCELLED.
    """
    cancelled: List[Order] = []
    if not filled_order.oco_group:
        return cancelled
def arm_bracket_children(entry: Order, children: Iterable[Order]) -> List[Order]:
    """Bracket: arm take-profit/stop-loss children once the entry fills."""
    armed: List[Order] = []
    if entry.state is not OrderState.FILLED:
        return armed
def cancel_bracket_twin(filled_child: Order, children: Iterable[Order]) -> List[Order]:
    """Bracket: when one child fills, its sibling child is cancelled."""
    cancelled: List[Order] = []
    if not filled_child.parent_id:
        return cancelled
```

### 7. Order state machine transition table
```python
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
```

## Full collect-only: both environments
```text
# .venv
========================= 393 tests collected in 0.15s =========================
# .venv-ml
========================= 393 tests collected in 0.15s =========================
```
Phase-8 focused test counts:
```text
tests/unit/test_phase8_order_types.py:29
tests/unit/test_phase8_paper_broker.py:25
```

## Core-green run 1 (.venv, ML-only files excluded)
```text

============================= 369 passed in 22.16s =============================
## Core-green run 2 (.venv, ML-only files excluded)
```text

============================= 369 passed in 21.78s =============================
## ML run 1 (.venv-ml, full suite)
```text

============================= 393 passed in 22.60s =============================
## ML run 2 (.venv-ml, full suite)
```text

============================= 393 passed in 23.44s =============================

## Reconciliation
TOTAL = CORE_GREEN(369) + ML_ONLY(24) = 393
collect-only proof: .venv=393 items; .venv-ml=393 items; ML_ONLY = 393 - 369 = 24

## Complete pinned-environment package outputs

### .venv pip freeze
```text
beautifulsoup4==4.15.0
certifi==2026.7.22
charset-normalizer==3.4.9
coverage==7.15.2
frozendict==2.4.7
html5lib==1.1
idna==3.18
iniconfig==2.3.0
loguru==0.7.3
lxml==6.1.1
multitasking==0.0.13
numpy==2.2.6
packaging==26.2
pandas==2.2.3
peewee==4.2.6
platformdirs==4.11.0
pluggy==1.6.0
pytest==8.3.5
pytest-cov==6.1.1
python-dateutil==2.9.0.post0
python-dotenv==1.2.1
pytz==2026.3.post1
PyYAML==6.0.3
requests==2.31.0
scipy==1.15.3
six==1.17.0
soupsieve==2.9.1
typing_extensions==4.16.0
tzdata==2025.2
urllib3==2.7.0
webencodings==0.5.1
yfinance==0.2.50
```

### .venv-ml pip freeze (ML additions; core packages identical to .venv)
```text
gymnasium==1.1.1
numpy==2.2.6
optuna==4.5.0
pandas==2.2.3
pytest==8.3.5
pytest-cov==6.1.1
scikit-learn==1.7.2
scipy==1.15.3
shap==0.51.0
stable_baselines3==2.6.0
torch==2.6.0
transformers==4.48.3
```

## No-network test grep
```text
no network references in Phase-8 modules/tests
```

## Gateway grep proof (broker.submit reachable ONLY via RiskGateway)
```text
./risk/position_limits.py:153:        return broker.submit(order)
```

## Runtime demo

Script `/tmp/phase8_demo.py` (complete, visible):
```python
import sys; sys.path.insert(0, "/home/user/fin-trade")
"""Phase 8 runtime demo: order types, shared fill pricing, fees, P&L with
entry fees, duplicate window + rate caps, OCO/bracket, gateway-only submit."""
import logging
logging.disable(logging.CRITICAL)

from data.database import DatabaseManager
from trading.order_types import Order, OrderType, OrderState
from trading.paper_broker import PaperBroker

clock = {"t": 0.0}
def now():
    return clock["t"]

db = DatabaseManager(":memory:")
b = PaperBroker(db=db, clock=now, fee_bps=10.0, slippage_bps=0.0)

# 1) market buy -> fill with 10bps fee
o1 = b.place_order(Order("AAPL", "buy", 10, price=100.0))
print("market buy:  state=%s fill=%.2f fee=%.4f cash=%.2f pos=%s"
      % (o1.state.value, o1.filled_price, b.fills[-1].fee, b.cash, b.positions))

# 2) limit sell resting then filled via mark
o2 = b.place_order(Order("AAPL", "sell", 10, type=OrderType.LIMIT, limit_price=105.0, price=100.0))
print("limit sell:  state=%s (resting)" % o2.state.value)
b.mark_price("AAPL", 106.0, high=106.5, low=105.2)
print("after mark:  state=%s fill=%.2f realized=%.4f pos=%s"
      % (o2.state.value, o2.filled_price, b.realized_pnl, b.positions))

# 3) duplicate window: same order resubmitted at t=5 must be rejected
clock["t"] = 5.0
dup = b.place_order(Order("AAPL", "buy", 10, price=100.0))
print("duplicate:   state=%s reason=%s" % (dup.state.value, dup.reject_reason))

# 4) rate cap: 10 orders now, 11th rejected
for i in range(10):
    b.place_order(Order("SYM%d" % i, "buy", 1, price=100.0))
clock["t"] = 6.0
eleventh = b.place_order(Order("SYM10", "buy", 1, price=100.0))
print("rate cap:    state=%s reason=%s" % (eleventh.state.value, eleventh.reject_reason))

# 5) trailing stop ratchet + trigger
b2 = PaperBroker(clock=now, fee_bps=0.0, slippage_bps=0.0)
b2.place_order(Order("NVDA", "buy", 10, price=100.0))
tr = b2.place_order(Order("NVDA", "sell", 10, type=OrderType.TRAILING_STOP, trail_pct=0.02, price=100.0))
b2.mark_price("NVDA", 104.0); b2.mark_price("NVDA", 103.0)
print("trailing:    anchor=%.2f state=%s" % (tr.trail_anchor, tr.state.value))
b2.mark_price("NVDA", 101.8, low=101.8)
print("after stop:  state=%s fill=%.2f pos=%s" % (tr.state.value, tr.filled_price, b2.positions))

# 6) OCO one-cancels-other
b3 = PaperBroker(clock=now, fee_bps=0.0, slippage_bps=0.0)
b3.place_order(Order("MSFT", "buy", 10, price=100.0))
tp, sl = b3.place_oco([
    Order("MSFT", "sell", 10, type=OrderType.LIMIT, limit_price=110.0, price=100.0),
    Order("MSFT", "sell", 10, type=OrderType.STOP, stop_price=95.0, price=100.0),
])
b3.mark_price("MSFT", 111.0)
print("OCO:         tp=%s sl=%s (%s)" % (tp.state.value, sl.state.value, sl.cancel_reason))

# 7) bracket: entry + TP/SL children; SL fills, TP cancelled
b4 = PaperBroker(clock=now, fee_bps=0.0, slippage_bps=0.0)
entry, tp4, sl4 = b4.place_bracket(Order("GOOG", "buy", 10, price=100.0), take_profit=110.0, stop_loss=95.0)
b4.mark_price("GOOG", 94.0)
print("bracket:     entry=%s tp=%s (%s) sl=%s" % (entry.state.value, tp4.state.value, tp4.cancel_reason, sl4.state.value))

# 8) realized P&L includes entry fees (DB row)
closed = db.fetch_paper_trades(status="CLOSED")
row = closed.iloc[0]
print("P&L row:     gross math 10*(105-100)=50 minus entry fee 1.00 - exit fee 1.05 = %.4f"
      % (10 * (105.0 - 100.0) - 1.00 - 1.05))
print("             realized_pnl=%s fees_total=%s" % (round(float(row["realized_pnl"]), 4), round(float(row["fees"]), 4)))
print("ALL PHASE 8 DEMO CHECKS PASS")
```
stdout:
```text
market buy:  state=filled fill=100.00 fee=1.0000 cash=98999.00 pos={'AAPL': 10.0}
limit sell:  state=submitted (resting)
after mark:  state=filled fill=105.00 realized=47.9500 pos={}
duplicate:   state=rejected reason=duplicate_order:within_30s_window
rate cap:    state=rejected reason=order_rate:10/min_exceeded
trailing:    anchor=104.00 state=submitted
after stop:  state=filled fill=101.80 pos={}
OCO:         tp=filled sl=cancelled (oco:one_cancels_other)
bracket:     entry=filled tp=cancelled (bracket:twin_filled) sl=filled
P&L row:     gross math 10*(105-100)=50 minus entry fee 1.00 - exit fee 1.05 = 47.9500
             realized_pnl=47.95 fees_total=2.05
ALL PHASE 8 DEMO CHECKS PASS
```

## Diff stat from the inherited base (8d6e075)
```text
 backtest/fill_engine.py                |  91 +++++--
 data/database.py                       |  48 ++++
 docs/ARCHITECTURE.md                   |  26 +-
 docs/AUDIT_REPORT.md                   |  41 ++-
 docs/BUILD_PLAN.md                     |   8 +
 risk/position_limits.py                |   7 +
 tests/unit/test_phase8_order_types.py  | 343 ++++++++++++++++++++++++
 tests/unit/test_phase8_paper_broker.py | 391 +++++++++++++++++++++++++++
 trading/core.py                        |  54 +---
 trading/order_types.py                 | 390 +++++++++++++++++++++++++++
 trading/paper_broker.py                | 476 +++++++++++++++++++++++++++++++++
 11 files changed, 1810 insertions(+), 65 deletions(-)
```

## Docs update proof
```text
0d84395 Phase 8: mark BUILD_PLAN phase 8 implemented
8d6e075 Merge pull request #4 from surendra-ksr/arena/019fb78d-fin-trade
060d550 Phase 8: update architecture roadmap and audit report
8d6e075 Merge pull request #4 from surendra-ksr/arena/019fb78d-fin-trade
060d550 Phase 8: update architecture roadmap and audit report
8d6e075 Merge pull request #4 from surendra-ksr/arena/019fb78d-fin-trade
```

## Behavioral test names and one-line purposes
```text
test_state_machine_clean_lifecycle
test_state_machine_illegal_transitions_raise
test_terminal_states_accept_no_transitions
test_every_state_has_transitions_entry
test_transition_function_validates_both_ends
test_market_order_fills_immediately_at_bar_price
test_limit_buy_fills_when_low_touches_limit
test_limit_buy_rests_when_price_stays_above_limit
test_limit_sell_fills_when_high_touches_limit
test_limit_sell_rests_when_price_stays_below_limit
test_stop_buy_triggers_on_high_cross
test_stop_sell_triggers_on_low_cross
test_stop_not_triggered_when_range_stays_above_stop
test_stop_gap_through_buy_executes_at_open
test_stop_gap_through_sell_executes_at_open
test_stop_limit_triggers_then_works_as_limit
test_stop_limit_working_not_filled_when_limit_not_met
test_stop_limit_same_bar_fill_when_limit_already_crossed
test_stop_limit_rests_when_stop_not_crossed
test_trailing_sell_ratchets_up_and_never_back
test_trailing_buy_ratchets_down_and_never_back
test_trailing_gap_through_executes_at_open
test_ratchet_trailing_requires_trailing_type
test_oco_one_cancels_other_on_fill
test_oco_unrelated_orders_are_untouched
test_bracket_children_arm_only_after_entry_fill
test_bracket_child_fill_cancels_twin
test_invalid_order_parameters_rejected
test_container_types_are_never_evaluated_directly
test_fills_reuse_the_single_shared_pricing_core
test_market_order_fills_with_fee_and_cash_impact
test_market_sell_reduces_position_and_adds_cash
test_limit_order_rests_then_fills_on_mark
test_stop_order_triggers_on_mark_cross
test_trailing_stop_ratchets_in_broker_and_triggers
test_realized_pnl_long_includes_entry_and_exit_fees
test_realized_pnl_short_includes_entry_fees
test_partial_close_allocates_entry_fees_proportionally
test_db_paper_trade_rows_round_trip_through_broker
test_in_memory_realized_matches_db_math_with_slippage
test_duplicate_order_window_blocks_resubmission
test_duplicate_window_expiry_allows_resubmission
test_same_client_id_is_idempotent_within_window
test_duplicate_window_does_not_block_different_orders
test_order_rate_cap_10_per_minute_fires
test_order_rate_cap_is_a_rolling_window
test_gateway_denial_blocks_low_level_submit
test_place_order_routes_through_gateway_transmit
test_oco_one_cancels_other_via_broker
test_oco_same_bar_cross_fills_only_first_leg
test_bracket_arms_children_on_entry_fill_and_cancels_twin
test_bracket_with_resting_limit_entry_arms_children_later
test_short_position_round_trip_cash
test_non_positive_quantity_rejected
```
No corrections were required this phase; no history was rewritten; every commit was pushed
immediately (`git push origin arena/019fb79c-fin-trade` after each commit).
