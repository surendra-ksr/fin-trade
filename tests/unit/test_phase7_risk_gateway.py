"""Phase 7 behavioral gate: every denial is explicit and auditable."""
import pytest
from risk.position_limits import OrderRequest, PortfolioSnapshot, Position, RiskGateway
from trading.core import Order, PaperBroker


def snapshot(**kwargs):
    return PortfolioSnapshot(equity=100000, cash=100000, **kwargs)


def test_asset_strategy_sector_and_portfolio_denials():
    gateway = RiskGateway()
    cases = [
        (OrderRequest("AAPL", "buy", 2000, 100), "asset:"),
        (OrderRequest("AAPL", "buy", 600, 100, strategy="s"), "strategy:"),
        (OrderRequest("AAPL", "buy", 300, 100, sector="TECH"), "sector:"),
        (OrderRequest("AAPL", "buy", 100, 100), "portfolio:"),
    ]
    portfolios = [
        snapshot(),
        snapshot(positions=[Position("MSFT", 1, 100, strategy="s")]),
        snapshot(positions=[Position("MSFT", 240, 100, "TECH")]),
        snapshot(positions=[Position(str(i), 1, 100) for i in range(10)]),
    ]
    for (order, prefix), portfolio in zip(cases, portfolios):
        decision = gateway.evaluate_order(order, portfolio)
        assert not decision.allowed
        assert any(reason.startswith(prefix) for reason in decision.reasons)


def test_all_speed_breakers_and_restricted_halted_entries_denied():
    gateway = RiskGateway()
    for field in ("daily_pnl_pct", "weekly_pnl_pct", "monthly_pnl_pct", "drawdown_pct"):
        p = snapshot(**{field: -0.20})
        assert not gateway.evaluate_order(OrderRequest("AAPL", "buy", 1, 100), p).allowed
    for state in ("RESTRICTED", "HALTED"):
        assert not gateway.evaluate_order(OrderRequest("AAPL", "buy", 1, 100), snapshot(breaker_state=state)).allowed


def test_gateway_is_only_paper_transmission_path():
    broker = PaperBroker()
    with pytest.raises(PermissionError, match="denied"):
        broker.place_order(Order("AAPL", "buy", 1, price=100), snapshot(breaker_state="HALTED"))
    accepted = broker.place_order(Order("AAPL", "buy", 1, price=100), snapshot())
    assert accepted.symbol == "AAPL"


def test_per_strategy_and_asset_loss_buckets_are_denied():
    gateway = RiskGateway()
    p = snapshot(strategy_daily_pnl_pct={"mean_reversion": -0.03, "mean_reversion:weekly": -0.06}, asset_daily_pnl_pct={"AAPL": -0.03, "AAPL:monthly": -0.09})
    d = gateway.evaluate_order(OrderRequest("AAPL", "buy", 1, 100, strategy="mean_reversion"), p)
    assert not d.allowed
    assert any("strategy_" in r or "asset_" in r for r in d.reasons)
