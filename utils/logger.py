"""Centralized, category-aware logging built on loguru.

Design
------
* One human-readable, colorized console sink.
* One file sink per *category* (app, trades, signals, circuit_breakers,
  predictions, automation, risk, errors). Records are routed by the
  ``category`` key bound via ``logger.bind(category=...)``.
* Category files are written as JSON lines (one structured event per line);
  the ``app`` sink additionally captures every record for forensics.
* Files rotate daily at the configured local time, are compressed to zip,
  and are retained for the configured number of days.
* Standard-library logging (used by yfinance, urllib3, apscheduler, ...)
  is intercepted and re-emitted through loguru so everything lands in the
  same files.

The module is intentionally self-contained and safe to import before the
configuration system exists: calling :func:`get_logger` without
:func:`configure_logging` first yields a sensible console-only logger.
"""

from __future__ import annotations

import functools
import logging
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar

from loguru import logger

from .constants import LOG_CATEGORIES

__all__ = [
    "configure_logging",
    "get_logger",
    "log_event",
    "suppress",
    "InterceptHandler",
    "logger",
]

F = TypeVar("F", bound=Callable[..., Any])

_VALID_LEVELS = ("TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL")

_console_format = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{extra[category]: <18}</cyan> | "
    "<cyan>{name}:{function}:{line}</cyan> - "
    "<level>{message}</level>"
)

_file_format_human = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "{extra[category]: <18} | {name}:{function}:{line} - {message}"
)

_configured: bool = False
_sink_ids: list[int] = []


class InterceptHandler(logging.Handler):
    """Route standard-library log records into loguru."""

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame is not None and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.bind(category=record.name.split(".")[0] or "app").opt(
            depth=depth, exception=record.exc_info
        ).log(level, record.getMessage())


def _normalize_level(level: Any) -> str:
    """Coerce arbitrary level input to a valid loguru level name."""
    if isinstance(level, int):
        if level <= logging.DEBUG:
            return "DEBUG"
        if level <= logging.INFO:
            return "INFO"
        if level <= logging.WARNING:
            return "WARNING"
        if level <= logging.ERROR:
            return "ERROR"
        return "CRITICAL"
    name = str(level or "INFO").strip().upper()
    return name if name in _VALID_LEVELS else "INFO"


def _coerce_settings(settings: Any) -> dict[str, Any]:
    """Accept a dict, a config dataclass, or None and return a plain dict."""
    if settings is None:
        return {}
    if isinstance(settings, dict):
        return dict(settings)
    # dataclass-like object from utils.config
    result: dict[str, Any] = {}
    for field_name in (
        "level",
        "log_to_file",
        "log_dir",
        "rotate_daily",
        "rotation_time",
        "keep_days",
        "compression",
        "structured_json",
    ):
        if hasattr(settings, field_name):
            result[field_name] = getattr(settings, field_name)
    return result


def configure_logging(settings: Any = None, **overrides: Any) -> None:
    """(Re)configure all loguru sinks.

    Safe to call multiple times — existing sinks are removed first, which is
    exactly what the test-suite relies on when redirecting logs to tmp dirs.

    Args:
        settings: dict or ``LoggingConfig`` with keys ``level``, ``log_to_file``,
            ``log_dir``, ``rotate_daily``, ``rotation_time``, ``keep_days``,
            ``compression``, ``structured_json``.
        **overrides: keyword arguments that take precedence over *settings*.
    """
    global _configured, _sink_ids

    cfg = _coerce_settings(settings)
    cfg.update(overrides)

    level = _normalize_level(cfg.get("level", "INFO"))
    log_to_file = bool(cfg.get("log_to_file", True))
    log_dir = Path(str(cfg.get("log_dir", "logs")))
    rotate_daily = bool(cfg.get("rotate_daily", True))
    rotation_time = str(cfg.get("rotation_time", "00:00"))
    keep_days = int(cfg.get("keep_days", 30))
    compression = cfg.get("compression", "zip")
    structured_json = bool(cfg.get("structured_json", True))

    # Remove every sink we previously installed (plus the default one).
    for sink_id in _sink_ids:
        try:
            logger.remove(sink_id)
        except ValueError:
            pass  # sink already removed
    _sink_ids = []
    logger.remove()  # remove default stderr sink

    logger.configure(extra={"category": "app"})

    _sink_ids.append(
        logger.add(
            sys.stderr,
            level=level,
            format=_console_format,
            colorize=True,
            backtrace=False,
            diagnose=False,
        )
    )

    if log_to_file:
        rotation: Any = rotation_time if rotate_daily else None
        retention: Any = f"{keep_days} days"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            probe = log_dir / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "Log directory {} not writable ({}); falling back to console-only logging.",
                log_dir,
                exc,
            )
        else:
            enqueue = True  # thread/process safe writes
            _sink_ids.append(
                logger.add(
                    str(log_dir / "app_{time:YYYY-MM-DD}.log"),
                    level=level,
                    format=_file_format_human,
                    rotation=rotation,
                    retention=retention,
                    compression=compression,
                    enqueue=enqueue,
                    backtrace=True,
                    diagnose=False,
                )
            )
            for category in LOG_CATEGORIES:
                if category == "app":
                    continue  # covered above
                # Each category file captures only its own category; the
                # `errors` file additionally captures every ERROR+ record.
                if category == "errors":
                    filter_fn = lambda rec: (  # noqa: E731
                        rec["extra"].get("category") == "errors"
                        or rec["level"].no >= logger.level("ERROR").no
                    )
                else:
                    filter_fn = lambda rec, cat=category: (  # noqa: E731
                        rec["extra"].get("category") == cat
                    )
                _sink_ids.append(
                    logger.add(
                        str(log_dir / (f"{category}_" + "{time:YYYY-MM-DD}.log")),
                        level=level,
                        rotation=rotation,
                        retention=retention,
                        compression=compression,
                        enqueue=enqueue,
                        serialize=structured_json,
                        filter=filter_fn,
                        backtrace=False,
                        diagnose=False,
                    )
                )

    # Pipe stdlib logging through loguru without double-printing.
    logging.root.handlers = [InterceptHandler()]
    logging.root.setLevel(_normalize_level(level))

    _configured = True
    logger.debug("Logging configured: level={} log_dir={} file_sinks={}", level, log_dir, log_to_file)


def get_logger(category: str = "app", **binds: Any) -> Any:
    """Return a bound logger for *category*.

    Args:
        category: routing category (see :data:`LOG_CATEGORIES`). Unknown
            categories fall back to the ``app`` file.
        **binds: extra structured fields attached to every record.

    Example:
        >>> log = get_logger("trades", strategy="mean_reversion")
        >>> log.info("order filled", symbol="AAPL", qty=10)
    """
    global _configured
    if not _configured:
        configure_logging({"log_to_file": False})
    category = category if category in LOG_CATEGORIES else "app"
    return logger.bind(category=category, **binds)


def log_event(category: str, level: str, message: str, **fields: Any) -> None:
    """Emit a single structured event — convenience over :func:`get_logger`."""
    log = get_logger(category)
    log.bind(**fields).log(_normalize_level(level), message)


def suppress(
    *,
    level: str = "ERROR",
    message: str = "{func} failed",
    reraise: bool = False,
    default: Any = None,
    category: str = "app",
    swallow: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[F], F]:
    """Decorator: log exceptions from the wrapped function.

    Args:
        level: log level used when an exception is caught.
        message: message template; ``{func}`` expands to the function name.
        reraise: re-raise after logging when True.
        default: value returned when an exception is swallowed.
        category: log category to route the error into.
        swallow: exception types caught by the decorator.

    Returns:
        The decorator.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except swallow as exc:
                get_logger(category).opt(exception=True).log(
                    _normalize_level(level),
                    message.format(func=getattr(func, "__qualname__", repr(func))),
                )
                if reraise:
                    raise
                return default

        return wrapper  # type: ignore[return-value]

    return decorator


class timed_block:
    """Context manager logging the wall-clock runtime of a code block.

    Example:
        >>> with timed_block("data sync", category="automation"):
        ...     sync_all()
    """

    def __init__(self, label: str, category: str = "app", level: str = "DEBUG") -> None:
        self.label = label
        self.category = category
        self.level = _normalize_level(level)
        self._start = 0.0

    def __enter__(self) -> "timed_block":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        elapsed_ms = (time.perf_counter() - self._start) * 1000.0
        log = get_logger(self.category)
        if exc is None:
            log.log(self.level, "{} completed in {:.1f} ms", self.label, elapsed_ms)
        else:
            log.error("{} failed after {:.1f} ms: {}", self.label, elapsed_ms, exc)
        return False


def flush(sleep_seconds: float = 0.05) -> None:
    """Best-effort flush of enqueued log records (used by tests/atexit)."""
    try:
        time.sleep(max(sleep_seconds, 0.0))  # allow enqueue drainer to catch up
        logger.complete()
    except Exception:  # pragma: no cover - logger.complete() is best effort
        pass


def iter_log_files(log_dir: str | Path, categories: Iterable[str] | None = None) -> list[Path]:
    """List current (uncompressed) log files, optionally filtered by category."""
    directory = Path(log_dir)
    if not directory.exists():
        return []
    prefixes = tuple(f"{cat}_" for cat in categories) if categories else ("app_",) + tuple(
        f"{cat}_" for cat in LOG_CATEGORIES if cat != "app"
    )
    return sorted(p for p in directory.glob("*.log") if p.name.startswith(prefixes))
