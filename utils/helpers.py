"""Utility helpers shared across the fin-trade agent.

Grouped into:

* **time/calendar** — UTC normalization, NYSE holiday calendar (rule-based,
  no external dependency), session bounds, trading-day arithmetic.
* **control flow** — retry decorator, rate limiter.
* **finance math** — returns, drawdowns, Sharpe/Sortino/Calmar, profit factor.
* **OHLCV data** — provider normalization, validation, resampling.
* **statistics** — IQR/Z-score outlier detection, winsorization.
* **containers/IO** — deep merge, flatten, atomic writes, JSON helpers.
* **misc** — id generation, env coercion, formatting.

Everything here is deterministic and side-effect-light; heavy lifting lives
in the dedicated agents. All public helpers are fully type-hinted.
"""

from __future__ import annotations

import functools
import hashlib
import json
import math
import os
import time
import uuid
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional, Sequence, TypeVar
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .constants import MARKET_TIMEZONE_NAME, TRADING_DAYS_PER_YEAR
from .logger import get_logger

__all__ = [
    "utc_now",
    "market_now",
    "to_utc",
    "parse_datetime",
    "to_iso_z",
    "ensure_utc_index",
    "us_market_holidays",
    "easter_sunday",
    "is_trading_day",
    "next_trading_day",
    "previous_trading_day",
    "add_trading_days",
    "trading_days_between",
    "session_bounds",
    "is_market_open",
    "day_key",
    "week_key",
    "month_key",
    "retry",
    "RateLimiter",
    "new_client_order_id",
    "deterministic_signal_id",
    "short_uuid",
    "hash_text",
    "deep_merge",
    "flatten_dict",
    "chunked",
    "dedupe_preserve_order",
    "ensure_directory",
    "atomic_write_text",
    "read_json_file",
    "write_json_file",
    "clamp",
    "safe_divide",
    "simple_returns",
    "log_returns",
    "equity_from_returns",
    "drawdown_series",
    "current_drawdown",
    "max_drawdown",
    "annualized_return",
    "annualized_volatility",
    "sharpe_ratio",
    "sortino_ratio",
    "calmar_ratio",
    "profit_factor",
    "detect_outliers_iqr",
    "detect_outliers_zscore",
    "winsorize_series",
    "OHLCV_COLUMNS",
    "validate_ohlcv",
    "resample_ohlcv",
    "ohlcv_from_provider",
    "getenv_str",
    "getenv_int",
    "getenv_float",
    "getenv_bool",
    "format_currency",
    "format_pct",
    "truncate_text",
    "require_python_version",
]

_log = get_logger("app")

UTC = timezone.utc
MARKET_TZ = ZoneInfo(MARKET_TIMEZONE_NAME)

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])


# =============================================================================
# Time & market calendar
# =============================================================================


def utc_now() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(tz=UTC)


def market_now() -> datetime:
    """Current time in the exchange timezone (America/New_York)."""
    return datetime.now(tz=MARKET_TZ)


def to_utc(value: datetime) -> datetime:
    """Return *value* as an aware UTC datetime (naive input assumed UTC)."""
    if not isinstance(value, datetime):
        raise TypeError(f"to_utc expects datetime, got {type(value).__name__}")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_datetime(value: Any) -> datetime:
    """Parse *value* into an aware UTC datetime.

    Accepts aware/naive datetimes (naive assumed UTC), ``date`` objects,
    ISO-8601 strings (``Z`` suffix supported), and epoch numbers (seconds, or
    milliseconds/microseconds/nanoseconds when the magnitude requires).

    Raises:
        TypeError: for unsupported input types.
        ValueError: for unparseable strings.
    """
    if isinstance(value, datetime):
        return to_utc(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        magnitude = abs(float(value))
        if magnitude >= 1e18:
            return datetime.fromtimestamp(float(value) / 1e9, tz=UTC)
        if magnitude >= 1e15:
            return datetime.fromtimestamp(float(value) / 1e6, tz=UTC)
        if magnitude >= 1e12:
            return datetime.fromtimestamp(float(value) / 1e3, tz=UTC)
        return datetime.fromtimestamp(float(value), tz=UTC)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("Cannot parse empty string as datetime")
        try:
            parsed = pd.to_datetime(text, utc=True)
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"Unparseable datetime string: {value!r}") from exc
        return parsed.to_pydatetime()
    raise TypeError(f"Unsupported datetime input type: {type(value).__name__}")


def to_iso_z(value: Any) -> str:
    """Serialize *value* as ISO-8601 UTC text ('2024-01-02T15:04:05Z').

    Lexicographic ordering of the result equals chronological ordering, which
    is what makes these strings safe as SQLite PRIMARY KEY components.
    """
    dt = parse_datetime(value) if not isinstance(value, datetime) else to_utc(value)
    text = dt.isoformat(timespec="microseconds")
    return text.replace("+00:00", "Z")


def ensure_utc_index(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a DataFrame's DatetimeIndex to UTC (mutates a copy)."""
    out = df.copy()
    if isinstance(out.index, pd.DatetimeIndex):
        if out.index.tz is None:
            out.index = out.index.tz_localize(UTC)
        else:
            out.index = out.index.tz_convert(UTC)
    return out


def easter_sunday(year: int) -> date:
    """Easter Sunday for *year* via the Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _observed(day: date) -> set[date]:
    """Apply NYSE weekend-observance rules to a fixed-date holiday."""
    if day.weekday() == 5:  # Saturday -> observed Friday
        return {day - timedelta(days=1)}
    if day.weekday() == 6:  # Sunday -> observed Monday
        return {day + timedelta(days=1)}
    return {day}


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th *weekday* (Mon=0) of *month*."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The last *weekday* (Mon=0) of *month*."""
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset)


def us_market_holidays(year: int) -> set[date]:
    """NYSE full-day holidays for *year*.

    Covers New Year's Day, MLK Day, Washington's Birthday, Good Friday,
    Memorial Day, Juneteenth (from 2022), Independence Day, Labor Day,
    Thanksgiving, and Christmas, with weekend observance rules. Early-close
    days (1pm ET) are intentionally *not* holidays; intraday logic should
    treat the close time on those dates as advisory only.
    """
    holidays: set[date] = set()
    holidays |= _observed(date(year, 1, 1))                        # New Year's Day
    holidays.add(_nth_weekday(year, 1, 0, 3))                      # MLK (3rd Mon Jan)
    holidays.add(_nth_weekday(year, 2, 0, 3))                      # Presidents (3rd Mon Feb)
    holidays.add(easter_sunday(year) - timedelta(days=2))          # Good Friday
    holidays.add(_last_weekday(year, 5, 0))                        # Memorial Day
    if year >= 2022:
        holidays |= _observed(date(year, 6, 19))                   # Juneteenth
    holidays |= _observed(date(year, 7, 4))                        # Independence Day
    holidays.add(_nth_weekday(year, 9, 0, 1))                      # Labor Day
    holidays.add(_nth_weekday(year, 11, 3, 4))                     # Thanksgiving (4th Thu Nov)
    holidays |= _observed(date(year, 12, 25))                      # Christmas
    # Trading cannot happen on a holiday observed in a neighboring year
    # (e.g. Jan 1 on Saturday observed the preceding Dec 31).
    holidays = {h for h in holidays if h.year == year}
    holidays |= {d for d in _observed(date(year + 1, 1, 1)) if d.year == year}
    return holidays


def is_trading_day(day: date) -> bool:
    """True when NYSE is open for a regular session on *day*."""
    return day.weekday() < 5 and day not in us_market_holidays(day.year)


def next_trading_day(day: date, include: bool = False) -> date:
    """First trading day after (or, with include=True, on/after) *day*."""
    candidate = day if include else day + timedelta(days=1)
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def previous_trading_day(day: date, include: bool = False) -> date:
    """Last trading day before (or, with include=True, on/before) *day*."""
    candidate = day if include else day - timedelta(days=1)
    while not is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def add_trading_days(day: date, n: int) -> date:
    """Shift *day* by *n* trading days (negative moves backwards)."""
    if n == 0:
        return day
    step = 1 if n > 0 else -1
    mover = next_trading_day if step > 0 else previous_trading_day
    result = day
    for _ in range(abs(n)):
        result = mover(result)
    return result


def trading_days_between(start: date, end: date) -> int:
    """Number of trading days in [start, end]."""
    if end < start:
        return 0
    count, cursor = 0, start
    while cursor <= end:
        if is_trading_day(cursor):
            count += 1
        cursor += timedelta(days=1)
    return count


def session_bounds(day: date, open_time: str = "09:30", close_time: str = "16:00") -> tuple[datetime, datetime]:
    """(open, close) of the session on *day* as aware UTC datetimes."""
    open_h, open_m = (int(part) for part in open_time.split(":"))
    close_h, close_m = (int(part) for part in close_time.split(":"))
    open_local = datetime.combine(day, dt_time(open_h, open_m), tzinfo=MARKET_TZ)
    close_local = datetime.combine(day, dt_time(close_h, close_m), tzinfo=MARKET_TZ)
    return open_local.astimezone(UTC), close_local.astimezone(UTC)


def is_market_open(moment: Optional[datetime] = None, *,
                   open_time: str = "09:30", close_time: str = "16:00") -> bool:
    """True when the regular NYSE session covers *moment* (default: now)."""
    instant = to_utc(moment) if moment is not None else utc_now()
    local = instant.astimezone(MARKET_TZ)
    if not is_trading_day(local.date()):
        return False
    open_utc, close_utc = session_bounds(local.date(), open_time, close_time)
    return open_utc <= instant < close_utc


def day_key(moment: Any) -> str:
    """Trading-day anchor key 'YYYY-MM-DD' in the exchange timezone."""
    return parse_datetime(moment).astimezone(MARKET_TZ).strftime("%Y-%m-%d")


def week_key(moment: Any) -> str:
    """ISO week anchor key 'YYYY-Www' in the exchange timezone."""
    local = parse_datetime(moment).astimezone(MARKET_TZ).date()
    year, week, _ = local.isocalendar()
    return f"{year}-W{week:02d}"


def month_key(moment: Any) -> str:
    """Month anchor key 'YYYY-MM' in the exchange timezone."""
    return parse_datetime(moment).astimezone(MARKET_TZ).strftime("%Y-%m")


# =============================================================================
# Control-flow helpers
# =============================================================================


def retry(
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    backoff: float = 2.0,
    max_delay: float = 60.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    give_up_on: tuple[type[BaseException], ...] = (),
) -> Callable[[F], F]:
    """Retry the wrapped function with exponential backoff + jitter.

    Args:
        attempts: total attempts (>= 1).
        base_delay: seconds waited before the first retry.
        backoff: multiplicative growth per retry.
        max_delay: ceiling for the computed delay.
        retry_on: exception classes that trigger another attempt.
        give_up_on: subclasses of retry_on that abort immediately.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = base_delay
            last_exc: Optional[BaseException] = None
            for attempt in range(1, attempts + 1):
                try:
                    return func(*args, **kwargs)
                except give_up_on as exc:
                    _log.error("{} aborted (non-retryable): {}", func.__qualname__, exc)
                    raise
                except retry_on as exc:
                    last_exc = exc
                    if attempt == attempts:
                        break
                    jitter = delay * 0.2 * (2 * np.random.random() - 1)
                    sleep_for = max(0.0, min(delay + jitter, max_delay))
                    _log.warning(
                        "{} attempt {}/{} failed: {}; retrying in {:.1f}s",
                        func.__qualname__, attempt, attempts, exc, sleep_for,
                    )
                    time.sleep(sleep_for)
                    delay = min(delay * backoff, max_delay)
            assert last_exc is not None
            _log.error("{} failed after {} attempt(s): {}", func.__qualname__, attempts, last_exc)
            raise last_exc

        return wrapper  # type: ignore[return-value]

    return decorator


class RateLimiter:
    """Enforce a minimum spacing between calls (thread-safe)."""

    def __init__(self, min_interval_seconds: float) -> None:
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must be >= 0")
        self._interval = min_interval_seconds
        self._next_allowed = 0.0
        import threading

        self._lock = threading.Lock()

    def wait(self) -> float:
        """Block until a call is allowed; returns the slept seconds."""
        with self._lock:
            now = time.monotonic()
            sleep_for = max(0.0, self._next_allowed - now)
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._next_allowed = max(time.monotonic(), self._next_allowed) + self._interval
            return sleep_for

    def __call__(self, func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            self.wait()
            return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]


# =============================================================================
# Ids & hashing
# =============================================================================


def short_uuid(length: int = 12) -> str:
    """URL-safe random identifier fragment."""
    return uuid.uuid4().hex[: max(4, length)]


def new_client_order_id(symbol: str, side: str, *, strategy: str = "") -> str:
    """Globally unique, idempotency-friendly client order id.

    Format: ``FT-<strategy>-<symbol>-<side>-<yyyymmddHHMMSS>-<rand6>``
    """
    stamp = utc_now().strftime("%Y%m%d%H%M%S")
    parts = ["FT", strategy.upper() or "GEN", symbol.upper(), side.upper(), stamp, short_uuid(6)]
    return "-".join(parts)


def deterministic_signal_id(symbol: str, timestamp: Any, source: str, signal_type: str) -> str:
    """Stable id for a signal so the same (symbol, ts, source) dedupes."""
    key = f"{symbol.upper()}|{to_iso_z(timestamp)}|{source}|{signal_type}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def hash_text(text: str, length: int = 16) -> str:
    """Short sha256 hex digest of *text*."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[: max(8, length)]


# =============================================================================
# Containers & IO
# =============================================================================


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into a copy of *base* (override wins)."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], Mapping) and isinstance(value, Mapping):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def flatten_dict(nested: Mapping[str, Any], *, prefix: str = "", sep: str = ".") -> dict[str, Any]:
    """Flatten nested mappings to dotted keys."""
    flat: dict[str, Any] = {}
    for key, value in nested.items():
        full_key = f"{prefix}{sep}{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            flat.update(flatten_dict(value, prefix=full_key, sep=sep))
        else:
            flat[full_key] = value
    return flat


def chunked(items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    """Yield *items* in slices of length *size*."""
    if size < 1:
        raise ValueError("chunk size must be >= 1")
    for start in range(0, len(items), size):
        yield items[start : start + size]


def dedupe_preserve_order(items: Iterable[T]) -> list[T]:
    """Unique values of *items* keeping first-seen order (items must be hashable)."""
    return list(dict.fromkeys(items))


def ensure_directory(path: str | Path) -> Path:
    """mkdir -p *path* and return it as a Path."""
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def atomic_write_text(path: str | Path, content: str, encoding: str = "utf-8") -> Path:
    """Write *content* atomically via a temp file in the same directory."""
    target = Path(path)
    ensure_directory(target.parent)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(content, encoding=encoding)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


def read_json_file(path: str | Path, default: Any = None) -> Any:
    """Read JSON from *path*, returning *default* when unreadable."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning("read_json_file({}) fell back to default: {}", path, exc)
        return default


def write_json_file(path: str | Path, data: Any, *, indent: int = 2) -> Path:
    """Atomically write *data* as pretty JSON."""

    def _default(obj: Any) -> Any:
        if isinstance(obj, (datetime, date)):
            return to_iso_z(obj) if isinstance(obj, datetime) else obj.isoformat()
        if isinstance(obj, (np.integer, np.floating)):
            return obj.item()
        if isinstance(obj, Path):
            return str(obj)
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

    return atomic_write_text(path, json.dumps(data, indent=indent, default=_default, sort_keys=True))


# =============================================================================
# Finance math
# =============================================================================


def clamp(value: float, lower: float, upper: float) -> float:
    """Clamp *value* into [lower, upper]."""
    if lower > upper:
        raise ValueError("lower must be <= upper")
    return max(lower, min(upper, value))


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Division that returns *default* on zero/non-finite denominators."""
    if denominator == 0 or not math.isfinite(denominator):
        return default
    result = numerator / denominator
    return result if math.isfinite(result) else default


def simple_returns(series: pd.Series, periods: int = 1) -> pd.Series:
    """Percent-change simple returns with NaN-first handling."""
    return series.astype(float).pct_change(periods=periods)


def log_returns(series: pd.Series, periods: int = 1) -> pd.Series:
    """Log returns of a price series."""
    values = series.astype(float)
    return np.log(values / values.shift(periods))


def equity_from_returns(returns: pd.Series, initial: float = 1.0) -> pd.Series:
    """Compound a returns series into an equity curve starting at *initial*."""
    clean = returns.fillna(0.0).astype(float)
    return initial * (1.0 + clean).cumprod()


def drawdown_series(equity: pd.Series) -> pd.Series:
    """Peak-to-trough drawdown series (0 at new highs, negative otherwise)."""
    values = equity.astype(float)
    running_max = values.cummax()
    return values / running_max - 1.0


def current_drawdown(equity: float, peak: float) -> float:
    """Fractional drawdown of *equity* vs *peak* (negative when below peak)."""
    if peak <= 0:
        return 0.0
    return safe_divide(equity - peak, peak)


def max_drawdown(equity: pd.Series) -> tuple[float, Optional[pd.Timestamp], Optional[pd.Timestamp]]:
    """Return (max_drawdown, trough_ts, recovery_ts).

    ``recovery_ts`` is None when the equity never recovers to its prior peak.
    """
    if equity.empty:
        return 0.0, None, None
    values = equity.astype(float)
    dd = drawdown_series(values)
    min_dd = float(dd.min())
    if min_dd >= 0:
        return 0.0, None, None
    trough = dd.idxmin()
    peak_value = values.loc[:trough].max()
    after = values.loc[trough:]
    recovered = after[after >= peak_value]
    recovery_ts = recovered.index[0] if not recovered.empty else None
    return min_dd, trough, recovery_ts


def annualized_return(returns: pd.Series, periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    """Geometric annualization of per-period returns."""
    clean = returns.dropna().astype(float)
    if clean.empty:
        return 0.0
    total = float((1.0 + clean).prod())
    years = len(clean) / periods_per_year
    if years <= 0:
        return 0.0
    return total ** (1.0 / years) - 1.0


def annualized_volatility(returns: pd.Series, periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    """Sample std-dev of returns scaled to annual terms."""
    clean = returns.dropna().astype(float)
    if len(clean) < 2:
        return 0.0
    return float(clean.std(ddof=1) * math.sqrt(periods_per_year))


def sharpe_ratio(
    returns: pd.Series,
    risk_free: float = 0.0,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """Annualized Sharpe ratio (period risk-free expressed per period)."""
    clean = (returns.dropna().astype(float)) - risk_free / periods_per_year
    if len(clean) < 2:
        return 0.0
    std = clean.std(ddof=1)
    # tolerance guard: float noise in (near-)degenerate series would otherwise
    # blow the ratio up (std ~1e-19 for a constant series)
    if not math.isfinite(std) or std < 1e-12:
        return 0.0
    return float(clean.mean() / std * math.sqrt(periods_per_year))


def sortino_ratio(
    returns: pd.Series,
    risk_free: float = 0.0,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """Annualized Sortino ratio using downside deviation."""
    excess = returns.dropna().astype(float) - risk_free / periods_per_year
    if len(excess) < 2:
        return 0.0
    downside = excess[excess < 0]
    if downside.empty:
        return float("inf") if excess.mean() > 0 else 0.0
    downside_std = downside.std(ddof=1)
    if downside_std == 0:
        return 0.0
    return float(excess.mean() / downside_std * math.sqrt(periods_per_year))


def calmar_ratio(equity: pd.Series, periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    """Annualized return divided by absolute max drawdown."""
    returns = simple_returns(equity)
    mdd, _, _ = max_drawdown(equity)
    if mdd == 0:
        return 0.0
    return safe_divide(annualized_return(returns, periods_per_year), abs(mdd))


def profit_factor(pnls: Sequence[float]) -> float:
    """Gross profits / gross losses (inf when there are no losses)."""
    gains = sum(p for p in pnls if p > 0)
    losses = abs(sum(p for p in pnls if p < 0))
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


# =============================================================================
# Statistics
# =============================================================================


def detect_outliers_iqr(series: pd.Series, k: float = 1.5) -> pd.Series:
    """Boolean mask (True == outlier) via the Tukey IQR fence method."""
    values = series.astype(float)
    q1, q3 = values.quantile(0.25), values.quantile(0.75)
    iqr = q3 - q1
    if iqr == 0 or not math.isfinite(iqr):
        return pd.Series(False, index=series.index)
    lower, upper = q1 - k * iqr, q3 + k * iqr
    return (values < lower) | (values > upper)


def detect_outliers_zscore(series: pd.Series, threshold: float = 8.0) -> pd.Series:
    """Boolean mask (True == outlier) via robust modified Z on returns/median."""
    values = series.astype(float)
    median = values.median()
    mad = (values - median).abs().median()
    if mad == 0 or not math.isfinite(mad):
        std = values.std(ddof=1)
        if std == 0 or not math.isfinite(std):
            return pd.Series(False, index=series.index)
        return (values - values.mean()).abs() / std > threshold
    modified_z = 0.6745 * (values - median).abs() / mad
    return modified_z > threshold


def winsorize_series(series: pd.Series, limits: tuple[float, float] = (0.01, 0.01)) -> pd.Series:
    """Clip *series* to quantile-based fences (never mutates input)."""
    values = series.astype(float)
    lo_q, hi_q = limits
    if not (0.0 <= lo_q < 1.0 and 0.0 <= hi_q < 1.0):
        raise ValueError("limits must be within [0,1)")
    low = values.quantile(lo_q)
    high = values.quantile(1.0 - hi_q)
    return values.clip(lower=low, upper=high)


# =============================================================================
# OHLCV data
# =============================================================================

OHLCV_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")

_COLUMN_ALIASES = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "adj close": "adj_close",
    "adjclose": "adj_close",
    "adj_close": "adj_close",
    "volume": "volume",
    "dividends": "dividends",
    "stock splits": "stock_splits",
    "datetime": "timestamp",
    "date": "timestamp",
    "time": "timestamp",
    "timestamp": "timestamp",
    "index": "timestamp",
}


def ohlcv_from_provider(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize a provider frame (yfinance-style) to canonical columns.

    Handles MultiIndex columns produced by multi-ticker downloads (keeps the
    first price level), renames columns case-insensitively, moves the
    timestamp into a UTC-aware column, and keeps one row per timestamp.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["timestamp", *OHLCV_COLUMNS, "adj_close"])
    out = df.copy()

    if isinstance(out.columns, pd.MultiIndex):
        # yfinance multi-ticker: level 0 holds price fields
        price_level = 0
        if not {"Open", "Close"} & set(out.columns.get_level_values(price_level)):
            price_level = out.columns.nlevels - 1
        out.columns = [str(col[price_level]) for col in out.columns]

    rename: dict[str, str] = {}
    for col in out.columns:
        key = str(col).strip().lower().replace("_", " ")
        key = _COLUMN_ALIASES.get(key, key.replace(" ", "_"))
        rename[col] = key
    out = out.rename(columns=rename)
    out = out.loc[:, ~out.columns.duplicated(keep="first")]

    if "timestamp" not in out.columns:
        if isinstance(out.index, pd.DatetimeIndex):
            out = out.reset_index().rename(columns={out.index.name or "index": "timestamp"})
        else:
            raise ValueError("Cannot locate a timestamp column/index in provider frame")

    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
    numeric_cols = [c for c in ("open", "high", "low", "close", "volume", "adj_close") if c in out.columns]
    for col in numeric_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["timestamp"]).sort_values("timestamp")
    out = out.drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)
    return out


def validate_ohlcv(
    df: pd.DataFrame,
    *,
    min_rows: int = 1,
    require_positive: bool = True,
) -> tuple[pd.DataFrame, list[str]]:
    """Integrity-check an OHLCV frame; returns (cleaned_df, issues).

    The frame must contain the canonical :data:`OHLCV_COLUMNS` plus a
    ``timestamp`` column. Cleaning keeps the data honest but non-destructive:
    duplicates removed, sorted by time, NaN rows dropped, and *no invented
    values*. Anything suspicious is reported in ``issues`` for the caller to
    log (the data agent logs these into the automation log).
    """
    issues: list[str] = []
    if df is None or df.empty:
        return pd.DataFrame(columns=["timestamp", *OHLCV_COLUMNS]), ["frame is empty"]

    out = df.copy()
    missing = [c for c in ("timestamp", *OHLCV_COLUMNS) if c not in out.columns]
    if missing:
        return pd.DataFrame(), [f"missing columns: {missing}"]

    before = len(out)
    out = out.dropna(subset=["timestamp", "open", "high", "low", "close"])
    if len(out) < before:
        issues.append(f"dropped {before - len(out)} rows with NaN OHLC values")

    before = len(out)
    out = out.drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp")
    if len(out) < before:
        issues.append(f"removed {before - len(out)} duplicate timestamps")
    out = out.reset_index(drop=True)

    if require_positive:
        bad_price = out[(out["open"] <= 0) | (out["high"] <= 0) | (out["low"] <= 0) | (out["close"] <= 0)]
        if not bad_price.empty:
            issues.append(f"{len(bad_price)} rows with non-positive prices")
            out = out.drop(index=bad_price.index)

    neg_volume = out[out["volume"] < 0]
    if not neg_volume.empty:
        issues.append(f"{len(neg_volume)} rows with negative volume")
        out = out.drop(index=neg_volume.index)

    inconsistent = out[(out["high"] < out["low"]) | (out["high"] < out["open"]) |
                       (out["high"] < out["close"]) | (out["low"] > out["open"]) |
                       (out["low"] > out["close"])]
    if not inconsistent.empty:
        issues.append(f"{len(inconsistent)} rows violate OHLC consistency")

    if len(out) < min_rows:
        issues.append(f"only {len(out)} rows after cleaning (min_rows={min_rows})")

    return out, issues


def resample_ohlcv(df: pd.DataFrame, rule: str, *, timestamp_col: str = "timestamp") -> pd.DataFrame:
    """Downsample OHLCV bars to a *rule* ('4h', '1D', ...).

    Bins are **left-labeled, left-closed, and anchored to the first
    timestamp** (`origin="start"`): intraday aggregation therefore starts at
    the session's first bar (e.g. 09:30) instead of midnight, which is what
    traders expect from e.g. 4-hour candles derived from 1-hour bars.
    """
    if df.empty:
        return df.copy()
    if timestamp_col not in df.columns:
        raise ValueError(f"missing timestamp column {timestamp_col!r}")
    out = df.copy()
    out[timestamp_col] = pd.to_datetime(out[timestamp_col], utc=True)
    agg: dict[str, str] = {"open": "first", "high": "max", "low": "min",
                           "close": "last", "volume": "sum"}
    agg = {k: v for k, v in agg.items() if k in out.columns}
    result = (
        out.set_index(timestamp_col)
        .resample(rule, label="left", closed="left", origin="start")
        .agg(agg)
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )
    return result


# =============================================================================
# Environment coercion
# =============================================================================


def getenv_str(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return value if value is not None else default


def getenv_int(name: str, default: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        _log.warning("env {}={!r} is not an int; using default {}", name, raw, default)
        return default


def getenv_float(name: str, default: float = 0.0) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        _log.warning("env {}={!r} is not a float; using default {}", name, raw, default)
        return default


def getenv_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# =============================================================================
# Formatting & misc
# =============================================================================


def format_currency(value: float, currency: str = "$") -> str:
    """Human-friendly currency string ('$1,234.56')."""
    return f"{currency}{value:,.2f}"


def format_pct(value: float, decimals: int = 2) -> str:
    """Format a fractional value as a percentage string ('-1.23%')."""
    return f"{value * 100:.{decimals}f}%"


def truncate_text(text: str, max_length: int = 120, suffix: str = "...") -> str:
    """Truncate *text* to *max_length*, preserving word boundaries."""
    if len(text) <= max_length:
        return text
    cut = text[: max_length - len(suffix)].rsplit(" ", 1)[0]
    return cut + suffix


def require_python_version(major: int = 3, minor: int = 11) -> None:
    """Raise RuntimeError unless the interpreter is >= major.minor."""
    import sys

    if sys.version_info < (major, minor):
        raise RuntimeError(
            f"fin-trade requires Python >= {major}.{minor}; running {sys.version.split()[0]}"
        )
