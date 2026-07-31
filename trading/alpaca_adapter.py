"""Alpaca broker adapter (Phase 10) — behind live gate + optional alpaca-py.

The adapter never opens a network connection from unit tests. Production
construction goes through :func:`trading.broker_base.build_broker`, which
requires a fully-passing live gate. Tests inject a fully-mocked client
object that satisfies the small surface used here:

* ``submit_order(**kwargs)``
* ``cancel_order_by_id(order_id)``
* ``replace_order_by_id(order_id, **kwargs)`` (optional)
* ``get_all_positions()``
* ``get_orders(filter=...)`` / ``get_orders()``
* ``get_account()``
* ``cancel_orders()``
* ``close_all_positions(cancel_orders=True)`` (optional; flatten falls back)

``alpaca-py`` lives in ``requirements-optional.txt`` and is imported lazily
only when no client is injected AND the live gate has already passed.
"""
from __future__ import annotations

import os
from typing import Any, List, Mapping, Optional
from urllib.parse import urlparse
from uuid import uuid4

from risk.position_limits import OrderRequest, PortfolioSnapshot, RiskGateway
from trading.broker_base import (
    AccountSnapshot,
    BrokerAdapter,
    LiveGateEvidence,
    OrderResult,
    OrderStatus,
    PositionSnapshot,
    RetryableBrokerError,
    TerminalBrokerError,
    evaluate_live_gate,
    with_retry,
)
from utils.config import AppConfig, load_config
from utils.logger import get_logger

__all__ = ["AlpacaBrokerAdapter", "MockAlpacaClient", "MockAlpacaError", "is_paper_base_url"]

_log = get_logger("trading")

PAPER_ALPACA_HOST = "paper-api.alpaca.markets"


def is_paper_base_url(base_url: str) -> bool:
    """Return True only for the canonical Alpaca paper trading endpoint."""
    parsed = urlparse(str(base_url or "").strip())
    host = (parsed.netloc or parsed.path).split("/", 1)[0].lower()
    return host == PAPER_ALPACA_HOST


class MockAlpacaError(Exception):
    """Exception carrying an Alpaca-like error payload for mocked tests."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = dict(payload)
        code = self.payload.get("code", "error")
        message = self.payload.get("message", self.payload.get("error", "mock alpaca error"))
        super().__init__(f"{code}: {message}")


# Map Alpaca status strings onto our normalized enum.
_STATUS_MAP = {
    "new": OrderStatus.SUBMITTED,
    "accepted": OrderStatus.ACCEPTED,
    "pending_new": OrderStatus.PENDING,
    "accepted_for_bidding": OrderStatus.PENDING,
    "stopped": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
    "suspended": OrderStatus.REJECTED,
    "calculated": OrderStatus.PARTIAL,
    "partially_filled": OrderStatus.PARTIAL,
    "filled": OrderStatus.FILLED,
    "done_for_day": OrderStatus.CANCELLED,
    "canceled": OrderStatus.CANCELLED,
    "cancelled": OrderStatus.CANCELLED,
    "expired": OrderStatus.CANCELLED,
    "replaced": OrderStatus.REPLACED,
    "pending_cancel": OrderStatus.PENDING,
    "pending_replace": OrderStatus.PENDING,
}


def _status_of(raw: Any) -> OrderStatus:
    if raw is None:
        return OrderStatus.UNKNOWN
    key = str(getattr(raw, "value", raw)).lower()
    return _STATUS_MAP.get(key, OrderStatus.UNKNOWN)


def _attr(obj: Any, *names: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        for n in names:
            if n in obj:
                return obj[n]
        return default
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


def _order_result(raw: Any, *, fallback_request: Optional[OrderRequest] = None) -> OrderResult:
    symbol = str(_attr(raw, "symbol", default=(fallback_request.symbol if fallback_request else ""))).upper()
    side = str(_attr(raw, "side", default=(fallback_request.side if fallback_request else ""))).lower()
    qty = float(_attr(raw, "qty", "quantity", default=(fallback_request.quantity if fallback_request else 0.0)) or 0.0)
    filled_qty = float(_attr(raw, "filled_qty", "filled_quantity", default=0.0) or 0.0)
    filled_px = _attr(raw, "filled_avg_price", "filled_price", default=None)
    order_id = str(_attr(raw, "id", "order_id", default="") or uuid4().hex)
    client_id = str(_attr(raw, "client_order_id", "client_id", default="") or order_id)
    status = _status_of(_attr(raw, "status", default="unknown"))
    reason = str(_attr(raw, "reject_reason", "failed_at", default="") or "")
    return OrderResult(
        order_id=order_id,
        client_id=client_id,
        symbol=symbol,
        side=side,
        quantity=qty,
        status=status,
        filled_quantity=filled_qty,
        filled_price=float(filled_px) if filled_px not in (None, "") else None,
        reject_reason=reason,
        raw={"source": "alpaca"},
    )


def _classify(exc: BaseException) -> BaseException:
    """Map vendor / transport exceptions onto our taxonomy."""
    if isinstance(exc, (RetryableBrokerError, TerminalBrokerError)):
        return exc
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    retryable_markers = (
        "timeout", "timed out", "connection", "temporarily", "429",
        "rate limit", "503", "502", "500", "reset", "unavailable",
    )
    terminal_markers = (
        "unauthorized", "forbidden", "401", "403", "invalid",
        "rejected", "not found", "404", "insufficient",
    )
    if any(m in name or m in msg for m in terminal_markers):
        return TerminalBrokerError(str(exc), cause=exc)
    if any(m in name or m in msg for m in retryable_markers):
        return RetryableBrokerError(str(exc), cause=exc)
    # Default: retryable for unknown transport noise; programming errors
    # raised by our own code stay terminal via with_retry's bare Exception path
    # only when they escape the client. Client-raised RuntimeError etc. are
    # treated as retryable so a flaky mock still exercises the wrapper.
    return RetryableBrokerError(str(exc), cause=exc)


class AlpacaBrokerAdapter(BrokerAdapter):
    """Alpaca adapter. Network is never opened from unit tests."""

    name = "alpaca"

    def __init__(
        self,
        *,
        config: Optional[AppConfig] = None,
        client: Any = None,
        gateway: Optional[RiskGateway] = None,
        base_url: Optional[str] = None,
        live_gate_evidence: Optional[LiveGateEvidence] = None,
    ) -> None:
        self.config = config or load_config()
        self.gateway = gateway or RiskGateway(config=self.config)
        alp = self.config.broker.alpaca
        self.base_url = str(base_url or alp.base_url or self.config.broker.alpaca_base_url)
        self.alpaca_mode = str(alp.mode or "paper").lower()
        if self._requires_live_gate():
            gate = evaluate_live_gate(
                self.config,
                live_gate_evidence or LiveGateEvidence(),
                broker_name="alpaca",
            )
            gate.raise_if_denied()
        self._client = client if client is not None else self._build_live_client()

    def _requires_live_gate(self) -> bool:
        """Fail-closed URL gate: live mode or non-paper URL requires all-pass live evidence."""
        return self.alpaca_mode == "live" or not is_paper_base_url(self.base_url)

    def _retry(self, func: Any, *, label: str) -> Any:
        """Run one Alpaca call with the nested broker.alpaca retry policy."""
        alp = self.config.broker.alpaca
        return with_retry(
            func,
            config=self.config,
            attempts=int(alp.max_retries),
            base_delay=float(alp.retry_delay_seconds),
            timeout=float(alp.request_timeout_seconds),
            label=label,
        )

    def _build_live_client(self) -> Any:
        """Lazy alpaca-py construction — only after the URL/live gate has passed."""
        try:
            from alpaca.trading.client import TradingClient  # type: ignore
        except ImportError as exc:
            raise TerminalBrokerError(
                "alpaca-py is required for the live Alpaca adapter "
                "(install via requirements-optional.txt)",
                cause=exc,
            ) from exc
        key = os.environ.get("APCA_API_KEY_ID") or self.config.api_keys.alpaca_api_key
        secret = os.environ.get("APCA_API_SECRET_KEY") or self.config.api_keys.alpaca_secret_key
        if not key or not secret:
            raise TerminalBrokerError(
                "alpaca credentials missing (APCA_API_KEY_ID / APCA_API_SECRET_KEY)"
            )
        paper = self.alpaca_mode == "paper" and is_paper_base_url(self.base_url)
        _log.info("constructing Alpaca TradingClient (paper={}, base_url={})", paper, self.base_url)
        return TradingClient(key, secret, paper=paper, url_override=self.base_url)

    # ------------------------------------------------------------------
    # Gateway-routed placement helper
    # ------------------------------------------------------------------
    def place_order(
        self,
        request: OrderRequest,
        portfolio: Optional[PortfolioSnapshot] = None,
    ) -> OrderResult:
        """Public placement — ALWAYS routes through RiskGateway.transmit."""
        if portfolio is None:
            acct = self.account()
            portfolio = PortfolioSnapshot(equity=acct.equity, cash=acct.cash, positions=[])
        result = self.gateway.transmit(self, request, portfolio)
        if isinstance(result, OrderResult):
            return result
        return _order_result(result, fallback_request=request)

    # ------------------------------------------------------------------
    # BrokerAdapter contract (every call through with_retry)
    # ------------------------------------------------------------------
    def submit(self, request: OrderRequest) -> OrderResult:
        """Low-level transmission target; callers must use RiskGateway."""
        def _once() -> OrderResult:
            try:
                payload = {
                    "symbol": request.symbol.upper(),
                    "qty": float(request.quantity),
                    "side": "buy" if request.side.lower() in {"buy", "cover"} else "sell",
                    "type": (request.order_type or "market").lower(),
                    "time_in_force": "day",
                    "client_order_id": request.client_id or uuid4().hex,
                }
                if request.limit_price is not None:
                    payload["limit_price"] = float(request.limit_price)
                if request.stop_price is not None:
                    payload["stop_price"] = float(request.stop_price)
                raw = self._client.submit_order(**payload)
                return _order_result(raw, fallback_request=request)
            except Exception as exc:
                raise _classify(exc) from exc

        return self._retry(_once, label="alpaca.submit")

    def cancel(self, order_id: str) -> OrderResult:
        def _once() -> OrderResult:
            try:
                raw = self._client.cancel_order_by_id(order_id)
                if raw is None:
                    return OrderResult(
                        order_id=order_id, client_id=order_id, symbol="",
                        side="", quantity=0.0, status=OrderStatus.CANCELLED,
                    )
                return _order_result(raw)
            except Exception as exc:
                raise _classify(exc) from exc

        return self._retry(_once, label="alpaca.cancel")

    def replace(
        self,
        order_id: str,
        *,
        quantity: Optional[float] = None,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
    ) -> OrderResult:
        def _once() -> OrderResult:
            try:
                if not hasattr(self._client, "replace_order_by_id"):
                    raise TerminalBrokerError("client does not support replace_order_by_id")
                kwargs: dict[str, Any] = {}
                if quantity is not None:
                    kwargs["qty"] = float(quantity)
                if limit_price is not None:
                    kwargs["limit_price"] = float(limit_price)
                if stop_price is not None:
                    kwargs["stop_price"] = float(stop_price)
                raw = self._client.replace_order_by_id(order_id, **kwargs)
                return _order_result(raw)
            except Exception as exc:
                raise _classify(exc) from exc

        return self._retry(_once, label="alpaca.replace")

    def positions(self) -> List[PositionSnapshot]:
        def _once() -> List[PositionSnapshot]:
            try:
                raw_list = self._client.get_all_positions()
                out: List[PositionSnapshot] = []
                for raw in raw_list or []:
                    qty = float(_attr(raw, "qty", "quantity", default=0.0) or 0.0)
                    side = str(_attr(raw, "side", default="long")).lower()
                    if qty == 0:
                        continue
                    out.append(PositionSnapshot(
                        symbol=str(_attr(raw, "symbol", default="")).upper(),
                        quantity=abs(qty),
                        side="long" if side in {"long", "buy"} else "short",
                        avg_entry_price=float(_attr(raw, "avg_entry_price", default=0.0) or 0.0),
                        market_value=float(_attr(raw, "market_value", default=0.0) or 0.0),
                        unrealized_pnl=float(_attr(raw, "unrealized_pl", "unrealized_pnl", default=0.0) or 0.0),
                    ))
                return out
            except Exception as exc:
                raise _classify(exc) from exc

        return self._retry(_once, label="alpaca.positions")

    def orders(self, *, status: str = "open") -> List[OrderResult]:
        def _once() -> List[OrderResult]:
            try:
                if not hasattr(self._client, "get_orders"):
                    return []
                collected: List[Any] = []
                page_token: Optional[str] = None
                while True:
                    try:
                        raw_page = self._client.get_orders(
                            status=status, limit=100, page_token=page_token,
                        )
                    except TypeError:
                        raw_page = self._client.get_orders(status=status)
                    next_token: Optional[str] = None
                    if isinstance(raw_page, Mapping) and "items" in raw_page:
                        collected.extend(list(raw_page.get("items") or []))
                        next_token = raw_page.get("next_page_token") or None
                    else:
                        collected.extend(list(raw_page or []))
                    if not next_token:
                        break
                    page_token = str(next_token)
                return [_order_result(r) for r in collected]
            except Exception as exc:
                raise _classify(exc) from exc

        return self._retry(_once, label="alpaca.orders")

    def account(self) -> AccountSnapshot:
        def _once() -> AccountSnapshot:
            try:
                raw = self._client.get_account()
                account_id = _attr(raw, "id", "account_id", "account_number", default="")
                return AccountSnapshot(
                    equity=float(_attr(raw, "equity", default=0.0) or 0.0),
                    cash=float(_attr(raw, "cash", default=0.0) or 0.0),
                    buying_power=float(_attr(raw, "buying_power", default=0.0) or 0.0),
                    currency=str(_attr(raw, "currency", default="USD") or "USD"),
                    status=str(_attr(raw, "status", default="ACTIVE") or "ACTIVE"),
                    raw={"source": "alpaca", "account_id": str(account_id or "")},
                )
            except Exception as exc:
                raise _classify(exc) from exc

        return self._retry(_once, label="alpaca.account")

    def cancel_all(self) -> List[OrderResult]:
        def _once() -> List[OrderResult]:
            try:
                if hasattr(self._client, "cancel_orders"):
                    raw = self._client.cancel_orders()
                    if isinstance(raw, list):
                        return [_order_result(r) for r in raw]
                    # Some clients return None / count; synthesize from open orders.
                open_orders = []
                if hasattr(self._client, "get_orders"):
                    open_orders = list(self._client.get_orders(status="open") or [])
                    for o in open_orders:
                        oid = _attr(o, "id", "order_id")
                        if oid is not None and hasattr(self._client, "cancel_order_by_id"):
                            self._client.cancel_order_by_id(oid)
                return [
                    OrderResult(
                        order_id=str(_attr(o, "id", "order_id", default=uuid4().hex)),
                        client_id=str(_attr(o, "client_order_id", "client_id", default="")),
                        symbol=str(_attr(o, "symbol", default="")).upper(),
                        side=str(_attr(o, "side", default="")).lower(),
                        quantity=float(_attr(o, "qty", "quantity", default=0.0) or 0.0),
                        status=OrderStatus.CANCELLED,
                    )
                    for o in open_orders
                ]
            except Exception as exc:
                raise _classify(exc) from exc

        return self._retry(_once, label="alpaca.cancel_all")

    def flatten(self) -> List[OrderResult]:
        def _once() -> List[OrderResult]:
            try:
                if hasattr(self._client, "close_all_positions"):
                    raw = self._client.close_all_positions(cancel_orders=True)
                    if isinstance(raw, list):
                        return [_order_result(r) for r in raw]
                    return []
                # Fallback: submit market closes for each open position.
                results: List[OrderResult] = []
                for pos in self.positions():
                    side = "sell" if pos.side == "long" else "buy"
                    req = OrderRequest(
                        pos.symbol, side, pos.quantity, pos.avg_entry_price or 0.0,
                        order_type="market", client_id=uuid4().hex,
                    )
                    results.append(self.submit(req))
                return results
            except Exception as exc:
                raise _classify(exc) from exc

        return self._retry(_once, label="alpaca.flatten")


# =============================================================================
# Fully-mocked Alpaca client for zero-network tests
# =============================================================================


class MockAlpacaClient:
    """In-memory stand-in for alpaca-py TradingClient.

    Zero network. Deterministic. Used by the shared adapter contract suite
    to prove behavioral equivalence with the paper adapter.
    """

    def __init__(
        self,
        *,
        equity: float = 100_000.0,
        cash: float = 100_000.0,
        fail_times: int = 0,
        fail_exc: Optional[BaseException] = None,
        base_url: str = "https://paper-api.alpaca.markets",
        mode: str = "paper",
        page_size: Optional[int] = None,
        account_id: str = "PAPER-ACCOUNT-000000",
    ) -> None:
        self.equity = float(equity)
        self.cash = float(cash)
        self.base_url = str(base_url)
        self.mode = str(mode).lower()
        self.paper = self.mode == "paper" and is_paper_base_url(self.base_url)
        self.page_size = page_size
        self.account_id = account_id
        self._orders: dict[str, dict[str, Any]] = {}
        self._positions: dict[str, dict[str, Any]] = {}
        self._fail_remaining = int(fail_times)
        self._fail_exc = fail_exc or RetryableBrokerError("mock transient")
        self._error_payloads: dict[str, list[Mapping[str, Any]]] = {}
        self.calls: list[tuple[str, tuple, dict]] = []

    def queue_error(self, op: str, payload: Mapping[str, Any]) -> None:
        """Queue one Alpaca-style error payload for operation ``op``."""
        self._error_payloads.setdefault(op, []).append(dict(payload))

    def _maybe_fail(self, op: str) -> None:
        self.calls.append((op, (), {}))
        if self._error_payloads.get(op):
            raise MockAlpacaError(self._error_payloads[op].pop(0))
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            raise self._fail_exc

    def submit_order(self, **kwargs: Any) -> dict[str, Any]:
        self._maybe_fail("submit_order")
        order_id = str(kwargs.get("client_order_id") or uuid4().hex)
        side = str(kwargs.get("side", "buy")).lower()
        qty = float(kwargs.get("qty", 0.0))
        symbol = str(kwargs.get("symbol", "")).upper()
        otype = str(kwargs.get("type", "market")).lower()
        status = "filled" if otype == "market" else "new"
        filled_qty = qty if status == "filled" else 0.0
        filled_px = float(kwargs.get("limit_price") or kwargs.get("stop_price") or 100.0)
        order = {
            "id": order_id,
            "client_order_id": order_id,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "status": status,
            "filled_qty": filled_qty,
            "filled_avg_price": filled_px if filled_qty else None,
            "limit_price": kwargs.get("limit_price"),
            "stop_price": kwargs.get("stop_price"),
            "type": otype,
            "endpoint": self.base_url,
            "paper": self.paper,
        }
        self._orders[order_id] = order
        if status == "filled" and qty:
            self._apply_fill(symbol, side, qty, filled_px)
        return dict(order)

    def _apply_fill(self, symbol: str, side: str, qty: float, price: float) -> None:
        pos = self._positions.get(symbol)
        signed = qty if side == "buy" else -qty
        notional = qty * price
        if side == "buy":
            self.cash -= notional
        else:
            self.cash += notional
        if pos is None:
            self._positions[symbol] = {
                "symbol": symbol,
                "qty": abs(signed),
                "side": "long" if signed > 0 else "short",
                "avg_entry_price": price,
                "market_value": abs(signed) * price,
                "unrealized_pl": 0.0,
            }
            return
        cur_signed = abs(float(pos["qty"])) * (1.0 if pos["side"] == "long" else -1.0)
        new_signed = cur_signed + signed
        if new_signed == 0:
            del self._positions[symbol]
        else:
            pos["qty"] = abs(new_signed)
            pos["side"] = "long" if new_signed > 0 else "short"
            pos["market_value"] = abs(new_signed) * price

    def cancel_order_by_id(self, order_id: str) -> dict[str, Any]:
        self._maybe_fail("cancel_order_by_id")
        order = self._orders.get(str(order_id))
        if order is None:
            raise TerminalBrokerError(f"unknown order {order_id}")
        order["status"] = "canceled"
        return dict(order)

    def replace_order_by_id(self, order_id: str, **kwargs: Any) -> dict[str, Any]:
        self._maybe_fail("replace_order_by_id")
        order = self._orders.get(str(order_id))
        if order is None:
            raise TerminalBrokerError(f"unknown order {order_id}")
        if order["status"] in {"filled", "canceled", "rejected"}:
            raise TerminalBrokerError(f"cannot replace terminal order {order_id}")
        if "qty" in kwargs:
            order["qty"] = float(kwargs["qty"])
        if "limit_price" in kwargs:
            order["limit_price"] = float(kwargs["limit_price"])
        if "stop_price" in kwargs:
            order["stop_price"] = float(kwargs["stop_price"])
        order["status"] = "replaced"
        return dict(order)

    def get_all_positions(self) -> list[dict[str, Any]]:
        self._maybe_fail("get_all_positions")
        return [dict(v) for v in self._positions.values()]

    def get_orders(
        self,
        status: str = "open",
        *,
        limit: Optional[int] = None,
        page_token: Optional[str] = None,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        self._maybe_fail("get_orders")
        open_statuses = {"new", "accepted", "pending_new", "partially_filled", "pending_cancel", "pending_replace"}
        closed_statuses = {"filled", "canceled", "cancelled", "rejected", "expired", "replaced"}
        out = []
        for order in self._orders.values():
            st = str(order.get("status", "")).lower()
            if status == "all":
                out.append(dict(order))
            elif status == "open" and st in open_statuses:
                out.append(dict(order))
            elif status == "closed" and st in closed_statuses:
                out.append(dict(order))
        if self.page_size is None:
            return out
        page_limit = int(limit or self.page_size or len(out) or 1)
        page_limit = max(1, min(page_limit, int(self.page_size)))
        start = int(page_token or 0)
        stop = start + page_limit
        next_token = str(stop) if stop < len(out) else None
        return {"items": out[start:stop], "next_page_token": next_token, "endpoint": self.base_url}

    def get_account(self) -> dict[str, Any]:
        self._maybe_fail("get_account")
        pos_value = sum(float(p.get("market_value", 0.0)) for p in self._positions.values())
        equity = self.cash + pos_value
        return {
            "id": self.account_id,
            "account_number": self.account_id,
            "equity": equity,
            "cash": self.cash,
            "buying_power": self.cash,
            "currency": "USD",
            "status": "ACTIVE",
            "endpoint": self.base_url,
            "paper": self.paper,
        }

    def cancel_orders(self) -> list[dict[str, Any]]:
        self._maybe_fail("cancel_orders")
        cancelled = []
        for order in self._orders.values():
            if str(order.get("status", "")).lower() in {
                "new", "accepted", "pending_new", "partially_filled",
            }:
                order["status"] = "canceled"
                cancelled.append(dict(order))
        return cancelled

    def close_all_positions(self, cancel_orders: bool = True) -> list[dict[str, Any]]:
        self._maybe_fail("close_all_positions")
        if cancel_orders:
            self.cancel_orders()
        closed = []
        for sym, pos in list(self._positions.items()):
            side = "sell" if pos["side"] == "long" else "buy"
            order = self.submit_order(
                symbol=sym, qty=pos["qty"], side=side, type="market",
                client_order_id=uuid4().hex,
            )
            closed.append(order)
        return closed
