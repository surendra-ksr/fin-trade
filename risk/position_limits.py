"""Config-driven exposure limits and the mandatory pre-transmission gateway."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from data.database import DatabaseManager
from utils.config import AppConfig, load_config

@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: float
    price: float
    sector: str = "UNKNOWN"
    strategy: str = "default"

    @property
    def value(self) -> float:
        return self.quantity * self.price

@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: str
    quantity: float
    price: float
    strategy: str = "default"
    sector: str = "UNKNOWN"
    order_type: str = "market"
    confidence: float = 1.0
    client_id: str = ""

@dataclass
class PortfolioSnapshot:
    equity: float
    cash: float
    positions: list[Position] = field(default_factory=list)
    daily_pnl_pct: float = 0.0
    weekly_pnl_pct: float = 0.0
    monthly_pnl_pct: float = 0.0
    drawdown_pct: float = 0.0
    strategy_daily_pnl_pct: Mapping[str, float] = field(default_factory=dict)
    asset_daily_pnl_pct: Mapping[str, float] = field(default_factory=dict)
    breaker_state: str = "NORMAL"

@dataclass(frozen=True)
class LimitDecision:
    allowed: bool
    reasons: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()

class PositionLimits:
    """Pure, deterministic checks; every threshold comes from AppConfig."""
    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or load_config()

    def _projected(self, order: OrderRequest, portfolio: PortfolioSnapshot) -> list[Position]:
        sign = 1.0 if order.side.lower() in {"buy", "cover"} else -1.0
        result = list(portfolio.positions)
        for i, p in enumerate(result):
            if p.symbol == order.symbol:
                result[i] = Position(p.symbol, p.quantity + sign * order.quantity, order.price, p.sector, p.strategy)
                break
        else:
            result.append(Position(order.symbol, sign * order.quantity, order.price, order.sector, order.strategy))
        return result

    def check_asset(self, order: OrderRequest, p: PortfolioSnapshot) -> Optional[str]:
        cfg = self.config.order_limits.per_stock
        projected = next((x for x in self._projected(order, p) if x.symbol == order.symbol), None)
        if projected is None: return None
        if abs(projected.quantity * order.price) > cfg.max_position_value: return f"asset_value>{cfg.max_position_value}"
        if abs(projected.quantity) > cfg.max_shares: return f"asset_shares>{cfg.max_shares}"
        if abs(projected.quantity * order.price) / p.equity > cfg.max_position_size_pct: return f"asset_pct>{cfg.max_position_size_pct}"
        return None

    def check_strategy(self, order: OrderRequest, p: PortfolioSnapshot) -> Optional[str]:
        threshold = self.config.risk.per_strategy_max_gross_pct
        value = sum(abs(x.value) for x in self._projected(order, p) if x.strategy == order.strategy)
        if value / p.equity > threshold: return f"strategy_gross>{threshold}"
        return None

    def check_sector(self, order: OrderRequest, p: PortfolioSnapshot) -> Optional[str]:
        cap = self.config.order_limits.per_portfolio.max_sector_concentration
        gross = sum(abs(x.value) for x in self._projected(order, p) if x.sector == order.sector) + abs(order.quantity * order.price)
        if gross / p.equity > cap: return f"sector_gross>{cap}"
        return None

    def check_portfolio(self, order: OrderRequest, p: PortfolioSnapshot) -> Optional[str]:
        cfg = self.config.order_limits.per_portfolio
        projected = self._projected(order, p)
        gross = sum(abs(x.value) for x in projected)
        net = sum(x.value for x in projected)
        if len([x for x in projected if x.quantity]) > cfg.max_open_positions: return f"open_positions>{cfg.max_open_positions}"
        if gross / p.equity > cfg.max_leverage: return f"gross_leverage>{cfg.max_leverage}"
        if net < -p.equity * cfg.max_short_exposure_pct: return f"net_short>{cfg.max_short_exposure_pct}"
        if p.cash - order.quantity * order.price < p.equity * cfg.min_cash_reserve and order.side.lower() in {"buy", "cover"}: return f"cash_reserve<{cfg.min_cash_reserve}"
        return None

class RiskGateway:
    """The sole order admission and broker transmission path."""
    def __init__(self, config: Optional[AppConfig] = None, db: Optional[DatabaseManager] = None):
        self.config = config or load_config()
        self.db = db
        self.limits = PositionLimits(self.config)

    def _breach(self, kind: str, order: OrderRequest, value: Any, threshold: Any, reason: str) -> None:
        if self.db:
            self.db.log_limit_breach(kind, "DENIED", entity=order.symbol, value=float(value) if isinstance(value, (int,float)) else None, threshold=float(threshold) if isinstance(threshold, (int,float)) else None, details={"reason": reason, "strategy": order.strategy})

    def evaluate_order(self, order: OrderRequest, portfolio: PortfolioSnapshot) -> LimitDecision:
        """Evaluate every configured order and record every denial."""
        reasons: list[str] = []
        checks = ["order_value", "asset", "strategy", "sector", "portfolio", "speed_breakers"]
        oc = self.config.order_limits.per_order
        value = abs(order.quantity * order.price)
        if order.quantity <= 0: reasons.append("quantity<=0")
        if value < oc.min_order_value: reasons.append(f"order_value<{oc.min_order_value}")
        if value > oc.max_order_value: reasons.append(f"order_value>{oc.max_order_value}")
        for label, reason in (("asset", self.limits.check_asset(order, portfolio)), ("strategy", self.limits.check_strategy(order, portfolio)), ("sector", self.limits.check_sector(order, portfolio)), ("portfolio", self.limits.check_portfolio(order, portfolio))):
            if reason: reasons.append(f"{label}:{reason}")
        state = portfolio.breaker_state.upper()
        if state in {"RESTRICTED", "HALTED", "DEFENSIVE", "EMERGENCY", "SUSPENDED"} and order.side.lower() in {"buy", "sell_short", "short"}:
            reasons.append(f"breaker_state:{state}:new_entry_blocked")
        for label, observed, threshold in (("daily", portfolio.daily_pnl_pct, -self.config.risk.daily_loss_limit_pct), ("weekly", portfolio.weekly_pnl_pct, -self.config.risk.weekly_loss_limit_pct), ("monthly", portfolio.monthly_pnl_pct, -self.config.risk.monthly_loss_limit_pct), ("drawdown", portfolio.drawdown_pct, -self.config.risk.max_drawdown_pct)):
            if observed <= threshold: reasons.append(f"{label}_loss:{observed}<={threshold}")
        strategy_loss = portfolio.strategy_daily_pnl_pct.get(order.strategy, 0.0)
        asset_loss = portfolio.asset_daily_pnl_pct.get(order.symbol, 0.0)
        if strategy_loss <= -self.config.risk.per_strategy_daily_loss_limit_pct: reasons.append(f"strategy_daily_loss:{strategy_loss}")
        if asset_loss <= -self.config.risk.per_asset_daily_loss_limit_pct: reasons.append(f"asset_daily_loss:{asset_loss}")
        # Weekly/monthly buckets use the same explicit config policy; callers
        # may provide period keys in the maps without introducing code limits.
        for period, limit in (("weekly", self.config.risk.per_strategy_weekly_loss_limit_pct), ("monthly", self.config.risk.per_strategy_monthly_loss_limit_pct)):
            observed = portfolio.strategy_daily_pnl_pct.get(f"{order.strategy}:{period}", 0.0)
            if observed <= -limit: reasons.append(f"strategy_{period}_loss:{observed}")
        for period, limit in (("weekly", self.config.risk.per_asset_weekly_loss_limit_pct), ("monthly", self.config.risk.per_asset_monthly_loss_limit_pct)):
            observed = portfolio.asset_daily_pnl_pct.get(f"{order.symbol}:{period}", 0.0)
            if observed <= -limit: reasons.append(f"asset_{period}_loss:{observed}")
        for reason in reasons: self._breach("gateway:" + reason.split(":", 1)[0], order, value, 0, reason)
        return LimitDecision(not reasons, tuple(reasons), tuple(checks))

    def transmit(self, broker: Any, order: OrderRequest, portfolio: PortfolioSnapshot) -> Any:
        """Only this method may invoke a broker's low-level ``submit``."""
        decision = self.evaluate_order(order, portfolio)
        if not decision.allowed: raise PermissionError("order denied: " + "; ".join(decision.reasons))
        return broker.submit(order)
