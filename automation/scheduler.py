"""US market-hours scheduler — America/New_York aware, with pre/post-market
guards.

Design contract
---------------
* **ALL time is read from an injected clock** (``now_fn``). The scheduler
  itself never calls ``datetime.now()``; every session/holiday/DST branch is
  therefore a pure function of the injected instant and is deterministically
  testable. (Grep ``now`` -> ``self._now`` everywhere; no ``datetime.now``.)
* **Session detection** classifies an instant into one of
  :class:`SessionPhase` values (PRE_MARKET / REGULAR / POST_MARKET / CLOSED)
  using the config schedule (``automation.pre_market_start`` ...
  ``automation.post_market_end``) and the NYSE weekend + holiday calendar.
* **DST is handled by the OS zoneinfo database** (``America/New_York``). A
  local wall-clock instant (e.g. 09:30) resolves to the correct UTC offset for
  its date, so the scheduler opens at 14:30 UTC during US Eastern *Standard*
  Time and 13:30 UTC during US Eastern *Daylight* Time. The spring-forward
  and fall-back Sundays are themselves non-trading days (weekends), but the
  Mondays on either side are detected correctly regardless of which side of
  the transition they fall on.
* The scheduler gates **job execution** by phase + interval, and exposes a
  :meth:`MarketScheduler.execution_allowed` guard driven by the configured
  ``trading.trading_hours`` policy (market_only | extended | 24h) plus the
  ``automation.stop_new_entries`` / ``close_day_positions`` intraday guards.

The scheduler never weakens any breaker threshold — it only *gates* when
work may run; risk enforcement still belongs to the :class:`RiskGateway`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timezone
from enum import Enum
from typing import Any, Callable, Optional

from data.database import DatabaseManager
from utils.config import AppConfig, load_config
from utils.helpers import (
    MARKET_TZ,
    is_trading_day,
    to_utc,
    utc_now,
)
from utils.logger import get_logger

__all__ = [
    "SessionPhase",
    "ScheduledJob",
    "MarketScheduler",
    "local_wallclock_to_utc",
    "session_phase",
    "Scheduler",
    "Job",
]

_log = get_logger("automation")


class SessionPhase(str, Enum):
    """The four classification buckets for any clock instant."""

    PRE_MARKET = "pre_market"   # pre_market_start .. market_open (extended only)
    REGULAR = "regular"         # market_open .. market_close (core session)
    POST_MARKET = "post_market"  # market_close .. post_market_end (extended only)
    CLOSED = "closed"           # weekend, holiday, or outside all windows


# =============================================================================
# Pure session-detection helpers (zero wall-clock)
# =============================================================================


def local_wallclock_to_utc(day: date, hhmm: str) -> datetime:
    """The UTC instant of a local ET wall-clock time (``HH:MM``) on ``day``.

    DST is resolved by the ``America/New_York`` zoneinfo database: the same
    ``09:30`` maps to 14:30 UTC during US Eastern Standard Time and 13:30 UTC
    during US Eastern Daylight Time. This is the one DST-aware conversion the
    rest of the scheduler builds on.
    """
    hours, minutes = (int(part) for part in hhmm.split(":"))
    local = datetime.combine(day, dt_time(hours, minutes), tzinfo=MARKET_TZ)
    return local.astimezone(timezone.utc)


def session_phase(
    at: datetime,
    *,
    config: Optional[AppConfig] = None,
) -> SessionPhase:
    """Classify an aware UTC ``at`` instant into a :class:`SessionPhase`.

    This is the **session-detection** function. It is pure w.r.t. ``at`` and
    ``config`` — it never reads the wall clock — so every branch (weekend,
    holiday, pre/regular/post windows) is deterministically testable.

    Window boundaries (all in exchange local time, DST-correct via
    :func:`local_wallclock_to_utc`):

    * CLOSED            when the day is a weekend or NYSE holiday
    * CLOSED            before ``pre_market_start`` or after ``post_market_end``
    * PRE_MARKET        ``pre_market_start`` <= t < ``market_open``
    * REGULAR           ``market_open`` <= t < ``market_close``
    * POST_MARKET       ``market_close`` <= t < ``post_market_end``
    """
    cfg = config or load_config()
    instant = to_utc(at)
    local = instant.astimezone(MARKET_TZ)
    day = local.date()
    if not is_trading_day(day):
        return SessionPhase.CLOSED
    auto = cfg.automation
    pre_start = local_wallclock_to_utc(day, auto.pre_market_start)
    market_open = local_wallclock_to_utc(day, auto.market_open)
    market_close = local_wallclock_to_utc(day, auto.market_close)
    post_end = local_wallclock_to_utc(day, auto.post_market_end)
    if instant < pre_start or instant >= post_end:
        return SessionPhase.CLOSED
    if instant < market_open:
        return SessionPhase.PRE_MARKET
    if instant < market_close:
        return SessionPhase.REGULAR
    return SessionPhase.POST_MARKET


# =============================================================================
# Scheduler
# =============================================================================


@dataclass
class ScheduledJob:
    """One registered callback plus its phase/interval eligibility."""

    name: str
    callback: Callable[..., Any]
    phases: tuple[SessionPhase, ...]
    interval_seconds: float
    enabled: bool = True
    last_run: Optional[datetime] = None

    def eligible(self, phase: SessionPhase) -> bool:
        return self.enabled and phase in self.phases

    def due(self, phase: SessionPhase, now: datetime) -> bool:
        if not self.eligible(phase):
            return False
        if self.interval_seconds <= 0:
            return True
        if self.last_run is None:
            return True
        return (now - self.last_run).total_seconds() >= self.interval_seconds


class MarketScheduler:
    """US market-hours scheduler. Every timestamp comes from ``now_fn``.

    Args:
        config: master :class:`AppConfig` (schedule + trading_hours policy).
        now_fn: injectable clock returning aware UTC datetimes.
        db: optional :class:`DatabaseManager`; when supplied, ``last_run``
            timestamps persist in ``system_state`` so the schedule survives a
            restart, and every run is recorded in ``automation_log``.
    """

    #: Phases considered "open for business" by each trading_hours policy.
    _POLICY_PHASES: dict[str, frozenset[SessionPhase]] = {
        "market_only": frozenset({SessionPhase.REGULAR}),
        "extended": frozenset({SessionPhase.PRE_MARKET, SessionPhase.REGULAR,
                               SessionPhase.POST_MARKET}),
        "24h": frozenset({SessionPhase.PRE_MARKET, SessionPhase.REGULAR,
                          SessionPhase.POST_MARKET}),
    }

    def __init__(
        self,
        config: Optional[AppConfig] = None,
        *,
        now_fn: Optional[Callable[[], datetime]] = None,
        db: Optional[DatabaseManager] = None,
    ) -> None:
        self.config = config or load_config()
        self._now = now_fn or utc_now
        self.db = db
        self.jobs: dict[str, ScheduledJob] = {}
        if self.db is not None:
            self._load_last_runs()

    # ------------------------------------------------------------------
    # Clock / detection
    # ------------------------------------------------------------------
    def now(self) -> datetime:
        """The current injected-clock instant (aware UTC)."""
        return self._now()

    def phase(self, at: Optional[datetime] = None) -> SessionPhase:
        return session_phase(at or self._now(), config=self.config)

    def is_trading_day(self, at: Optional[datetime] = None) -> bool:
        return is_trading_day((at or self._now()).astimezone(MARKET_TZ).date())

    def is_open(self, at: Optional[datetime] = None) -> bool:
        """True only during the core REGULAR session."""
        return self.phase(at) is SessionPhase.REGULAR

    def execution_allowed(self, at: Optional[datetime] = None) -> bool:
        """Does the configured ``trading_hours`` policy permit execution?

        * ``market_only`` -> REGULAR only
        * ``extended``    -> PRE_MARKET + REGULAR + POST_MARKET
        * ``24h``         -> any non-CLOSED phase (weekends/holidays still closed)
        """
        phase = self.phase(at)
        allowed = self._POLICY_PHASES.get(
            self.config.trading.trading_hours, self._POLICY_PHASES["market_only"])
        return phase in allowed

    def entries_allowed(self, at: Optional[datetime] = None) -> bool:
        """Pre/post-market guards for *new entries*.

        Honors :meth:`execution_allowed` AND the ``stop_new_entries`` intraday
        cutoff: no new entries are admitted at or after that local time (the
        system uses the remainder of the session to manage/flatten only).
        """
        if not self.execution_allowed(at):
            return False
        instant = to_utc(at or self._now())
        day = instant.astimezone(MARKET_TZ).date()
        if not is_trading_day(day):
            return False
        stop_entries = local_wallclock_to_utc(day, self.config.automation.stop_new_entries)
        return instant < stop_entries

    # ------------------------------------------------------------------
    # Job registry
    # ------------------------------------------------------------------
    def add(
        self,
        name: str,
        callback: Callable[..., Any],
        *,
        phases: Optional[tuple[SessionPhase, ...]] = None,
        interval_seconds: float = 0.0,
        enabled: bool = True,
    ) -> ScheduledJob:
        """Register a callback eligible in ``phases`` (default: regular only).

        ``interval_seconds`` throttles repeats; 0 means "run whenever due by
        phase". Existing jobs with the same name are replaced.
        """
        if phases is None:
            phases = (SessionPhase.REGULAR,)
        job = ScheduledJob(
            name=name, callback=callback,
            phases=tuple(phases), interval_seconds=float(interval_seconds),
            enabled=enabled,
        )
        self.jobs[name] = job
        return job

    def remove(self, name: str) -> None:
        self.jobs.pop(name, None)

    def due(self, at: Optional[datetime] = None) -> list[str]:
        """Names of jobs eligible and due at the current clock instant."""
        now = at or self._now()
        phase = self.phase(now)
        return [name for name, job in self.jobs.items() if job.due(phase, now)]

    def run_due(self, at: Optional[datetime] = None) -> dict[str, Any]:
        """Execute every due job once; returns ``{name: result}``.

        Failures are captured per-job (logged, never raised) so one bad
        callback cannot take the schedule down — automation must be robust.
        """
        now = at or self._now()
        results: dict[str, Any] = {}
        for name in self.due(now):
            job = self.jobs[name]
            try:
                results[name] = job.callback()
            except Exception as exc:  # automation resilience
                _log.error("scheduled job '{}' failed: {}", name, exc)
                results[name] = {"error": str(exc)}
                if self.db is not None:
                    try:
                        self.db.log_automation("scheduler", name, "error",
                                               details={"error": str(exc)})
                    except Exception:
                        pass
                continue
            job.last_run = now
            if self.db is not None:
                try:
                    self.db.log_automation("scheduler", name, "ok")
                except Exception:
                    pass
        self._persist_last_runs()
        return results

    # ------------------------------------------------------------------
    # Persistence (survives restart)
    # ------------------------------------------------------------------
    _KV_KEY = "scheduler:last_runs"

    def _load_last_runs(self) -> None:
        assert self.db is not None
        try:
            raw = self.db.kv_get(self._KV_KEY, default={}) or {}
            from utils.helpers import parse_datetime
            for name, ts in raw.items():
                if name in self.jobs and ts:
                    self.jobs[name].last_run = parse_datetime(ts)
        except Exception as exc:
            _log.warning("could not load scheduler last-runs: {}", exc)

    def _persist_last_runs(self) -> None:
        if self.db is None:
            return
        try:
            payload = {
                name: (job.last_run.isoformat() if job.last_run else None)
                for name, job in self.jobs.items()
            }
            self.db.kv_set(self._KV_KEY, payload)
        except Exception as exc:
            _log.warning("could not persist scheduler last-runs: {}", exc)


# =============================================================================
# Backward-compatible shim (pre-Phase-9 callers kept working)
# =============================================================================


@dataclass
class Job:
    """Minimal legacy job record retained for import compatibility."""

    name: str
    callback: object
    enabled: bool = True


class Scheduler:
    """Legacy one-shot runner; delegates to :class:`MarketScheduler`.

    Kept so any pre-Phase-9 ``from automation.scheduler import Scheduler`` call
    site still resolves. New code should use :class:`MarketScheduler`.
    """

    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    def add(self, name: str, callback: object) -> Job:
        self.jobs[name] = Job(name, callback)
        return self.jobs[name]

    def run_once(self) -> dict[str, Any]:
        return {n: j.callback() for n, j in self.jobs.items() if j.enabled}
