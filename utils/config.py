"""Configuration system: load, substitute, type, and validate ``config.yaml``.

Features
--------
* Loads ``.env`` (if present) before substitution so `${VAR}` placeholders
  inside ``config.yaml`` resolve from either the process environment or the
  dotenv file. Supports ``${VAR:-default}`` inline defaults.
* Missing variables degrade to empty strings with recorded **warnings**
  instead of hard failures: a paper-trading research setup must boot with
  zero API keys configured.
* Strongly typed dataclass tree mirroring ``config.yaml``; unknown keys are
  ignored with a warning (forward compatibility) while *missing* keys fall
  back to the conservative defaults defined here.
* :func:`AppConfig.validate` performs whole-config cross-field validation and
  raises :class:`ConfigValidationError` with **every** problem listed.
* :func:`get_config` provides a cached process-wide singleton.

The module purposely depends only on the standard library + PyYAML +
python-dotenv so it can bootstrap everything else.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv

from .constants import (
    DEFAULT_CONFIG_PATH,
    LIVE_TRADING_AUTH_PHRASE,
    AutomationMode,
    PaperTradingMode,
    PositionSizingMethod,
    TradingMode,
)
from .logger import get_logger

__all__ = [
    "AppConfig",
    "ConfigError",
    "ConfigValidationError",
    "get_config",
    "load_config",
]

_log = get_logger("app")

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")
_TIME_PATTERN = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class ConfigError(Exception):
    """Raised when the configuration file cannot be loaded or parsed."""


class ConfigValidationError(ConfigError):
    """Raised when the configuration is internally inconsistent."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        joined = "\n  - ".join(self.problems)
        super().__init__(f"Invalid configuration ({len(self.problems)} problem(s)):\n  - {joined}")


# =============================================================================
# Section dataclasses (defaults are the conservative ones from config.yaml)
# =============================================================================


@dataclass
class TradingConfig:
    mode: str = "paper"
    automation_mode: str = "semi_automated"
    initial_capital: float = 100_000.0
    base_currency: str = "USD"
    trading_hours: str = "market_only"
    min_confidence_normal: float = 0.65
    min_confidence_restricted: float = 0.75
    min_confidence_defensive: float = 0.90
    require_live_authorization_env: bool = True

    @property
    def trading_mode(self) -> TradingMode:
        return TradingMode(self.mode)

    @property
    def automation(self) -> AutomationMode:
        return AutomationMode(self.automation_mode)


@dataclass
class WatchlistConfig:
    use_sp500: bool = False
    use_nasdaq100: bool = False
    use_russell2000: bool = False
    custom_stocks: list[str] = field(default_factory=lambda: ["SPY", "QQQ"])


@dataclass
class TimeframesConfig:
    primary: str = "1d"
    secondary: list[str] = field(default_factory=lambda: ["1h", "4h"])
    long_term: str = "1w"
    lookback_days: dict[str, Optional[int]] = field(default_factory=dict)

    @property
    def all_timeframes(self) -> list[str]:
        ordered = [self.primary, *self.secondary, self.long_term]
        seen: list[str] = []
        for tf in ordered:
            if tf not in seen:
                seen.append(tf)
        return seen


@dataclass
class RiskConfig:
    max_position_size_pct: float = 0.10
    max_portfolio_risk_pct: float = 0.02
    max_drawdown_pct: float = 0.15
    daily_loss_limit_pct: float = 0.02
    weekly_loss_limit_pct: float = 0.05
    monthly_loss_limit_pct: float = 0.08
    max_open_positions: int = 10
    max_sector_concentration: float = 0.25
    min_cash_reserve: float = 0.10
    default_stop_loss_pct: float = 0.02
    default_take_profit_pct: float = 0.04
    default_rr_ratio: float = 2.0
    use_trailing_stop: bool = True
    trailing_stop_pct: float = 0.015
    trailing_stop_atr_multiple: float = 2.0
    earnings_blackout_days: int = 2
    min_avg_volume: int = 500_000
    min_stock_price: float = 5.0
    time_stop_days: int = 10
    max_loss_per_trade_pct: float = 0.02
    per_strategy_daily_loss_limit_pct: float = 0.02
    per_strategy_weekly_loss_limit_pct: float = 0.05
    per_strategy_monthly_loss_limit_pct: float = 0.08
    per_strategy_max_gross_pct: float = 0.10
    per_asset_daily_loss_limit_pct: float = 0.02
    per_asset_weekly_loss_limit_pct: float = 0.05
    per_asset_monthly_loss_limit_pct: float = 0.08


@dataclass
class DailyLossBreakerConfig:
    level1_pct: float = -0.01
    level2_pct: float = -0.015
    level3_pct: float = -0.02
    level4_pct: float = -0.03


@dataclass
class WeeklyLossBreakerConfig:
    level1_pct: float = -0.03
    level2_pct: float = -0.05
    level3_pct: float = -0.07


@dataclass
class MonthlyLossBreakerConfig:
    level1_pct: float = -0.05
    level2_pct: float = -0.08
    level3_pct: float = -0.12


@dataclass
class DrawdownBreakerConfig:
    level1_pct: float = -0.05
    level2_pct: float = -0.08
    level3_pct: float = -0.12
    level4_pct: float = -0.15


@dataclass
class VixBreakerConfig:
    reduce_25: float = 20.0
    reduce_50: float = 25.0
    reduce_75: float = 30.0
    exit_all: float = 40.0
    intraday_spike_pct: float = 0.20


@dataclass
class MarketCrashBreakerConfig:
    benchmark_symbol: str = "SPY"
    yellow_pct: float = -0.02
    orange_pct: float = -0.03
    red_pct: float = -0.05
    sector_crash_pct: float = -0.05
    sector_block_days: int = 3


@dataclass
class FlashCrashBreakerConfig:
    threshold_pct: float = -0.01
    timeframe_minutes: int = 5
    pause_minutes: int = 10
    resume_recovery_pct: float = 0.50


@dataclass
class LiquidityBreakerConfig:
    max_spread_pct: float = 0.01
    min_volume_ratio: float = 0.30


@dataclass
class TechnicalBreakerConfig:
    data_feed_timeout_seconds: int = 120
    data_feed_emergency_seconds: int = 300
    api_retry_attempts: int = 3
    api_retry_delay_seconds: int = 10
    api_failure_escalation_seconds: int = 300
    model_min_confidence: float = 0.40
    max_orders_per_minute: int = 10
    duplicate_order_window_seconds: int = 30
    max_order_attempts: int = 3
    position_mismatch_check_seconds: int = 300


@dataclass
class CircuitBreakerConfig:
    enabled: bool = True
    daily_loss: DailyLossBreakerConfig = field(default_factory=DailyLossBreakerConfig)
    weekly_loss: WeeklyLossBreakerConfig = field(default_factory=WeeklyLossBreakerConfig)
    monthly_loss: MonthlyLossBreakerConfig = field(default_factory=MonthlyLossBreakerConfig)
    drawdown: DrawdownBreakerConfig = field(default_factory=DrawdownBreakerConfig)
    vix: VixBreakerConfig = field(default_factory=VixBreakerConfig)
    market_crash: MarketCrashBreakerConfig = field(default_factory=MarketCrashBreakerConfig)
    flash_crash: FlashCrashBreakerConfig = field(default_factory=FlashCrashBreakerConfig)
    liquidity: LiquidityBreakerConfig = field(default_factory=LiquidityBreakerConfig)
    technical: TechnicalBreakerConfig = field(default_factory=TechnicalBreakerConfig)


@dataclass
class RecoveryConfig:
    day1_3_size_pct: float = 0.25
    day4_7_size_pct: float = 0.50
    week2_size_pct: float = 0.75
    week3_plus_size_pct: float = 1.00
    cooling_off_days: int = 5
    require_positive_performance: bool = True


@dataclass
class PerOrderLimitsConfig:
    max_order_size_pct: float = 0.10
    max_order_value: float = 10_000.0
    min_order_value: float = 100.0
    max_slippage_pct: float = 0.005
    price_sanity_pct: float = 0.05
    max_volume_participation: float = 0.10
    order_confirmation_delay_seconds: int = 2
    duplicate_order_window_seconds: int = 30
    max_attempts: int = 3


@dataclass
class PerStockLimitsConfig:
    max_position_size_pct: float = 0.10
    max_position_value: float = 25_000.0
    max_shares: int = 10_000
    min_avg_volume: int = 500_000
    min_price: float = 5.0
    max_price: float = 10_000.0
    max_orders_per_stock_per_day: int = 3
    earnings_blackout_days: int = 2


@dataclass
class PerDayLimitsConfig:
    max_trades_per_day: int = 20
    max_new_positions_per_day: int = 5
    max_capital_deployed_pct: float = 0.25
    max_daily_loss_pct: float = 0.02
    enforce_pdt_rule: bool = True


@dataclass
class PerPortfolioLimitsConfig:
    max_open_positions: int = 10
    max_sector_concentration: float = 0.25
    max_correlation: float = 0.70
    min_cash_reserve: float = 0.10
    max_leverage: float = 1.0
    max_short_exposure_pct: float = 0.20


@dataclass
class OrderLimitsConfig:
    per_order: PerOrderLimitsConfig = field(default_factory=PerOrderLimitsConfig)
    per_stock: PerStockLimitsConfig = field(default_factory=PerStockLimitsConfig)
    per_day: PerDayLimitsConfig = field(default_factory=PerDayLimitsConfig)
    per_portfolio: PerPortfolioLimitsConfig = field(default_factory=PerPortfolioLimitsConfig)


@dataclass
class PositionSizingConfig:
    method: str = "fixed_risk_pct"
    kelly_fraction: float = 0.5
    kelly_lookback_trades: int = 100
    atr_period: int = 14
    atr_multiplier: float = 2.0
    atr_risk_per_trade: float = 0.02
    volatility_target_annual: float = 0.15
    confidence_scaled: bool = True

    @property
    def sizing_method(self) -> PositionSizingMethod:
        return PositionSizingMethod(self.method)


@dataclass
class ModelsConfig:
    use_lstm: bool = True
    use_transformer: bool = True
    use_xgboost: bool = True
    use_lightgbm: bool = True
    use_rl_agent: bool = True
    use_ensemble: bool = True
    ensemble_method: str = "weighted_vote"
    retrain_frequency: str = "weekly"
    min_training_samples: int = 1000
    validation_split: float = 0.15
    test_split: float = 0.15
    lookback_window: int = 60
    prediction_horizon_days: int = 5
    mc_dropout_samples: int = 50
    drift_p_value: float = 0.01
    model_dir: str = "models/trained"


@dataclass
class SignalWeightsConfig:
    technical: float = 0.30
    ml_models: float = 0.35
    sentiment: float = 0.20
    fundamental: float = 0.10
    macro: float = 0.05


@dataclass
class SignalThresholdsConfig:
    strong_buy: float = 0.60
    weak_buy: float = 0.30
    weak_sell: float = -0.30
    strong_sell: float = -0.60


@dataclass
class SentimentConfig:
    use_news: bool = True
    use_reddit: bool = False
    use_twitter: bool = False
    use_insider: bool = True
    news_decay_hours: int = 24
    sentiment_weight: float = 0.20
    shift_alert_zscore: float = 2.5


@dataclass
class BacktestingConfig:
    default_start: str = "2015-01-01"
    default_end: str = "2024-01-01"
    commission: float = 0.001
    slippage: float = 0.001
    simulate_circuit_breakers: bool = True
    walk_forward_windows: int = 6
    monte_carlo_simulations: int = 10_000
    report_dir: str = "backtesting/reports"


@dataclass
class DataConfig:
    update_frequency: str = "5min"
    historical_years: int = 10
    database_path: str = "data/trading.db"
    cache_ttl_seconds: int = 300
    raw_dir: str = "data/raw"
    processed_dir: str = "data/processed"
    live_dir: str = "data/live"
    alternative_dir: str = "data/alternative"
    clean_outliers: bool = False
    outlier_zscore: float = 8.0
    request_timeout_seconds: float = 30.0
    max_retries: int = 3
    retry_backoff_seconds: float = 2.0
    rate_limit_spacing_seconds: float = 0.5
    universe_cache_hours: int = 24


@dataclass
class SlippageModelConfig:
    large_cap_min_bps: float = 1.0
    large_cap_max_bps: float = 10.0
    small_cap_min_bps: float = 10.0
    small_cap_max_bps: float = 50.0
    large_cap_threshold_mcap: float = 10_000_000_000.0
    high_vol_extra_multiplier: float = 1.5
    extended_hours_multiplier: float = 2.0


@dataclass
class PaperTradingConfig:
    enabled: bool = True
    starting_capital: float = 100_000.0
    mode: str = "full_auto"
    margin_enabled: bool = False
    snapshot_interval_seconds: int = 60
    min_days_before_live: int = 90
    required_sharpe: float = 1.0
    required_max_drawdown: float = 0.15
    required_win_rate: float = 0.50
    slippage_model: SlippageModelConfig = field(default_factory=SlippageModelConfig)
    fill_model: str = "realistic"

    @property
    def paper_mode(self) -> PaperTradingMode:
        return PaperTradingMode(self.mode)


@dataclass
class AutomationConfig:
    timezone: str = "America/New_York"
    pre_market_start: str = "06:00"
    market_open: str = "09:30"
    stop_new_entries: str = "15:45"
    close_day_positions: str = "15:55"
    market_close: str = "16:00"
    post_market_end: str = "18:00"
    monitor_interval_seconds: int = 60
    signal_scan_interval_seconds: int = 300
    sentiment_update_interval_seconds: int = 3600
    watchdog_interval_seconds: int = 30
    task_max_retries: int = 3
    task_retry_delay_seconds: int = 60


@dataclass
class BrokerConfig:
    name: str = "paper_only"
    paper_trading: bool = True
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    request_timeout_seconds: int = 15
    max_retries: int = 3
    retry_delay_seconds: int = 10
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497
    ibkr_client_id: int = 1


@dataclass
class NotificationsConfig:
    enable_email: bool = False
    enable_desktop: bool = True
    enable_sms: bool = False
    enable_telegram: bool = False
    alert_on_signal: bool = True
    alert_on_trade: bool = True
    alert_on_circuit_breaker: bool = True
    alert_on_daily_summary: bool = True
    alert_on_risk_breach: bool = True
    daily_summary_time: str = "16:30"
    max_alerts_per_hour: int = 60
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_to: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


_SECRET_MARKERS = ("SECRET", "PASSWORD", "TOKEN", "KEY")


@dataclass
class ApiKeysConfig:
    yfinance: str = "free_no_key_needed"
    alpha_vantage: str = ""
    finnhub: str = ""
    polygon: str = ""
    newsapi: str = ""
    fred: str = ""
    sec_edgar_user_agent: str = "fin-trade research agent"
    reddit_client_id: str = ""
    reddit_secret: str = ""
    reddit_user_agent: str = "fin-trade/1.0"
    twitter_bearer: str = ""
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""

    def configured(self, name: str) -> bool:
        """True when a non-empty value exists for provider *name*."""
        return bool(str(getattr(self, name, "") or "").strip())

    def redacted(self) -> dict[str, str]:
        """Values masked for logging: never leak secrets into log files."""
        result: dict[str, str] = {}
        for f in fields(self):
            value = str(getattr(self, f.name) or "")
            if not value:
                result[f.name] = "<empty>"
            elif any(marker in f.name.upper() for marker in _SECRET_MARKERS):
                result[f.name] = value[:3] + "***" if len(value) > 3 else "***"
            else:
                result[f.name] = value
        return result


@dataclass
class LoggingConfig:
    level: str = "INFO"
    log_to_file: bool = True
    log_dir: str = "logs/"
    rotate_daily: bool = True
    rotation_time: str = "00:00"
    keep_days: int = 30
    compression: str = "zip"
    structured_json: bool = True
    log_trades: bool = True
    log_signals: bool = True
    log_circuit_breakers: bool = True
    log_model_predictions: bool = True


@dataclass
class PathsConfig:
    data_dir: str = "data"
    notebooks_dir: str = "notebooks"
    reports_dir: str = "backtesting/reports"
    model_dir: str = "models/trained"
    log_dir: str = "logs"


# =============================================================================
# Root config
# =============================================================================


@dataclass
class AppConfig:
    trading: TradingConfig = field(default_factory=TradingConfig)
    watchlist: WatchlistConfig = field(default_factory=WatchlistConfig)
    timeframes: TimeframesConfig = field(default_factory=TimeframesConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    circuit_breakers: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)
    order_limits: OrderLimitsConfig = field(default_factory=OrderLimitsConfig)
    position_sizing: PositionSizingConfig = field(default_factory=PositionSizingConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    signal_weights: SignalWeightsConfig = field(default_factory=SignalWeightsConfig)
    signal_thresholds: SignalThresholdsConfig = field(default_factory=SignalThresholdsConfig)
    sentiment: SentimentConfig = field(default_factory=SentimentConfig)
    backtesting: BacktestingConfig = field(default_factory=BacktestingConfig)
    data: DataConfig = field(default_factory=DataConfig)
    paper_trading: PaperTradingConfig = field(default_factory=PaperTradingConfig)
    automation: AutomationConfig = field(default_factory=AutomationConfig)
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    api_keys: ApiKeysConfig = field(default_factory=ApiKeysConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)

    # populated by the loader (not part of the yaml file)
    config_path: str = ""
    base_dir: str = "."
    warnings: list[str] = field(default_factory=list)

    def resolve_path(self, relative: str) -> Path:
        """Resolve *relative* against the config file's directory."""
        path = Path(relative).expanduser()
        if path.is_absolute():
            return path
        return Path(self.base_dir) / path

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict view (secrets included — use :meth:`redacted` to log)."""
        return asdict(self)

    def redacted(self) -> dict[str, Any]:
        """Serialization-safe view with secrets masked."""
        data = asdict(self)
        data["api_keys"] = self.api_keys.redacted()
        notif = data.get("notifications", {})
        for sensitive in ("smtp_password", "telegram_bot_token"):
            if notif.get(sensitive):
                notif[sensitive] = "***"
        return data

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate(self) -> list[str]:
        """Return a list of validation problems (empty == valid).

        Cross-field invariants, monotonic breaker ladders, enum membership,
        sane ranges, and clock-ordering of the automation schedule are all
        checked here so misconfiguration fails at startup, not mid-trading.
        """
        p: list[str] = []

        def check(cond: bool, msg: str) -> None:
            if not cond:
                p.append(msg)

        def in_unit(x: float, name: str, open_lower: bool = True) -> None:
            lo_ok = x > 0.0 if open_lower else x >= 0.0
            check(lo_ok and x <= 1.0, f"{name} must be in (0,1], got {x}")

        # --- trading ---
        check(self.trading.mode in {m.value for m in TradingMode},
              f"trading.mode must be one of {[m.value for m in TradingMode]}")
        check(self.trading.automation_mode in {m.value for m in AutomationMode},
              f"trading.automation_mode must be one of {[m.value for m in AutomationMode]}")
        check(self.trading.initial_capital > 0, "trading.initial_capital must be > 0")
        for name in ("min_confidence_normal", "min_confidence_restricted", "min_confidence_defensive"):
            in_unit(getattr(self.trading, name), f"trading.{name}")
        check(self.trading.min_confidence_normal <= self.trading.min_confidence_restricted
              <= self.trading.min_confidence_defensive,
              "trading confidence thresholds must be ordered normal <= restricted <= defensive")
        check(self.trading.trading_hours in {"market_only", "extended", "24h"},
              "trading.trading_hours must be market_only | extended | 24h")

        live_env_ok = os.environ.get("FIN_TRADE_LIVE_AUTHORIZATION", "") == LIVE_TRADING_AUTH_PHRASE
        if self.trading.mode == TradingMode.LIVE.value:
            check(self.broker.name in {"alpaca", "ibkr"},
                  "trading.mode=live requires broker.name to be 'alpaca' or 'ibkr'")
            if self.trading.require_live_authorization_env and not live_env_ok:
                p.append("trading.mode=live requires env FIN_TRADE_LIVE_AUTHORIZATION="
                         f"'{LIVE_TRADING_AUTH_PHRASE}'")

        # --- watchlist ---
        check(bool(self.watchlist.custom_stocks) or self.watchlist.use_sp500
              or self.watchlist.use_nasdaq100 or self.watchlist.use_russell2000,
              "watchlist is empty: set custom_stocks and/or a universe flag")

        # --- risk ---
        for name in ("max_position_size_pct", "max_portfolio_risk_pct", "max_drawdown_pct",
                     "daily_loss_limit_pct", "weekly_loss_limit_pct", "monthly_loss_limit_pct",
                     "max_sector_concentration", "min_cash_reserve", "default_stop_loss_pct",
                     "default_take_profit_pct", "trailing_stop_pct", "max_loss_per_trade_pct"):
            in_unit(getattr(self.risk, name), f"risk.{name}")
        check(self.risk.max_open_positions >= 1, "risk.max_open_positions must be >= 1")
        check(self.risk.default_rr_ratio >= 1.0, "risk.default_rr_ratio should be >= 1")
        check(self.risk.earnings_blackout_days >= 0, "risk.earnings_blackout_days must be >= 0")
        check(self.risk.min_stock_price > 0, "risk.min_stock_price must be > 0")
        check(self.risk.time_stop_days >= 1, "risk.time_stop_days must be >= 1")
        check(self.risk.daily_loss_limit_pct <= self.risk.weekly_loss_limit_pct
              <= self.risk.monthly_loss_limit_pct,
              "risk loss limits should be ordered daily <= weekly <= monthly")

        # --- circuit breakers: ladders must be strictly descending negatives ---
        def descending(values: list[float], name: str) -> None:
            check(all(-1.0 < v < 0.0 for v in values),
                  f"circuit_breakers.{name}: all levels must be in (-1, 0)")
            check(all(a > b for a, b in zip(values, values[1:])),
                  f"circuit_breakers.{name}: levels must be strictly descending {values}")

        dl = self.circuit_breakers.daily_loss
        descending([dl.level1_pct, dl.level2_pct, dl.level3_pct, dl.level4_pct], "daily_loss")
        wl = self.circuit_breakers.weekly_loss
        descending([wl.level1_pct, wl.level2_pct, wl.level3_pct], "weekly_loss")
        ml = self.circuit_breakers.monthly_loss
        descending([ml.level1_pct, ml.level2_pct, ml.level3_pct], "monthly_loss")
        dd = self.circuit_breakers.drawdown
        descending([dd.level1_pct, dd.level2_pct, dd.level3_pct, dd.level4_pct], "drawdown")

        vix = self.circuit_breakers.vix
        check(0 < vix.reduce_25 < vix.reduce_50 < vix.reduce_75 < vix.exit_all,
              "circuit_breakers.vix thresholds must increase: reduce_25 < reduce_50 < reduce_75 < exit_all")
        in_unit(vix.intraday_spike_pct, "circuit_breakers.vix.intraday_spike_pct")

        mc = self.circuit_breakers.market_crash
        descending([mc.yellow_pct, mc.orange_pct, mc.red_pct], "market_crash")
        check(-1.0 < mc.sector_crash_pct < 0.0, "circuit_breakers.market_crash.sector_crash_pct must be in (-1,0)")
        check(mc.sector_block_days >= 1, "circuit_breakers.market_crash.sector_block_days must be >= 1")

        fc = self.circuit_breakers.flash_crash
        check(-1.0 < fc.threshold_pct < 0.0, "circuit_breakers.flash_crash.threshold_pct must be in (-1,0)")
        check(fc.timeframe_minutes >= 1, "circuit_breakers.flash_crash.timeframe_minutes must be >= 1")
        check(fc.pause_minutes >= 1, "circuit_breakers.flash_crash.pause_minutes must be >= 1")
        check(0.0 < fc.resume_recovery_pct <= 1.0,
              "circuit_breakers.flash_crash.resume_recovery_pct must be in (0,1]")

        liq = self.circuit_breakers.liquidity
        check(liq.max_spread_pct > 0, "circuit_breakers.liquidity.max_spread_pct must be > 0")
        check(0.0 < liq.min_volume_ratio < 1.0,
              "circuit_breakers.liquidity.min_volume_ratio must be in (0,1)")

        tech = self.circuit_breakers.technical
        for name, minimum in (("data_feed_timeout_seconds", 10), ("data_feed_emergency_seconds", 30),
                              ("api_retry_attempts", 1), ("api_retry_delay_seconds", 1),
                              ("api_failure_escalation_seconds", 30), ("max_orders_per_minute", 1),
                              ("duplicate_order_window_seconds", 1), ("max_order_attempts", 1),
                              ("position_mismatch_check_seconds", 30)):
            check(getattr(tech, name) >= minimum,
                  f"circuit_breakers.technical.{name} must be >= {minimum}")
        check(tech.data_feed_emergency_seconds >= tech.data_feed_timeout_seconds,
              "technical.data_feed_emergency_seconds must be >= data_feed_timeout_seconds")
        check(0.0 < tech.model_min_confidence < 1.0,
              "technical.model_min_confidence must be in (0,1)")

        # --- recovery ---
        rec = self.recovery
        check(0 < rec.day1_3_size_pct <= rec.day4_7_size_pct <= rec.week2_size_pct
              <= rec.week3_plus_size_pct <= 1.0,
              "recovery size multipliers must ascend within (0,1]")
        check(rec.cooling_off_days >= 0, "recovery.cooling_off_days must be >= 0")

        # --- order limits ---
        po = self.order_limits.per_order
        in_unit(po.max_order_size_pct, "order_limits.per_order.max_order_size_pct")
        check(po.max_order_value > po.min_order_value > 0,
              "order_limits.per_order requires max_order_value > min_order_value > 0")
        in_unit(po.max_slippage_pct, "order_limits.per_order.max_slippage_pct")
        in_unit(po.price_sanity_pct, "order_limits.per_order.price_sanity_pct")
        in_unit(po.max_volume_participation, "order_limits.per_order.max_volume_participation")
        check(po.order_confirmation_delay_seconds >= 0, "per_order.order_confirmation_delay_seconds >= 0")
        check(po.duplicate_order_window_seconds >= 1, "per_order.duplicate_order_window_seconds >= 1")
        check(po.max_attempts >= 1, "per_order.max_attempts >= 1")

        ps = self.order_limits.per_stock
        in_unit(ps.max_position_size_pct, "order_limits.per_stock.max_position_size_pct")
        check(ps.max_position_value > 0, "per_stock.max_position_value must be > 0")
        check(ps.max_shares > 0, "per_stock.max_shares must be > 0")
        check(ps.min_avg_volume >= 0, "per_stock.min_avg_volume must be >= 0")
        check(0 < ps.min_price < ps.max_price, "per_stock requires 0 < min_price < max_price")
        check(ps.max_orders_per_stock_per_day >= 1, "per_stock.max_orders_per_stock_per_day >= 1")
        check(ps.earnings_blackout_days >= 0, "per_stock.earnings_blackout_days >= 0")

        pd_ = self.order_limits.per_day
        check(pd_.max_trades_per_day >= 1, "per_day.max_trades_per_day >= 1")
        check(pd_.max_new_positions_per_day >= 1, "per_day.max_new_positions_per_day >= 1")
        in_unit(pd_.max_capital_deployed_pct, "order_limits.per_day.max_capital_deployed_pct")
        in_unit(pd_.max_daily_loss_pct, "order_limits.per_day.max_daily_loss_pct")

        pp = self.order_limits.per_portfolio
        check(pp.max_open_positions >= 1, "per_portfolio.max_open_positions >= 1")
        in_unit(pp.max_sector_concentration, "per_portfolio.max_sector_concentration")
        check(0.0 < pp.max_correlation <= 1.0, "per_portfolio.max_correlation must be in (0,1]")
        in_unit(pp.min_cash_reserve, "per_portfolio.min_cash_reserve")
        check(pp.max_leverage >= 1.0, "per_portfolio.max_leverage must be >= 1.0")
        in_unit(pp.max_short_exposure_pct, "per_portfolio.max_short_exposure_pct")

        # --- position sizing ---
        check(self.position_sizing.method in {m.value for m in PositionSizingMethod},
              f"position_sizing.method must be one of {[m.value for m in PositionSizingMethod]}")
        check(0 < self.position_sizing.kelly_fraction <= 1.0, "kelly_fraction must be in (0,1]")
        check(self.position_sizing.kelly_lookback_trades >= 10, "kelly_lookback_trades >= 10")
        check(self.position_sizing.atr_period >= 2, "atr_period >= 2")
        check(self.position_sizing.atr_multiplier > 0, "atr_multiplier > 0")
        in_unit(self.position_sizing.atr_risk_per_trade, "position_sizing.atr_risk_per_trade")
        check(self.position_sizing.volatility_target_annual > 0, "volatility_target_annual > 0")

        # --- models ---
        for name in ("validation_split", "test_split"):
            check(0.0 < getattr(self.models, name) < 1.0, f"models.{name} must be in (0,1)")
        check(self.models.validation_split + self.models.test_split < 0.9,
              "models.validation_split + test_split must be < 0.9")
        check(self.models.retrain_frequency in {"daily", "weekly", "monthly"},
              "models.retrain_frequency must be daily | weekly | monthly")
        check(self.models.ensemble_method in {"weighted_vote", "stacking", "majority"},
              "models.ensemble_method must be weighted_vote | stacking | majority")
        check(self.models.min_training_samples >= 100, "models.min_training_samples >= 100")
        check(self.models.lookback_window >= 5, "models.lookback_window >= 5")
        check(self.models.prediction_horizon_days >= 1, "models.prediction_horizon_days >= 1")
        check(self.models.mc_dropout_samples >= 5, "models.mc_dropout_samples >= 5")
        check(0 < self.models.drift_p_value < 0.5, "models.drift_p_value must be in (0,0.5)")

        # --- signal weights / thresholds ---
        w = self.signal_weights
        total = w.technical + w.ml_models + w.sentiment + w.fundamental + w.macro
        check(all(0.0 <= v <= 1.0 for v in (w.technical, w.ml_models, w.sentiment,
                                            w.fundamental, w.macro)),
              "signal_weights entries must be in [0,1]")
        check(abs(total - 1.0) <= 0.01, f"signal_weights must sum to 1.0 (got {total:.4f})")

        th = self.signal_thresholds
        check(-1.0 <= th.strong_sell < th.weak_sell <= 0.0 <= th.weak_buy < th.strong_buy <= 1.0,
              "signal_thresholds must order strong_sell < weak_sell <= 0 <= weak_buy < strong_buy")

        # --- sentiment ---
        check(self.sentiment.news_decay_hours >= 1, "sentiment.news_decay_hours >= 1")
        check(self.sentiment.shift_alert_zscore > 0, "sentiment.shift_alert_zscore > 0")

        # --- backtesting ---
        try:
            from datetime import date
            start = date.fromisoformat(self.backtesting.default_start)
            end = date.fromisoformat(self.backtesting.default_end)
            check(start < end, "backtesting.default_start must be before default_end")
        except ValueError as exc:
            p.append(f"backtesting dates must be ISO YYYY-MM-DD ({exc})")
        check(self.backtesting.commission >= 0, "backtesting.commission >= 0")
        check(self.backtesting.slippage >= 0, "backtesting.slippage >= 0")
        check(self.backtesting.walk_forward_windows >= 2, "walk_forward_windows >= 2")
        check(self.backtesting.monte_carlo_simulations >= 100, "monte_carlo_simulations >= 100")

        # --- data ---
        check(self.data.historical_years >= 1, "data.historical_years >= 1")
        check(self.data.cache_ttl_seconds > 0, "data.cache_ttl_seconds > 0")
        check(self.data.outlier_zscore > 0, "data.outlier_zscore > 0")
        check(self.data.request_timeout_seconds > 0, "data.request_timeout_seconds > 0")
        check(self.data.max_retries >= 0, "data.max_retries >= 0")
        check(self.data.retry_backoff_seconds >= 0, "data.retry_backoff_seconds >= 0")
        check(self.data.rate_limit_spacing_seconds >= 0, "data.rate_limit_spacing_seconds >= 0")
        check(self.data.universe_cache_hours >= 1, "data.universe_cache_hours >= 1")

        # --- paper trading ---
        pt = self.paper_trading
        check(pt.starting_capital > 0, "paper_trading.starting_capital > 0")
        check(pt.mode in {m.value for m in PaperTradingMode},
              f"paper_trading.mode must be one of {[m.value for m in PaperTradingMode]}")
        check(pt.min_days_before_live >= 0, "paper_trading.min_days_before_live >= 0")
        check(pt.required_sharpe >= 0, "paper_trading.required_sharpe >= 0")
        in_unit(pt.required_max_drawdown, "paper_trading.required_max_drawdown")
        check(0.0 <= pt.required_win_rate <= 1.0, "paper_trading.required_win_rate must be in [0,1]")
        check(pt.fill_model in {"optimistic", "realistic", "pessimistic"},
              "paper_trading.fill_model must be optimistic | realistic | pessimistic")
        sm = pt.slippage_model
        check(0 <= sm.large_cap_min_bps <= sm.large_cap_max_bps,
              "slippage_model.large_cap_min_bps <= large_cap_max_bps")
        check(0 <= sm.small_cap_min_bps <= sm.small_cap_max_bps,
              "slippage_model.small_cap_min_bps <= small_cap_max_bps")
        check(sm.large_cap_threshold_mcap > 0, "slippage_model.large_cap_threshold_mcap > 0")
        check(sm.high_vol_extra_multiplier >= 1.0, "slippage_model.high_vol_extra_multiplier >= 1")
        check(sm.extended_hours_multiplier >= 1.0, "slippage_model.extended_hours_multiplier >= 1")

        # --- automation schedule ordering ---
        auto = self.automation
        for name in ("pre_market_start", "market_open", "stop_new_entries",
                     "close_day_positions", "market_close", "post_market_end"):
            value = getattr(auto, name)
            check(bool(_TIME_PATTERN.match(value)),
                  f"automation.{name} must be HH:MM (got {value!r})")
        if all(_TIME_PATTERN.match(getattr(auto, n)) for n in
               ("pre_market_start", "market_open", "stop_new_entries", "market_close", "post_market_end")):
            check(auto.pre_market_start < auto.market_open <= auto.stop_new_entries
                  < auto.market_close < auto.post_market_end,
                  "automation schedule must order pre_market_start < market_open <= "
                  "stop_new_entries < market_close < post_market_end")
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(auto.timezone)
        except Exception:
            p.append(f"automation.timezone {auto.timezone!r} is not a valid IANA timezone")
        for name in ("monitor_interval_seconds", "signal_scan_interval_seconds",
                     "sentiment_update_interval_seconds", "watchdog_interval_seconds"):
            check(getattr(auto, name) >= 5, f"automation.{name} >= 5")
        check(auto.task_max_retries >= 0, "automation.task_max_retries >= 0")
        check(auto.task_retry_delay_seconds >= 0, "automation.task_retry_delay_seconds >= 0")

        # --- broker ---
        check(self.broker.name in {"alpaca", "ibkr", "paper_only"},
              "broker.name must be alpaca | ibkr | paper_only")
        check(self.broker.request_timeout_seconds > 0, "broker.request_timeout_seconds > 0")
        check(self.broker.max_retries >= 0, "broker.max_retries >= 0")
        check(self.broker.retry_delay_seconds >= 0, "broker.retry_delay_seconds >= 0")
        check(0 < self.broker.ibkr_port < 65536, "broker.ibkr_port must be a valid TCP port")

        # --- notifications ---
        check(bool(_TIME_PATTERN.match(self.notifications.daily_summary_time)),
              "notifications.daily_summary_time must be HH:MM")
        check(self.notifications.max_alerts_per_hour >= 1, "notifications.max_alerts_per_hour >= 1")
        check(0 < self.notifications.smtp_port < 65536, "notifications.smtp_port invalid")

        # --- logging ---
        check(self.logging.level.upper() in
              {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"},
              "logging.level must be a valid loguru level")
        check(self.logging.keep_days >= 1, "logging.keep_days >= 1")
        check(bool(_TIME_PATTERN.match(self.logging.rotation_time)),
              "logging.rotation_time must be HH:MM")

        return p


# =============================================================================
# Loader
# =============================================================================


def _substitute_env(node: Any, warnings: list[str], path: str = "") -> Any:
    """Recursively expand ${VAR} / ${VAR:-default} in every string."""

    def replacer(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        value = os.environ.get(name)
        if value is None:
            if default is not None:
                return default
            warnings.append(f"Environment variable {name} referenced at {path or '<root>'} is not set; "
                            "using empty string. The related provider/feature will be skipped.")
            return ""
        return value

    if isinstance(node, str):
        return _ENV_PATTERN.sub(replacer, node)
    if isinstance(node, dict):
        return {key: _substitute_env(value, warnings, f"{path}.{key}" if path else str(key))
                for key, value in node.items()}
    if isinstance(node, list):
        return [_substitute_env(item, warnings, path) for item in node]
    return node


def _section(dataclass_type: type, data: Any, warnings: list[str], name: str) -> Any:
    """Build a dataclass from a mapping, tolerating unknown/missing keys.

    Unknown keys produce warnings (helps spot typos in ``config.yaml`` without
    breaking forward compatibility). Missing keys keep the dataclass defaults.
    """
    if data is None:
        return dataclass_type()
    if not isinstance(data, dict):
        warnings.append(f"Section '{name}' must be a mapping; using defaults.")
        return dataclass_type()
    known = {f.name for f in fields(dataclass_type)}
    for key in sorted(set(data) - known):
        warnings.append(f"Unknown key '{name}.{key}' ignored.")
    kwargs = {f.name: data[f.name] for f in fields(dataclass_type) if f.name in data}
    try:
        return dataclass_type(**kwargs)
    except TypeError as exc:
        warnings.append(f"Section '{name}' has incompatible values ({exc}); using defaults.")
        return dataclass_type()




def _build_root(raw: dict[str, Any], warnings: list[str]) -> AppConfig:
    """Assemble the AppConfig tree from substituted yaml data."""
    cfg = AppConfig()

    cfg.trading = _section(TradingConfig, raw.get("trading"), warnings, "trading")
    cfg.watchlist = _section(WatchlistConfig, raw.get("watchlist"), warnings, "watchlist")
    cfg.timeframes = _section(TimeframesConfig, raw.get("timeframes"), warnings, "timeframes")
    cfg.risk = _section(RiskConfig, raw.get("risk"), warnings, "risk")

    cb_raw = raw.get("circuit_breakers") or {}
    cb = CircuitBreakerConfig()
    if isinstance(cb_raw, dict):
        cb.enabled = bool(cb_raw.get("enabled", True))
        cb.daily_loss = _section(DailyLossBreakerConfig, cb_raw.get("daily_loss"), warnings,
                                 "circuit_breakers.daily_loss")
        cb.weekly_loss = _section(WeeklyLossBreakerConfig, cb_raw.get("weekly_loss"), warnings,
                                  "circuit_breakers.weekly_loss")
        cb.monthly_loss = _section(MonthlyLossBreakerConfig, cb_raw.get("monthly_loss"), warnings,
                                   "circuit_breakers.monthly_loss")
        cb.drawdown = _section(DrawdownBreakerConfig, cb_raw.get("drawdown"), warnings,
                               "circuit_breakers.drawdown")
        cb.vix = _section(VixBreakerConfig, cb_raw.get("vix"), warnings, "circuit_breakers.vix")
        cb.market_crash = _section(MarketCrashBreakerConfig, cb_raw.get("market_crash"), warnings,
                                   "circuit_breakers.market_crash")
        cb.flash_crash = _section(FlashCrashBreakerConfig, cb_raw.get("flash_crash"), warnings,
                                  "circuit_breakers.flash_crash")
        cb.liquidity = _section(LiquidityBreakerConfig, cb_raw.get("liquidity"), warnings,
                                "circuit_breakers.liquidity")
        cb.technical = _section(TechnicalBreakerConfig, cb_raw.get("technical"), warnings,
                                "circuit_breakers.technical")
    else:
        warnings.append("circuit_breakers section malformed; using defaults.")
    cfg.circuit_breakers = cb

    cfg.recovery = _section(RecoveryConfig, raw.get("recovery"), warnings, "recovery")

    ol_raw = raw.get("order_limits") or {}
    cfg.order_limits = OrderLimitsConfig(
        per_order=_section(PerOrderLimitsConfig, ol_raw.get("per_order") if isinstance(ol_raw, dict) else None,
                           warnings, "order_limits.per_order"),
        per_stock=_section(PerStockLimitsConfig, ol_raw.get("per_stock") if isinstance(ol_raw, dict) else None,
                           warnings, "order_limits.per_stock"),
        per_day=_section(PerDayLimitsConfig, ol_raw.get("per_day") if isinstance(ol_raw, dict) else None,
                         warnings, "order_limits.per_day"),
        per_portfolio=_section(PerPortfolioLimitsConfig,
                               ol_raw.get("per_portfolio") if isinstance(ol_raw, dict) else None,
                               warnings, "order_limits.per_portfolio"),
    )

    cfg.position_sizing = _section(PositionSizingConfig, raw.get("position_sizing"), warnings,
                                   "position_sizing")
    cfg.models = _section(ModelsConfig, raw.get("models"), warnings, "models")
    cfg.signal_weights = _section(SignalWeightsConfig, raw.get("signal_weights"), warnings,
                                  "signal_weights")
    cfg.signal_thresholds = _section(SignalThresholdsConfig, raw.get("signal_thresholds"), warnings,
                                     "signal_thresholds")
    cfg.sentiment = _section(SentimentConfig, raw.get("sentiment"), warnings, "sentiment")
    cfg.backtesting = _section(BacktestingConfig, raw.get("backtesting"), warnings, "backtesting")
    cfg.data = _section(DataConfig, raw.get("data"), warnings, "data")

    pt_raw = raw.get("paper_trading") or {}
    if isinstance(pt_raw, dict):
        cfg.paper_trading = _section(PaperTradingConfig, pt_raw, warnings, "paper_trading")
        cfg.paper_trading.slippage_model = _section(SlippageModelConfig, pt_raw.get("slippage_model"),
                                                    warnings, "paper_trading.slippage_model")
    cfg.automation = _section(AutomationConfig, raw.get("automation"), warnings, "automation")
    cfg.broker = _section(BrokerConfig, raw.get("broker"), warnings, "broker")
    cfg.notifications = _section(NotificationsConfig, raw.get("notifications"), warnings,
                                 "notifications")
    cfg.api_keys = _section(ApiKeysConfig, raw.get("api_keys"), warnings, "api_keys")
    cfg.logging = _section(LoggingConfig, raw.get("logging"), warnings, "logging")
    cfg.paths = _section(PathsConfig, raw.get("paths"), warnings, "paths")
    cfg.warnings = warnings
    return cfg


_CONFIG_CACHE: dict[str, AppConfig] = {}


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    """Load, substitute, build, and validate the application config.

    Args:
        path: explicit config path; falls back to ``$FIN_TRADE_CONFIG`` and
            finally to ``./config.yaml``.

    Returns:
        A fully validated :class:`AppConfig`.

    Raises:
        ConfigError: file missing/unparseable, or not a mapping.
        ConfigValidationError: semantic validation failures (lists all problems).
    """
    config_path = Path(path or os.environ.get("FIN_TRADE_CONFIG", DEFAULT_CONFIG_PATH)).expanduser()
    if not config_path.exists():
        raise ConfigError(f"Configuration file not found: {config_path.resolve()}")

    # Load .env next to the config first, then CWD; real env always wins.
    load_dotenv(config_path.parent / ".env", override=False)
    if config_path.parent.resolve() != Path.cwd().resolve():
        load_dotenv(Path.cwd() / ".env", override=False)

    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read configuration file {config_path}: {exc}") from exc
    try:
        raw = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML parse error in {config_path}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"Top level of {config_path} must be a mapping, got {type(raw).__name__}")

    warnings: list[str] = []
    substituted = _substitute_env(raw, warnings)
    cfg = _build_root(substituted, warnings)
    cfg.config_path = str(config_path.resolve())
    cfg.base_dir = str(config_path.resolve().parent)

    problems = cfg.validate()
    if problems:
        raise ConfigValidationError(problems)

    for warning in warnings:
        _log.warning("config: {}", warning)
    _log.info("Configuration loaded from {} ({} warning(s))", config_path, len(warnings))
    return cfg


def get_config(path: str | os.PathLike[str] | None = None, reload: bool = False) -> AppConfig:
    """Process-wide cached accessor for the master configuration.

    Args:
        path: optional explicit path; cached per resolved absolute path.
        reload: drop the cache entry and re-read from disk.

    Returns:
        The cached :class:`AppConfig`.
    """
    resolved = str(Path(path or os.environ.get("FIN_TRADE_CONFIG", DEFAULT_CONFIG_PATH))
                     .expanduser().resolve())
    if reload or resolved not in _CONFIG_CACHE:
        _CONFIG_CACHE[resolved] = load_config(resolved)
    return _CONFIG_CACHE[resolved]
