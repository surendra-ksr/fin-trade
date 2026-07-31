#!/usr/bin/env python3
"""Phase 15 Alpaca paper-sandbox smoke.

This script is intentionally operator-run.  It never accepts credentials on
CLI arguments and never prints key material.  Credentials are read only from
``APCA_API_KEY_ID`` / ``APCA_API_SECRET_KEY`` after loading a local ``.env``
file.  ``--dry-run`` uses the in-memory MockAlpacaClient and performs zero
network calls / real orders while still exercising the ordered RiskGateway ->
adapter flow.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional
from uuid import uuid4

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from risk.circuit_breakers import CircuitBreakerManager  # noqa: E402
from risk.position_limits import OrderRequest, PortfolioSnapshot  # noqa: E402
from trading.alpaca_adapter import AlpacaBrokerAdapter, MockAlpacaClient  # noqa: E402
from trading.broker_base import BrokerError, LiveGateEvidence, OrderResult, OrderStatus  # noqa: E402
from utils.config import AppConfig, ConfigError, load_config  # noqa: E402
from utils.logger import configure_logging  # noqa: E402

EXIT_OK = 0
EXIT_MISSING_CREDENTIALS = 2
EXIT_CONFIG_ERROR = 3
EXIT_LIVE_GATE_DENIED = 4
EXIT_BROKER_ERROR = 5
EXIT_FILL_TIMEOUT = 6
EXIT_SAFETY_ABORT = 7


@dataclass
class Transcript:
    """Redacted smoke transcript."""

    lines: list[str] = field(default_factory=list)

    def add(self, message: str) -> None:
        self.lines.append(message)

    def render(self) -> str:
        body = "\n".join(self.lines)
        return "REDACTED ALPACA PAPER SANDBOX TRANSCRIPT\n" + body


def mask_identifier(value: object, *, keep: int = 4) -> str:
    """Mask account/order identifiers for console output."""
    text = str(value or "")
    if not text:
        return "<empty>"
    if len(text) <= keep * 2:
        return "*" * len(text)
    return f"{text[:keep]}***{text[-keep:]}"


def load_dotenv_for_config(config_path: Path) -> None:
    """Load local dotenv files; real environment values still win."""
    load_dotenv(config_path.parent / ".env", override=False)
    if config_path.parent.resolve() != Path.cwd().resolve():
        load_dotenv(Path.cwd() / ".env", override=False)


def credentials_present() -> bool:
    """True only when both APCA paper-sandbox credential env vars are set."""
    return bool(os.environ.get("APCA_API_KEY_ID") and os.environ.get("APCA_API_SECRET_KEY"))


def portfolio_from_adapter(adapter: AlpacaBrokerAdapter) -> PortfolioSnapshot:
    """Build a RiskGateway portfolio snapshot from adapter account/positions."""
    acct = adapter.account()
    return PortfolioSnapshot(equity=acct.equity, cash=acct.cash, positions=[])


def wait_for_fill(
    adapter: AlpacaBrokerAdapter,
    initial: OrderResult,
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> OrderResult:
    """Poll adapter orders until ``initial`` is filled or the timeout expires."""
    if initial.status is OrderStatus.FILLED:
        return initial
    deadline = time.monotonic() + float(timeout_seconds)
    while time.monotonic() < deadline:
        for order in adapter.orders(status="all"):
            if order.order_id == initial.order_id or order.client_id == initial.client_id:
                if order.status is OrderStatus.FILLED:
                    return order
        time.sleep(max(0.05, float(poll_seconds)))
    raise TimeoutError(f"order {initial.order_id} did not fill within {timeout_seconds}s")


def _build_adapter(
    cfg: AppConfig,
    *,
    base_url: str,
    dry_run: bool,
    live_gate_evidence: Optional[LiveGateEvidence] = None,
) -> AlpacaBrokerAdapter:
    """Construct the adapter; dry-run injects the zero-network mock client."""
    if dry_run:
        client = MockAlpacaClient(base_url=base_url, mode="paper")
    else:
        client = None
    return AlpacaBrokerAdapter(
        config=cfg,
        client=client,
        base_url=base_url,
        live_gate_evidence=live_gate_evidence,
    )


def run_smoke(
    *,
    cfg: AppConfig,
    dry_run: bool,
    symbol: str,
    quantity: float,
    reference_price: float,
    limit_price: float,
    fill_timeout_seconds: float,
    poll_seconds: float,
    base_url: Optional[str] = None,
    transcript: Optional[Transcript] = None,
) -> int:
    """Execute the ordered sandbox smoke and print a redacted transcript."""
    out = transcript or Transcript()
    url = str(base_url or cfg.broker.alpaca.base_url)

    if not dry_run and not credentials_present():
        out.add("ABORT missing APCA_API_KEY_ID/APCA_API_SECRET_KEY in environment or .env")
        print(out.render())
        return EXIT_MISSING_CREDENTIALS

    try:
        adapter = _build_adapter(cfg, base_url=url, dry_run=dry_run)
    except Exception as exc:
        out.add(f"ABORT adapter construction failed: {type(exc).__name__}: {exc}")
        print(out.render())
        return EXIT_LIVE_GATE_DENIED

    mode = "DRY-RUN mock" if dry_run else "LIVE PAPER endpoint"
    out.add(f"mode={mode} base_url={url}")

    # 1. Fetch account.
    account = adapter.account()
    account_id = account.raw.get("account_id", "") if account.raw else ""
    out.add(
        "1 account "
        f"id={mask_identifier(account_id)} status={account.status} "
        f"equity={account.equity:.2f} cash={account.cash:.2f}"
    )

    # 2. List positions.
    positions = adapter.positions()
    out.add(f"2 positions count={len(positions)} symbols={[p.symbol for p in positions]}")

    # 3. Submit tiny market buy through RiskGateway -> adapter and await fill.
    portfolio = portfolio_from_adapter(adapter)
    buy_request = OrderRequest(
        symbol.upper(),
        "buy",
        float(quantity),
        float(reference_price),
        order_type="market",
        client_id="sandbox-buy-" + uuid4().hex,
    )
    market_result = adapter.place_order(buy_request, portfolio)
    out.add(
        "3 market_buy submitted "
        f"order={mask_identifier(market_result.order_id)} status={market_result.status.value}"
    )
    try:
        filled = wait_for_fill(
            adapter,
            market_result,
            timeout_seconds=fill_timeout_seconds,
            poll_seconds=poll_seconds,
        )
    except TimeoutError as exc:
        out.add(f"ABORT fill timeout: {exc}")
        print(out.render())
        return EXIT_FILL_TIMEOUT
    out.add(
        "4 market_buy filled "
        f"order={mask_identifier(filled.order_id)} qty={filled.filled_quantity:.6g}"
    )

    # 4. Place and cancel a non-marketable limit order through the same gateway path.
    portfolio = portfolio_from_adapter(adapter)
    limit_request = OrderRequest(
        symbol.upper(),
        "buy",
        float(quantity),
        float(reference_price),
        order_type="limit",
        limit_price=float(limit_price),
        client_id="sandbox-limit-" + uuid4().hex,
    )
    limit_result = adapter.place_order(limit_request, portfolio)
    out.add(
        "5 limit_order submitted "
        f"order={mask_identifier(limit_result.order_id)} status={limit_result.status.value}"
    )
    cancelled = adapter.cancel(limit_result.order_id)
    out.add(
        "6 limit_order cancel "
        f"order={mask_identifier(cancelled.order_id)} status={cancelled.status.value}"
    )

    # 5. Kill switch through adapter: cancel-all + flatten.
    payload = adapter.engage_kill_switch("alpaca paper sandbox smoke")
    out.add(
        "7 kill_switch "
        f"cancelled={payload['cancelled_count']} flattened={payload['flattened_count']}"
    )

    # 6. Token-confirmed resume against the breaker state machine.
    breaker = CircuitBreakerManager(cfg)
    breaker.activate_kill_switch("alpaca sandbox smoke local breaker", flatten=True)
    token = breaker.request_override("resume", reason="alpaca sandbox smoke complete")
    confirmed = breaker.confirm_override(token)
    if not confirmed:
        out.add("ABORT resume token confirmation failed")
        print(out.render())
        return EXIT_SAFETY_ABORT
    adapter.resume(token=token, breaker=breaker, reason="alpaca sandbox smoke complete")
    out.add(f"8 resume token_confirmed={confirmed} breaker_state={breaker.state.value}")

    print(out.render())
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alpaca paper sandbox smoke (redacted output)")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"), help="config.yaml path")
    parser.add_argument("--dry-run", action="store_true", help="use the zero-network mock client")
    parser.add_argument("--symbol", default="AAPL", help="liquid symbol for the 1-share smoke")
    parser.add_argument("--quantity", type=float, default=1.0, help="share quantity (default 1)")
    parser.add_argument("--reference-price", type=float, default=150.0, help="gateway price reference")
    parser.add_argument("--limit-price", type=float, default=1.0, help="non-marketable limit price")
    parser.add_argument("--fill-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--base-url", default=None, help="explicit Alpaca base URL (defaults to config)")
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    config_path = Path(args.config).expanduser().resolve()
    load_dotenv_for_config(config_path)
    configure_logging({"log_to_file": False, "level": "CRITICAL"})
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}")
        return EXIT_CONFIG_ERROR
    try:
        return run_smoke(
            cfg=cfg,
            dry_run=bool(args.dry_run),
            symbol=args.symbol,
            quantity=float(args.quantity),
            reference_price=float(args.reference_price),
            limit_price=float(args.limit_price),
            fill_timeout_seconds=float(args.fill_timeout_seconds),
            poll_seconds=float(args.poll_seconds),
            base_url=args.base_url,
        )
    except BrokerError as exc:
        print(f"BROKER ERROR: {type(exc).__name__}: {exc}")
        return EXIT_BROKER_ERROR
    except PermissionError as exc:
        print(f"SAFETY ABORT: {exc}")
        return EXIT_SAFETY_ABORT


if __name__ == "__main__":  # pragma: no cover - exercised via unit main() tests
    raise SystemExit(main())
