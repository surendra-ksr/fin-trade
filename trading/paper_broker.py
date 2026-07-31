"""Real paper broker (Phase 8): order types, shared fill pricing, fees,
position tracking, realized P&L including entry fees, and idempotent
submission with the 30s duplicate window and 10 orders/min caps.

Fill pricing is NOT re-implemented here. Every fill is priced through
``backtest.fill_engine.price_fill`` — the single shared fill-pricing path
used by both the backtester and the paper broker — so the two execution
surfaces can never diverge.

Public placement always routes through ``RiskGateway``; the low-level
``submit`` is reachable only from ``RiskGateway.transmit`` (grep proof in the
Phase-8 evidence pack).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

from backtest.fill_engine import price_fill
from data.database import DatabaseManager
from risk.position_limits import OrderRequest, PortfolioSnapshot, Position, RiskGateway
from trading.order_types import (
    Bar,
    InvalidOrderError,
    Order,
    OrderState,
    OrderType,
    cancel_bracket_twin,
    cancel_oco_siblings,
    evaluate_trigger,
    order_fingerprint,
)
from utils.config import AppConfig, load_config


@dataclass(frozen=True)
class FillRecord:
    """One executed fill in the paper ledger."""

    symbol: str
    side: str
    quantity: float
    price: float
    fee: float
    slippage: float
    time: float
    order_id: str


@dataclass
class OpenTrade:
    """FIFO lot: an open DB row (or in-memory mirror when no DB is used).

    ``entry_ref`` is the pre-slippage reference price; the entry fee and
    slippage are carried as separate costs exactly like ``paper_trades``
    rows, so ``db.close_paper_trade`` and the in-memory formula agree.
    """

    trade_id: Optional[int]        # None when no DatabaseManager is attached
    symbol: str
    side: str                      # "BUY" (long) | "SELL" (short)
    quantity: float                # original quantity of the lot
    remaining: float               # un-closed quantity
    entry_ref: float               # reference entry price (pre-slippage)
    entry_fee: float               # total entry fee for the original lot
    entry_slip: float              # total entry slippage cost for the lot


def _exit_side(entry: Order) -> str:
    """The closing side for a bracket/OCO leg of an entry order."""
    return "buy" if entry.side.lower() in {"sell", "sell_short"} else "sell"


class PaperBroker:
    """Idempotent, gateway-gated paper broker with real fills and P&L."""

    def __init__(
        self,
        cash: float = 100000.0,
        gateway: Optional[RiskGateway] = None,
        db: Optional[DatabaseManager] = None,
        config: Optional[AppConfig] = None,
        clock: Optional[Any] = None,
        rng: Optional[Any] = None,
        fee_bps: Optional[float] = None,
        slippage_bps: Optional[float] = None,
        partial_fill_prob: float = 0.0,
        portfolio_id: str = "default",
    ) -> None:
        self.config = config or load_config()
        self.cash = float(cash)
        self.db = db
        self.gateway = gateway or RiskGateway(config=self.config, db=self.db)
        self._clock = clock or time.monotonic
        self._rng = rng
        self.fee_bps = float(fee_bps) if fee_bps is not None else float(self.config.backtesting.commission) * 10000.0
        self.slippage_bps = float(slippage_bps) if slippage_bps is not None else float(self.config.backtesting.slippage) * 10000.0
        self.partial_fill_prob = float(partial_fill_prob)
        self.portfolio_id = portfolio_id
        self.orders: list[Order] = []
        self.fills: list[FillRecord] = []
        self.positions: Dict[str, float] = {}
        self.last_prices: Dict[str, float] = {}
        self.open_trades: Dict[str, List[OpenTrade]] = {}
        self.realized_pnl: float = 0.0
        self._submission_times: List[float] = []
        self._duplicate_map: Dict[str, float] = {}
        self._pending_children: Dict[str, List[OrderRequest]] = {}

    # ------------------------------------------------------------------
    # Portfolio snapshot for the gateway
    # ------------------------------------------------------------------
    def portfolio_snapshot(self) -> PortfolioSnapshot:
        """Current portfolio state; used when callers omit a snapshot."""
        positions = [
            Position(sym, qty, self.last_prices.get(sym, 0.0))
            for sym, qty in self.positions.items()
            if qty
        ]
        equity = self.cash + sum(abs(qty) * self.last_prices.get(sym, 0.0) for sym, qty in self.positions.items())
        return PortfolioSnapshot(equity=equity, cash=self.cash, positions=positions)

    # ------------------------------------------------------------------
    # Public placement — the only gateway entry point
    # ------------------------------------------------------------------
    def place_order(self, order: Order, portfolio: Optional[PortfolioSnapshot] = None) -> Order:
        """Mandatory gateway entry point for manual and automated orders.

        The order is converted to an ``OrderRequest`` and transmitted only
        through ``RiskGateway.transmit``, which evaluates every configured
        limit and then calls the low-level ``submit``.
        """
        # ``price`` is the market reference used by the gateway's exposure
        # checks and by the broker's marketability evaluation; ``limit_price``
        # is the resting limit level for limit/stop-limit orders.
        request = OrderRequest(
            order.symbol,
            order.side,
            order.quantity,
            order.price,
            order_type=order.type.value if isinstance(order.type, OrderType) else str(order.type),
            client_id=order.client_id,
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            trail_pct=order.trail_pct,
            oco_group=order.oco_group,
            parent_id=order.parent_id,
        )
        snapshot = portfolio or self.portfolio_snapshot()
        return self.gateway.transmit(self, request, snapshot)

    def place_oco(self, orders: Sequence[Order], portfolio: Optional[PortfolioSnapshot] = None) -> List[Order]:
        """Submit a one-cancels-other group: the first leg that fills cancels
        the other legs (each leg is transmitted through the gateway)."""
        group = uuid4().hex
        placed: List[Order] = []
        for order in orders:
            order.oco_group = group
            placed.append(self.place_order(order, portfolio))
        return placed

    def place_bracket(
        self,
        entry: Order,
        take_profit: float,
        stop_loss: float,
        portfolio: Optional[PortfolioSnapshot] = None,
    ) -> Tuple[Order, Order, Order]:
        """Submit a bracket: entry + take-profit limit + stop-loss.

        Children are transmitted through the gateway only after the entry
        actually fills (``_pending_children`` otherwise). When one child
        fills, the sibling child is cancelled.
        """
        if not entry.client_id:
            entry.client_id = uuid4().hex
        placed_entry = self.place_order(entry, portfolio)
        exit_side = _exit_side(entry)
        # Children rest at their trigger levels; their request ``price`` is the
        # current market mark so they are not immediately marketable.
        mark = self.last_prices.get(entry.symbol.upper(), entry.price)
        tp_req = OrderRequest(
            entry.symbol, exit_side, entry.quantity, mark,
            order_type="limit", limit_price=take_profit,
            client_id=uuid4().hex, parent_id=placed_entry.client_id,
        )
        sl_req = OrderRequest(
            entry.symbol, exit_side, entry.quantity, mark,
            order_type="stop", stop_price=stop_loss,
            client_id=uuid4().hex, parent_id=placed_entry.client_id,
        )
        children: List[Optional[Order]] = []
        if placed_entry.state is OrderState.FILLED:
            children = [
                self.gateway.transmit(self, req, portfolio or self.portfolio_snapshot())
                for req in (tp_req, sl_req)
            ]
        else:
            # Transmitted later by ``_fill_order`` when the entry fills.
            self._pending_children.setdefault(placed_entry.client_id, []).extend((tp_req, sl_req))
        return placed_entry, children[0] if children else None, children[1] if len(children) > 1 else None

    # ------------------------------------------------------------------
    # Low-level transmission target — called ONLY by RiskGateway.transmit
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Idempotency: 30s duplicate window + 10 orders/min (both caps fire)
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Market updates: evaluate every resting order against a price mark
    # ------------------------------------------------------------------
    def mark_price(
        self,
        symbol: str,
        price: float,
        high: Optional[float] = None,
        low: Optional[float] = None,
        now: Optional[float] = None,
    ) -> None:
        """Advance the market for one symbol and evaluate resting orders.

        ``high``/``low`` model the intrabar range for trigger purposes;
        defaults collapse to the mark price. OCO siblings and bracket twins
        are cancelled immediately after a fill, so a same-bar crossing of
        both legs deterministically fills only the first leg evaluated.
        """
        symbol = symbol.upper()
        self.last_prices[symbol] = float(price)
        high = float(high) if high is not None else float(price)
        low = float(low) if low is not None else float(price)
        now = float(now) if now is not None else float(self._clock())
        for order in list(self.orders):
            if order.symbol.upper() != symbol or order.is_terminal():
                continue
            if OrderType.coerce(order.type) in (OrderType.OCO, OrderType.BRACKET):
                continue
            self._evaluate_order_fill(order, price=float(price), high=high, low=low, now=now)

    def _evaluate_order_fill(
        self,
        order: Order,
        price: float,
        high: Optional[float] = None,
        low: Optional[float] = None,
        now: Optional[float] = None,
    ) -> None:
        now = float(now) if now is not None else float(self._clock())
        high = float(high) if high is not None else float(price)
        low = float(low) if low is not None else float(price)
        otype = OrderType.coerce(order.type)
        if otype in (OrderType.OCO, OrderType.BRACKET):
            raise InvalidOrderError(f"{otype.value} is a container order and is never filled directly")
        decision = evaluate_trigger(
            order, Bar(high=high, low=low, close=float(price), open=float(price))
        )
        if decision.fill:
            self._fill_order(order, ref_price=decision.fill_price or price, now=now)
        elif decision.state_after is not order.state:
            order.transition(decision.state_after)

    # ------------------------------------------------------------------
    # Fill execution — priced through the SHARED backtest.fill_engine core
    # ------------------------------------------------------------------
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
        pending = self._pending_children.pop(order.client_id, [])
        for child_request in pending:
            self.gateway.transmit(self, child_request, self.portfolio_snapshot())

    # ------------------------------------------------------------------
    # Position ledger + realized P&L (entry fees included)
    # ------------------------------------------------------------------
    def _apply_position(
        self, order: Order, ref_price: float, qty: float, fee: float, slip: float, now: float
    ) -> None:
        symbol = order.symbol.upper()
        delta = qty if order.side.lower() in {"buy", "cover"} else -qty
        pos = self.positions.get(symbol, 0.0)
        same_direction = (pos >= 0.0 and delta >= 0.0) or (pos <= 0.0 and delta <= 0.0)
        if pos == 0.0 or same_direction:
            self._open_lot(symbol, order.side, qty, ref_price, fee, slip, now)
            self.positions[symbol] = pos + delta
            return
        # Opposite direction: close FIFO lots, then open the remainder.
        remaining = abs(delta)
        exit_fee_share_total = 0.0
        exit_slip_share_total = 0.0
        while remaining > 0.0 and self.open_trades.get(symbol):
            trade = self.open_trades[symbol][0]
            close_qty = min(remaining, trade.remaining)
            fee_share = fee * (close_qty / qty)
            slip_share = slip * (close_qty / qty)
            exit_fee_share_total += fee_share
            exit_slip_share_total += slip_share
            realized = self._close_lot(symbol, trade, close_qty, ref_price, fee_share, slip_share, now)
            self.realized_pnl += realized
            remaining -= close_qty
        self.positions[symbol] = pos + delta
        if self.positions[symbol] == 0.0:
            del self.positions[symbol]
        if remaining > 0.0:
            self._open_lot(symbol, order.side, remaining, ref_price, fee - exit_fee_share_total, slip - exit_slip_share_total, now)

    def _open_lot(
        self, symbol: str, side: str, qty: float, ref_price: float, fee: float, slip: float, now: float
    ) -> None:
        trade_id: Optional[int] = None
        if self.db is not None:
            trade_id = self.db.insert_paper_trade(
                symbol, "BUY" if side.lower() in {"buy", "cover"} else "SELL",
                qty, self._iso(now), ref_price,
                portfolio_id=self.portfolio_id, fees=fee, slippage_cost=slip,
            )
        self.open_trades.setdefault(symbol, []).append(OpenTrade(
            trade_id=trade_id, symbol=symbol,
            side="BUY" if side.lower() in {"buy", "cover"} else "SELL",
            quantity=qty, remaining=qty, entry_ref=ref_price,
            entry_fee=fee, entry_slip=slip,
        ))

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

    @staticmethod
    def _realized_formula(trade: OpenTrade, qty: float, exit_ref: float, exit_fee: float, exit_slip: float) -> float:
        """In-memory realized P&L — identical math to ``db.close_paper_trade``:
        directional gross minus entry fees, entry slippage, exit fees and exit
        slippage (entry costs allocated proportionally to the closed slice)."""
        direction = 1.0 if trade.side == "BUY" else -1.0
        gross = direction * (float(exit_ref) - trade.entry_ref) * qty
        slice_fee = trade.entry_fee * (qty / trade.remaining)
        slice_slip = trade.entry_slip * (qty / trade.remaining)
        return gross - slice_fee - slice_slip - exit_fee - exit_slip

    @staticmethod
    def _iso(now: float) -> str:
        return datetime.fromtimestamp(now, tz=timezone.utc).isoformat()


# Compatibility protocol for adapter construction; adapters expose submit only
# to RiskGateway.transmit, never directly to strategy code.
class Broker:
    def submit(self, request: OrderRequest) -> Any:
        raise NotImplementedError
