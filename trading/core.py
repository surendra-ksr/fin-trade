"""Paper-first execution interfaces. Live adapters must implement Broker."""
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4
@dataclass(frozen=True)
class Order:
    symbol:str; side:str; quantity:float; type:str='market'; limit_price:float|None=None; client_id:str=''
class Broker(Protocol):
    def place_order(self, order:Order): ...
class PaperBroker:
    def __init__(self, cash=100000.): self.cash=float(cash); self.orders=[]
    def place_order(self, order):
        if order.quantity<=0: raise ValueError('quantity must be positive')
        order=Order(**{**order.__dict__,'client_id':order.client_id or uuid4().hex})
        self.orders.append(order); return order
