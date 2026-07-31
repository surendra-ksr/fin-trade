"""Phase 15 Alpaca paper sandbox: config, URL gate, reconcile compare, smoke.

All tests are zero-network.  The sandbox script's real paper transcript is an
operator-run artifact and is recorded as PENDING-USER-RUN in the evidence pack
when credentials are unavailable in this environment.
"""
from __future__ import annotations

import inspect
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

import scripts.alpaca_sandbox_smoke as smoke
from automation.reconcile import reconcile_alpaca_paper
from risk.circuit_breakers import CircuitBreakerManager
from trading.alpaca_adapter import (
    AlpacaBrokerAdapter,
    MockAlpacaClient,
    MockAlpacaError,
    is_paper_base_url,
)
from trading.broker_base import LiveGateDenied, LiveGateEvidence, OrderStatus, RetryableBrokerError
from utils.config import ConfigValidationError, load_config
from utils.constants import LIVE_TRADING_AUTH_PHRASE

ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc


def at(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def passing_gate() -> LiveGateEvidence:
    return LiveGateEvidence(
        paper_days=120,
        sharpe=1.25,
        max_drawdown=0.10,
        win_rate=0.55,
        breakers_tested=True,
        human_authorized=True,
        auth_phrase=LIVE_TRADING_AUTH_PHRASE,
    )


def write_mutated_config(tmp_path: Path, mutate: Any) -> Path:
    data = yaml.safe_load((ROOT / "config.yaml").read_text())
    mutate(data)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


# ---------------------------------------------------------------------------
# Config: broker.alpaca section and mode validation
# ---------------------------------------------------------------------------


def test_alpaca_nested_config_defaults_load() -> None:
    cfg = load_config(ROOT / "config.yaml")
    assert cfg.broker.alpaca.base_url == "https://paper-api.alpaca.markets"
    assert cfg.broker.alpaca.mode == "paper"
    assert cfg.broker.alpaca.request_timeout_seconds == 15
    assert cfg.broker.alpaca.max_retries == 3
    assert cfg.broker.alpaca.retry_delay_seconds == 10


def test_alpaca_config_unknown_mode_rejected(tmp_path: Path) -> None:
    def mutate(data: dict[str, Any]) -> None:
        data["broker"]["alpaca"]["mode"] = "sandbox-but-not-valid"

    with pytest.raises(ConfigValidationError) as err:
        load_config(write_mutated_config(tmp_path, mutate))
    assert any("broker.alpaca.mode" in problem for problem in err.value.problems)


def test_alpaca_config_retry_timeout_fields_validated(tmp_path: Path) -> None:
    def mutate(data: dict[str, Any]) -> None:
        data["broker"]["alpaca"]["request_timeout_seconds"] = 0
        data["broker"]["alpaca"]["max_retries"] = 0

    with pytest.raises(ConfigValidationError) as err:
        load_config(write_mutated_config(tmp_path, mutate))
    joined = " ".join(err.value.problems)
    assert "broker.alpaca.request_timeout_seconds" in joined
    assert "broker.alpaca.max_retries" in joined


# ---------------------------------------------------------------------------
# Fail-closed URL gate
# ---------------------------------------------------------------------------


def test_paper_url_detection_is_exact() -> None:
    assert is_paper_base_url("https://paper-api.alpaca.markets") is True
    assert is_paper_base_url("paper-api.alpaca.markets") is True
    assert is_paper_base_url("https://api.alpaca.markets") is False
    assert is_paper_base_url("https://paper-api.alpaca.markets.evil.example") is False


def test_alpaca_adapter_allows_explicit_paper_base_url_without_live_gate(app_config) -> None:
    app_config.broker.name = "alpaca"
    app_config.broker.alpaca.mode = "paper"
    client = MockAlpacaClient(base_url="https://paper-api.alpaca.markets")
    adapter = AlpacaBrokerAdapter(
        config=app_config,
        client=client,
        base_url="https://paper-api.alpaca.markets",
    )
    assert adapter.base_url == "https://paper-api.alpaca.markets"
    assert adapter.account().status == "ACTIVE"


def test_alpaca_adapter_rejects_non_paper_url_without_all_pass_gate(app_config) -> None:
    app_config.broker.name = "alpaca"
    app_config.broker.alpaca.mode = "paper"
    with pytest.raises(LiveGateDenied):
        AlpacaBrokerAdapter(
            config=app_config,
            client=MockAlpacaClient(base_url="https://api.alpaca.markets", mode="live"),
            base_url="https://api.alpaca.markets",
        )


def test_alpaca_adapter_allows_non_paper_url_with_all_pass_gate(app_config) -> None:
    app_config.broker.name = "alpaca"
    app_config.broker.alpaca.mode = "paper"
    adapter = AlpacaBrokerAdapter(
        config=app_config,
        client=MockAlpacaClient(base_url="https://api.alpaca.markets", mode="live"),
        base_url="https://api.alpaca.markets",
        live_gate_evidence=passing_gate(),
    )
    assert adapter.base_url == "https://api.alpaca.markets"


def test_alpaca_adapter_live_mode_requires_gate_even_on_paper_url(app_config) -> None:
    app_config.broker.name = "alpaca"
    app_config.broker.alpaca.mode = "live"
    with pytest.raises(LiveGateDenied):
        AlpacaBrokerAdapter(
            config=app_config,
            client=MockAlpacaClient(base_url="https://paper-api.alpaca.markets"),
            base_url="https://paper-api.alpaca.markets",
        )


def test_fail_closed_url_gate_body_pasted() -> None:
    src = inspect.getsource(AlpacaBrokerAdapter.__init__) + inspect.getsource(AlpacaBrokerAdapter._requires_live_gate)
    assert "evaluate_live_gate" in src
    assert "gate.raise_if_denied" in src
    assert "not is_paper_base_url" in src
    assert "mode" in src and "live" in src


# ---------------------------------------------------------------------------
# Mock Alpaca client paper semantics: pagination + error payloads
# ---------------------------------------------------------------------------


def test_mock_alpaca_paginates_order_payloads(app_config) -> None:
    client = MockAlpacaClient(page_size=1)
    adapter = AlpacaBrokerAdapter(config=app_config, client=client)
    adapter.submit(smoke.OrderRequest("AAPL", "buy", 1, 150.0, client_id="p1"))
    adapter.submit(smoke.OrderRequest("MSFT", "buy", 1, 150.0, client_id="p2"))
    orders = adapter.orders(status="all")
    assert {order.symbol for order in orders} >= {"AAPL", "MSFT"}
    get_order_calls = [call for call in client.calls if call[0] == "get_orders"]
    assert len(get_order_calls) >= 2


def test_mock_alpaca_error_payload_maps_to_retryable(app_config) -> None:
    client = MockAlpacaClient()
    client.queue_error("get_account", {"code": 429, "message": "rate limit"})
    app_config.broker.alpaca.max_retries = 1
    adapter = AlpacaBrokerAdapter(config=app_config, client=client)
    with pytest.raises(RetryableBrokerError, match="429"):
        adapter.account()


def test_mock_alpaca_error_payload_object_is_visible() -> None:
    err = MockAlpacaError({"code": 422, "message": "bad payload"})
    assert err.payload["code"] == 422
    assert "bad payload" in str(err)


# ---------------------------------------------------------------------------
# Optional Alpaca-paper reconciliation compare
# ---------------------------------------------------------------------------


def test_reconcile_alpaca_paper_skips_without_adapter(db, app_config) -> None:
    app_config.broker.name = "paper_only"
    result = reconcile_alpaca_paper(
        db,
        config=app_config,
        adapter=None,
        now_fn=lambda: at(2024, 4, 1, 22, 0),
    )
    assert result.ok is True
    assert "skipped" in result.summary
    log = db.fetch_automation_log(routine="reconcile")
    assert log.iloc[0]["action"] == "alpaca_paper_compare"
    assert log.iloc[0]["result"] == "skipped"


def test_reconcile_alpaca_paper_match_when_enabled(db, app_config) -> None:
    app_config.broker.name = "alpaca"
    app_config.broker.alpaca.mode = "paper"
    db.insert_paper_trade("AAPL", "buy", 1, at(2024, 4, 1, 14, 0), 150.0)
    client = MockAlpacaClient()
    adapter = AlpacaBrokerAdapter(config=app_config, client=client)
    adapter.submit(smoke.OrderRequest("AAPL", "buy", 1, 150.0, client_id="r1"))
    result = reconcile_alpaca_paper(
        db,
        adapter=adapter,
        config=app_config,
        now_fn=lambda: at(2024, 4, 1, 22, 0),
    )
    assert result.ok is True
    assert result.matched == ["AAPL"]
    log = db.fetch_automation_log(routine="reconcile")
    assert log.iloc[0]["action"] == "alpaca_paper_compare"
    assert log.iloc[0]["result"] == "ok"


def test_reconcile_alpaca_paper_mismatch_logs_and_sticky_halts(db, app_config) -> None:
    app_config.broker.name = "alpaca"
    app_config.broker.alpaca.mode = "paper"
    breaker = CircuitBreakerManager(app_config, db=db, now_fn=lambda: at(2024, 4, 1, 22, 0))
    db.insert_paper_trade("AAPL", "buy", 2, at(2024, 4, 1, 14, 0), 150.0)
    client = MockAlpacaClient()
    adapter = AlpacaBrokerAdapter(config=app_config, client=client)
    adapter.submit(smoke.OrderRequest("AAPL", "buy", 1, 150.0, client_id="r2"))
    result = reconcile_alpaca_paper(
        db,
        adapter=adapter,
        config=app_config,
        breaker=breaker,
        now_fn=lambda: at(2024, 4, 1, 22, 0),
    )
    assert result.halted is True
    assert result.quantity_mismatches[0]["symbol"] == "AAPL"
    log = db.fetch_automation_log(routine="reconcile")
    assert log.iloc[0]["action"] == "alpaca_paper_compare"
    assert log.iloc[0]["result"] == "halt"
    policy = breaker.evaluate(
        __import__("risk.circuit_breakers", fromlist=["PortfolioSnapshot"]).PortfolioSnapshot(
            timestamp=at(2024, 4, 1, 22, 0), equity=100_000.0, cash=100_000.0
        )
    )
    assert policy.trading_halted is True
    assert not policy.allow_new_entries


def test_reconcile_compare_function_body_pasted() -> None:
    src = inspect.getsource(reconcile_alpaca_paper)
    assert "adapter.positions" in src
    assert "alpaca_paper_compare" in src
    assert "reconcile_positions" in src
    assert "POSITION_MISMATCH" in src


# ---------------------------------------------------------------------------
# Sandbox smoke script: dry-run, credentials, redaction, gateway flow
# ---------------------------------------------------------------------------


def test_smoke_dry_run_completes_and_prints_redacted_transcript(app_config, capsys) -> None:
    code = smoke.run_smoke(
        cfg=app_config,
        dry_run=True,
        symbol="AAPL",
        quantity=1,
        reference_price=150.0,
        limit_price=1.0,
        fill_timeout_seconds=1.0,
        poll_seconds=0.01,
    )
    captured = capsys.readouterr().out
    assert code == smoke.EXIT_OK
    assert "REDACTED ALPACA PAPER SANDBOX TRANSCRIPT" in captured
    assert "market_buy filled" in captured
    assert "kill_switch" in captured
    assert "resume token_confirmed=True" in captured
    assert "PAPER-ACCOUNT-000000" not in captured
    assert not re.search(r"PK[A-Z0-9]{18}|SK[A-Z0-9]{18}", captured)


def test_smoke_main_missing_credentials_returns_clear_exit(monkeypatch, capsys) -> None:
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    code = smoke.main(["--config", str(ROOT / "config.yaml")])
    captured = capsys.readouterr().out
    assert code == smoke.EXIT_MISSING_CREDENTIALS
    assert "missing APCA_API_KEY_ID/APCA_API_SECRET_KEY" in captured
    assert "REDACTED" in captured


def test_smoke_script_orders_use_gateway_place_order_not_low_level_submit() -> None:
    src = (ROOT / "scripts" / "alpaca_sandbox_smoke.py").read_text()
    assert "adapter.place_order" in src
    assert ".submit(" not in src
    assert "RiskGateway ->\nadapter" in src or "RiskGateway -> adapter" in src


def test_smoke_mask_identifier_keeps_edges_only() -> None:
    assert smoke.mask_identifier("ACCOUNT123456789", keep=3) == "ACC***789"
    assert smoke.mask_identifier("short", keep=3) == "*****"
