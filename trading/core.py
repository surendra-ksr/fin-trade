"""Paper-first broker facade. Public placement always routes through RiskGateway."""
from dataclasses import dataclass
from typing import Any, Optional
from uuid import uuid4
from risk.position_limits import OrderRequest, PortfolioSnapshot, RiskGateway

@dataclass(frozen=True)
class Order:
    symbol: str
    side: str
    quantity: float
    type: str = "market"
    limit_price: float | None = None
    client_id: str = ""
    price: float = 100.0

class PaperBroker:
    """In-memory broker whose low-level ``submit`` is unreachable publicly."""
    def __init__(self, cash: float = 100000.0, gateway: Optional[RiskGateway] = None):
        self.cash = float(cash)
        self.orders: list[Order] = []
        self.gateway = gateway or RiskGateway()

    def place_order(self, order: Order, portfolio: Optional[PortfolioSnapshot] = None) -> Order:
        """Mandatory gateway entry point for manual and automated orders."""
        request = OrderRequest(order.symbol, order.side, order.quantity, order.limit_price or order.price, client_id=order.client_id)
        snapshot = portfolio or PortfolioSnapshot(equity=self.cash, cash=self.cash)
        return self.gateway.transmit(self, request, snapshot)

    def submit(self, request: OrderRequest) -> Order:
        """Low-level transmission target; callers must use ``RiskGateway``."""
        if request.quantity <= 0:
            raise ValueError("quantity must be positive")
        order = Order(request.symbol, request.side, request.quantity, request.order_type, request.price, request.client_id or uuid4().hex, request.price)
        self.orders.append(order)
        return order

# Compatibility protocol for adapter construction; adapters expose submit only
# to RiskGateway.transmit, never directly to strategy code.
class Broker:
    def submit(self, request: OrderRequest) -> Any:
        raise NotImplementedError
