"""Shared constants and enumerations for the fin-trade agent.

Every module in the project uses these definitions instead of bare strings so
that invalid states are impossible to construct and renaming is trivial.

All enums subclass ``str`` so they serialize transparently to JSON/YAML and
compare equal to their string values.
"""

from __future__ import annotations

from enum import Enum, IntEnum

__all__ = [
    "TradingMode",
    "AutomationMode",
    "PaperTradingMode",
    "CircuitBreakerState",
    "AlertLevel",
    "BreakerCategory",
    "OrderSide",
    "PositionSide",
    "OrderType",
    "OrderStatus",
    "TimeInForce",
    "SignalType",
    "PositionSizingMethod",
    "MarketRegime",
    "RecoveryPhase",
    "Timeframe",
    "STATE_SEVERITY",
    "VALID_STATE_TRANSITIONS",
    "STATE_POLICY_DEFAULTS",
    "SIGNAL_THRESHOLD_SETS",
    "TRADING_DAYS_PER_YEAR",
    "TRADING_WEEKS_PER_YEAR",
    "TRADING_MONTHS_PER_YEAR",
    "MINUTES_PER_SESSION",
    "MARKET_TIMEZONE_NAME",
    "LIVE_TRADING_AUTH_PHRASE",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_DATABASE_PATH",
    "DEFAULT_LOG_DIR",
    "TIMEFRAME_TO_TIMEFRAME",
    "LOG_CATEGORIES",
]


# =============================================================================
# Enums
# =============================================================================


class TradingMode(str, Enum):
    """Where orders are ultimately routed."""

    PAPER = "paper"        # internal paper engine, no real money
    LIVE = "live"          # real broker, real money (heavily gated)
    SHADOW = "shadow"      # observe & log only, never executes anything


class AutomationMode(str, Enum):
    """How much human approval the execution path requires."""

    MANUAL = "manual"                  # signals only, human executes
    SEMI_AUTOMATED = "semi_automated"  # queued signals, auto after timeout
    FULL_AUTO = "full_auto"            # no approval needed (paper default)
    HYBRID = "hybrid"                  # auto high-confidence, queue the rest


class PaperTradingMode(str, Enum):
    """Behavior of the paper-trading engine."""

    SHADOW = "shadow"        # records hypothetical trades vs actual, no action
    MANUAL = "manual"        # every trade needs explicit user approval
    SEMI_AUTO = "semi_auto"  # auto above confidence threshold, user can veto
    FULL_AUTO = "full_auto"  # fully autonomous, notifications only


class CircuitBreakerState(str, Enum):
    """Global state machine states for the circuit-breaker system.

    Order of severity (least -> most):
    NORMAL < CAUTION < RESTRICTED < DEFENSIVE < HALTED < EMERGENCY < SUSPENDED

    SUSPENDED means a human took the system fully offline; it outranks
    EMERGENCY because it suppresses even the emergency-flatten automation.
    """

    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    RESTRICTED = "RESTRICTED"
    DEFENSIVE = "DEFENSIVE"
    HALTED = "HALTED"
    EMERGENCY = "EMERGENCY"
    SUSPENDED = "SUSPENDED"


class AlertLevel(IntEnum):
    """Comparable alert severity. Higher is worse."""

    NONE = 0
    INFO = 1
    YELLOW = 2
    ORANGE = 3
    RED = 4
    EMERGENCY = 5


class BreakerCategory(str, Enum):
    """Which layer / detector produced a trigger."""

    DAILY_LOSS = "daily_loss"
    WEEKLY_LOSS = "weekly_loss"
    MONTHLY_LOSS = "monthly_loss"
    DRAWDOWN = "drawdown"
    VIX = "vix"
    MARKET_CRASH = "market_crash"
    FLASH_CRASH = "flash_crash"
    SECTOR_CRASH = "sector_crash"
    LIQUIDITY = "liquidity"
    DATA_FEED = "data_feed"
    API_FAILURE = "api_failure"
    MODEL_FAILURE = "model_failure"
    RUNAWAY_ORDER = "runaway_order"
    POSITION_MISMATCH = "position_mismatch"
    KILL_SWITCH = "kill_switch"
    RECOVERY = "recovery"
    MANUAL = "manual"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    TRAILING_STOP = "trailing_stop"
    BRACKET = "bracket"          # entry + stop + take-profit (OCO)
    OCO = "oco"                  # one-cancels-other
    CONDITIONAL = "conditional"
    SCALE_IN = "scale_in"
    SCALE_OUT = "scale_out"


class OrderStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    TRIGGERED = "triggered"      # stop/stop-limit fired, child order live
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"

    @property
    def is_open(self) -> bool:
        """True while the order can still produce a fill."""
        return self in {
            OrderStatus.PENDING,
            OrderStatus.SUBMITTED,
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.TRIGGERED,
        }

    @property
    def is_terminal(self) -> bool:
        """True when the order will not change again."""
        return not self.is_open


class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"    # good till cancelled
    GTD = "gtd"    # good till date
    IOC = "ioc"    # immediate or cancel
    FOK = "fok"    # fill or kill
    OPG = "opg"    # market on open
    CLS = "cls"    # market on close


class SignalType(str, Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"

    @property
    def is_actionable(self) -> bool:
        """Whether the signal should ever produce an order."""
        return self is not SignalType.HOLD


class PositionSizingMethod(str, Enum):
    FIXED_PCT = "fixed_pct"
    FIXED_RISK_PCT = "fixed_risk_pct"
    KELLY = "kelly"
    ATR_BASED = "atr_based"
    VOLATILITY_TARGET = "volatility_target"


class MarketRegime(str, Enum):
    BULL = "bull"
    BEAR = "bear"
    SIDEWAYS = "sideways"
    HIGH_VOLATILITY = "high_volatility"
    CRASH = "crash"
    UNKNOWN = "unknown"


class RecoveryPhase(str, Enum):
    """Graduated recovery after a trading halt."""

    NONE = "none"
    DAYS_1_3 = "days_1_3"
    DAYS_4_7 = "days_4_7"
    WEEK_2 = "week_2"
    WEEK_3_PLUS = "week_3_plus"


class Timeframe(str, Enum):
    """Canonical bar timeframes used across the whole project."""

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"
    MO1 = "1mo"

    @property
    def yf_interval(self) -> str:
        """Yahoo Finance interval string ('4h' has no native interval)."""
        return {
            Timeframe.M1: "1m",
            Timeframe.M5: "5m",
            Timeframe.M15: "15m",
            Timeframe.M30: "30m",
            Timeframe.H1: "60m",
            Timeframe.H4: "60m",   # fetched as 60m then resampled to 4h
            Timeframe.D1: "1d",
            Timeframe.W1: "1wk",
            Timeframe.MO1: "1mo",
        }[self]

    @property
    def pandas_freq(self) -> str:
        """Pandas offset alias used for resampling/alignment."""
        return {
            Timeframe.M1: "1min",
            Timeframe.M5: "5min",
            Timeframe.M15: "15min",
            Timeframe.M30: "30min",
            Timeframe.H1: "1h",
            Timeframe.H4: "4h",
            Timeframe.D1: "1D",
            Timeframe.W1: "1W",
            Timeframe.MO1: "1MS",
        }[self]

    @property
    def seconds(self) -> int:
        """Nominal bar duration in seconds (approximate for W1/MO1)."""
        return {
            Timeframe.M1: 60,
            Timeframe.M5: 300,
            Timeframe.M15: 900,
            Timeframe.M30: 1800,
            Timeframe.H1: 3600,
            Timeframe.H4: 14400,
            Timeframe.D1: 86400,
            Timeframe.W1: 604800,
            Timeframe.MO1: 2592000,
        }[self]


# =============================================================================
# Circuit-breaker state machine tables
# =============================================================================

#: Numeric severity per state; escalation compares with integers.
STATE_SEVERITY: dict[CircuitBreakerState, int] = {
    CircuitBreakerState.NORMAL: 0,
    CircuitBreakerState.CAUTION: 1,
    CircuitBreakerState.RESTRICTED: 2,
    CircuitBreakerState.DEFENSIVE: 3,
    CircuitBreakerState.HALTED: 4,
    CircuitBreakerState.EMERGENCY: 5,
    CircuitBreakerState.SUSPENDED: 6,
}

# Callers may always escalate to a *worse* state. De-escalation is limited to
# one step at a time and is additionally gated by the manager (conditions must
# clear, locks must expire, HALTED+/recovery require explicit resume()).
VALID_STATE_TRANSITIONS: dict[CircuitBreakerState, frozenset[CircuitBreakerState]] = {
    CircuitBreakerState.NORMAL: frozenset(
        state for state in CircuitBreakerState if state is not CircuitBreakerState.NORMAL
    ),
    CircuitBreakerState.CAUTION: frozenset(
        {
            CircuitBreakerState.NORMAL,
            CircuitBreakerState.RESTRICTED,
            CircuitBreakerState.DEFENSIVE,
            CircuitBreakerState.HALTED,
            CircuitBreakerState.EMERGENCY,
            CircuitBreakerState.SUSPENDED,
        }
    ),
    CircuitBreakerState.RESTRICTED: frozenset(
        {
            CircuitBreakerState.CAUTION,
            CircuitBreakerState.DEFENSIVE,
            CircuitBreakerState.HALTED,
            CircuitBreakerState.EMERGENCY,
            CircuitBreakerState.SUSPENDED,
        }
    ),
    CircuitBreakerState.DEFENSIVE: frozenset(
        {
            CircuitBreakerState.RESTRICTED,
            CircuitBreakerState.HALTED,
            CircuitBreakerState.EMERGENCY,
            CircuitBreakerState.SUSPENDED,
        }
    ),
    CircuitBreakerState.HALTED: frozenset(
        {
            CircuitBreakerState.DEFENSIVE,   # resume path starts one step down
            CircuitBreakerState.EMERGENCY,
            CircuitBreakerState.SUSPENDED,
        }
    ),
    CircuitBreakerState.EMERGENCY: frozenset(
        {
            CircuitBreakerState.HALTED,      # emergency resolved -> still halted
            CircuitBreakerState.SUSPENDED,
        }
    ),
    CircuitBreakerState.SUSPENDED: frozenset(
        {
            CircuitBreakerState.HALTED,      # operator brings system back halted
        }
    ),
}

#: Baseline trading policy per state. The circuit-breaker manager further
#: tightens these values dynamically based on the *worst* active trigger.
STATE_POLICY_DEFAULTS: dict[CircuitBreakerState, dict[str, float | int | bool]] = {
    CircuitBreakerState.NORMAL: {
        "position_size_multiplier": 1.00,
        "confidence_boost": 0.00,
        "allow_new_longs": True,
        "allow_new_shorts": True,
        "allow_new_entries": True,
        "max_open_positions": 10,
    },
    CircuitBreakerState.CAUTION: {
        "position_size_multiplier": 0.85,
        "confidence_boost": 0.05,
        "allow_new_longs": True,
        "allow_new_shorts": True,
        "allow_new_entries": True,
        "max_open_positions": 8,
    },
    CircuitBreakerState.RESTRICTED: {
        "position_size_multiplier": 0.50,
        "confidence_boost": 0.10,
        "allow_new_longs": True,
        "allow_new_shorts": False,
        "allow_new_entries": True,
        "max_open_positions": 5,
    },
    CircuitBreakerState.DEFENSIVE: {
        "position_size_multiplier": 0.25,
        "confidence_boost": 0.25,
        "allow_new_longs": False,
        "allow_new_shorts": False,
        "allow_new_entries": False,
        "max_open_positions": 3,
    },
    CircuitBreakerState.HALTED: {
        "position_size_multiplier": 0.00,
        "confidence_boost": 1.00,
        "allow_new_longs": False,
        "allow_new_shorts": False,
        "allow_new_entries": False,
        "max_open_positions": 0,
    },
    CircuitBreakerState.EMERGENCY: {
        "position_size_multiplier": 0.00,
        "confidence_boost": 1.00,
        "allow_new_longs": False,
        "allow_new_shorts": False,
        "allow_new_entries": False,
        "max_open_positions": 0,
    },
    CircuitBreakerState.SUSPENDED: {
        "position_size_multiplier": 0.00,
        "confidence_boost": 1.00,
        "allow_new_longs": False,
        "allow_new_shorts": False,
        "allow_new_entries": False,
        "max_open_positions": 0,
    },
}

#: Composite score -> SignalType threshold sets per market "mode". The
#: manager picks the set matching the *effective* state (RESTRICTED is used
#: for RESTRICTED/DEFENSIVE and worse).
SIGNAL_THRESHOLD_SETS: dict[str, dict[str, float]] = {
    "NORMAL": {
        "strong_buy": 0.60,
        "weak_buy": 0.30,
        "weak_sell": -0.30,
        "strong_sell": -0.60,
    },
    "RESTRICTED": {"strong_buy": 0.75, "weak_buy": 0.75, "weak_sell": -0.75, "strong_sell": -0.75},
    "DEFENSIVE": {"strong_buy": 1.01, "weak_buy": 1.01, "weak_sell": -0.85, "strong_sell": -0.85},
}


# =============================================================================
# Misc constants
# =============================================================================

TRADING_DAYS_PER_YEAR: int = 252
TRADING_WEEKS_PER_YEAR: int = 52
TRADING_MONTHS_PER_YEAR: int = 12
MINUTES_PER_SESSION: int = 390  # 09:30 -> 16:00 ET

MARKET_TIMEZONE_NAME: str = "America/New_York"

#: Exact environment-phrase required before any live order can be placed.
LIVE_TRADING_AUTH_PHRASE: str = "I-UNDERSTAND-LIVE-TRADING-RISK"

DEFAULT_CONFIG_PATH: str = "config.yaml"
DEFAULT_DATABASE_PATH: str = "data/trading.db"
DEFAULT_LOG_DIR: str = "logs"

#: Logging categories -> dedicated files (see utils/logger.py).
LOG_CATEGORIES: tuple[str, ...] = (
    "app",
    "trades",
    "signals",
    "circuit_breakers",
    "predictions",
    "automation",
    "risk",
    "errors",
)

# Convenience alias kept for backwards compatibility with earlier scaffolding.
TIMEFRAME_TO_TIMEFRAME = Timeframe
