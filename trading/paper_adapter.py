"""Paper broker adapter — wraps the Phase-8 PaperBroker behind BrokerAdapter.

Default adapter. Implements the full :class:`BrokerAdapter` contract so the
same behavioral-equivalence test suite can run against paper and a fully
mocked Alpaca client. Placement still routes through ``RiskGateway``;
low-level ``submit`` remains reachable only from ``RiskGateway.transmit``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import uuid4

from risk.position_limits import OrderRequest, PortfolioSnapshot, RiskGateway
from trading.broker_base import (
    AccountSnapshot,
    BrokerAdapter,
    OrderResult,
    OrderStatus,
    PositionSnapshot,
    TerminalBrokerError,
    with_retry,
)
from trading.order_types import Order, OrderState
from trading.paper_broker import PaperBroker
from utils.config import AppConfig, load_config
from utils.logger import get_logger

__all__ = ["PaperBrokerAdapter"]

_log = get_logger("trading")

_STATE_MAP: Dict[OrderState, OrderStatus] = {
    OrderState.PENDING_NEW: OrderStatus.PENDING,
    OrderState.SUBMITTED: OrderStatus.SUBMITTED,
    OrderState.TRIGGERED: OrderStatus.SUBMITTED,
    OrderState.WORKING: OrderStatus.SUBMITTED,
    OrderState.FILLED: OrderStatus.FILLED,
    OrderState.CANCELLED: OrderStatus.CANCELLED,
    OrderState.REJECTED: OrderStatus.REJECTED,
    OrderState.EXPIRED: OrderStatus.CANCELLED,
}


def _to_result(order: Order) -> OrderResult:
    state = order.state if isinstance(order.state, OrderState) else OrderState(str(order.state))
    return OrderResult(
        order_id=order.client_id,
        client_id=order.client_id,
        symbol=order.symbol.upper(),
        side=str(order.side).lower(),
        quantity=float(order.quantity),
        status=_STATE_MAP.get(state, OrderStatus.UNKNOWN),
        filled_quantity=float(order.filled_quantity or 0.0),
        filled_price=order.filled_price,
        reject_reason=str(getattr(order, "reject_reason", "") or ""),
        raw={"state": state.value},
    )


class PaperBrokerAdapter(BrokerAdapter):
    """Adapter around the in-process :class:`PaperBroker`."""

    name = "paper"

    def __init__(
        self,
        *,
        paper: Optional[PaperBroker] = None,
        config: Optional[AppConfig] = None,
        gateway: Optional[RiskGateway] = None,
        **paper_kwargs: Any,
    ) -> None:
        self.config = config or load_config()
        if paper is not None:
            self.paper = paper
            if gateway is not None:
                self.paper.gateway = gateway
        else:
            self.paper = PaperBroker(config=self.config, gateway=gateway, **paper_kwargs)
        self.gateway = self.paper.gateway

    # ------------------------------------------------------------------
    # Gateway-routed placement helper (public convenience)
    # ------------------------------------------------------------------
    def place_order(
        self,
        request: OrderRequest,
        portfolio: Optional[PortfolioSnapshot] = None,
    ) -> OrderResult:
        """Public placement — ALWAYS routes through RiskGateway.transmit."""
        snap = portfolio or self.paper.portfolio_snapshot()
        order = self.gateway.transmit(self, request, snap)
        if isinstance(order, OrderResult):
            return order
        return _to_result(order)

    # ------------------------------------------------------------------
    # BrokerAdapter contract
    # ------------------------------------------------------------------
    def submit(self, request: OrderRequest) -> OrderResult:
        """Low-level transmission target; callers must use RiskGateway."""
        def _once() -> OrderResult:
            order = self.paper.submit(request)
            return _to_result(order)

        return with_retry(
            _once,
            config=self.config,
            # Paper is in-process; keep attempts at the config default but a
            # zero/near-zero timeout would be wrong — paper never blocks.
            timeout=max(float(self.config.broker.request_timeout_seconds), 1.0),
            label="paper.submit",
        )

    def cancel(self, order_id: str) -> OrderResult:
        def _once() -> OrderResult:
            for order in self.paper.orders:
                if order.client_id == order_id or getattr(order, "id", None) == order_id:
                    if order.is_terminal():
                        return _to_result(order)
                    order.transition(OrderState.CANCELLED)
                    return _to_result(order)
            raise TerminalBrokerError(f"unknown order: {order_id!r}")

        return with_retry(_once, config=self.config, label="paper.cancel")

    def replace(
        self,
        order_id: str,
        *,
        quantity: Optional[float] = None,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
    ) -> OrderResult:
        def _once() -> OrderResult:
            for order in self.paper.orders:
                if order.client_id != order_id and getattr(order, "id", None) != order_id:
                    continue
                if order.is_terminal():
                    raise TerminalBrokerError(f"cannot replace terminal order {order_id!r}")
                if quantity is not None:
                    order.quantity = float(quantity)
                if limit_price is not None:
                    order.limit_price = float(limit_price)
                if stop_price is not None:
                    order.stop_price = float(stop_price)
                return _to_result(order)
            raise TerminalBrokerError(f"unknown order: {order_id!r}")

        return with_retry(_once, config=self.config, label="paper.replace")

    def positions(self) -> List[PositionSnapshot]:
        def _once() -> List[PositionSnapshot]:
            out: List[PositionSnapshot] = []
            for sym, qty in self.paper.positions.items():
                if not qty:
                    continue
                side = "long" if qty > 0 else "short"
                px = float(self.paper.last_prices.get(sym, 0.0))
                out.append(PositionSnapshot(
                    symbol=sym,
                    quantity=abs(float(qty)),
                    side=side,
                    avg_entry_price=px,
                    market_value=abs(float(qty)) * px,
                ))
            return out

        return with_retry(_once, config=self.config, label="paper.positions")

    def orders(self, *, status: str = "open") -> List[OrderResult]:
        def _once() -> List[OrderResult]:
            want = status.lower()
            results: List[OrderResult] = []
            for order in self.paper.orders:
                result = _to_result(order)
                if want == "all":
                    results.append(result)
                elif want == "open" and result.status in {
                    OrderStatus.SUBMITTED, OrderStatus.PENDING, OrderStatus.PARTIAL,
                }:
                    results.append(result)
                elif want == "closed" and result.status in {
                    OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED,
                }:
                    results.append(result)
            return results

        return with_retry(_once, config=self.config, label="paper.orders")

    def account(self) -> AccountSnapshot:
        def _once() -> AccountSnapshot:
            snap = self.paper.portfolio_snapshot()
            return AccountSnapshot(
                equity=float(snap.equity),
                cash=float(snap.cash),
                buying_power=float(snap.cash),
                status="ACTIVE",
            )

        return with_retry(_once, config=self.config, label="paper.account")

    def cancel_all(self) -> List[OrderResult]:
        def _once() -> List[OrderResult]:
            cancelled: List[OrderResult] = []
            for order in list(self.paper.orders):
                if order.is_terminal():
                    continue
                order.transition(OrderState.CANCELLED)
                cancelled.append(_to_result(order))
            return cancelled

        return with_retry(_once, config=self.config, label="paper.cancel_all")

    def flatten(self) -> List[OrderResult]:
        """Market-close every open position via the gateway."""
        results: List[OrderResult] = []
        for snap in list(self.positions()):
            side = "sell" if snap.side == "long" else "buy"
            mark = float(self.paper.last_prices.get(snap.symbol, snap.avg_entry_price or 0.0))
            if mark <= 0:
                mark = 1.0
            req = OrderRequest(
                snap.symbol, side, snap.quantity, mark,
                order_type="market", client_id=uuid4().hex,
            )
            try:
                results.append(self.place_order(req))
            except PermissionError as exc:
                # Even under a halt, kill-switch flatten must still reduce
                # risk. Fall through to low-level submit for protective exits.
                _log.warning("flatten gateway denied ({}); using protective submit", exc)
                results.append(self.submit(req))
        return results
