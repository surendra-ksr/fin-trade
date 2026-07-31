"""Phase 9 automation: scheduler (DST + holiday + session guards), approval
queue (TTL + bypass + persistence), recovery ramp (full timeline + REAL
RiskGateway integration), daily digest, and startup reconciliation.

Every test reads time from an INJECTED clock (zero wall-clock). The recovery
integration test drives the ramp-capped quantity through the real
``RiskGateway.transmit -> PaperBroker.submit`` pipeline and asserts the real
broker ledger reflects the reduced size — no parallel limit logic.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from automation.approval_queue import (
    APPROVED,
    CANCELLED,
    EXECUTED,
    EXPIRED,
    PENDING,
    REJECTED,
    ApprovalError,
    ApprovalQueue,
    QueuedSignal,
    _ALLOWED,
)
from automation.digest import DailyDigest, build_digest, render_text
from automation.reconcile import reconcile_positions
from automation.recovery import RecoveryError, RecoveryRamp, ramp_multiplier
from automation.scheduler import (
    MarketScheduler,
    ScheduledJob,
    SessionPhase,
    Scheduler,
    local_wallclock_to_utc,
    session_phase,
)
from risk.circuit_breakers import CircuitBreakerManager, PortfolioSnapshot
from risk.position_limits import PortfolioSnapshot as GatewaySnapshot
from trading.order_types import Order, OrderState
from trading.paper_broker import PaperBroker
from utils.constants import RecoveryPhase

UTC = timezone.utc


def at(year, month, day, hour, minute):
    """Aware UTC instant helper."""
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# ======================================================================
# Session detection
# ======================================================================

def test_local_wallclock_to_utc_is_dst_aware():
    """The same local 09:30 maps to a different UTC offset across DST."""
    est = local_wallclock_to_utc(date(2024, 3, 4), "09:30")   # Standard Time
    edt = local_wallclock_to_utc(date(2024, 4, 1), "09:30")   # Daylight Time
    assert est == datetime(2024, 3, 4, 14, 30, tzinfo=UTC)    # UTC-5
    assert edt == datetime(2024, 4, 1, 13, 30, tzinfo=UTC)    # UTC-4


def test_session_phase_classifies_each_window(app_config):
    """A single trading day spans CLOSED -> PRE -> REGULAR -> POST -> CLOSED.

    Times are UTC during US Eastern Daylight Time (EDT = UTC-4):
    06:00 ET=10:00Z(pre start), 09:30 ET=13:30Z(open), 16:00 ET=20:00Z(close),
    18:00 ET=22:00Z(post end).
    """
    assert session_phase(at(2024, 4, 1, 15, 0), config=app_config) is SessionPhase.REGULAR   # 11:00 ET
    assert session_phase(at(2024, 4, 1, 11, 0), config=app_config) is SessionPhase.PRE_MARKET  # 07:00 ET
    assert session_phase(at(2024, 4, 1, 21, 0), config=app_config) is SessionPhase.POST_MARKET  # 17:00 ET
    assert session_phase(at(2024, 4, 1, 5, 0), config=app_config) is SessionPhase.CLOSED        # 01:00 ET
    assert session_phase(at(2024, 4, 1, 23, 0), config=app_config) is SessionPhase.CLOSED       # 19:00 ET


def test_session_phase_boundaries_are_half_open(app_config):
    """market_open is REGULAR; market_close is POST_MARKET (half-open)."""
    # 09:30 EDT == 13:30 UTC is the first REGULAR instant
    assert session_phase(at(2024, 4, 1, 13, 30), config=app_config) is SessionPhase.REGULAR
    # 16:00 EDT == 20:00 UTC is the first POST_MARKET instant
    assert session_phase(at(2024, 4, 1, 20, 0), config=app_config) is SessionPhase.POST_MARKET


def test_session_phase_weekend_is_closed(app_config):
    """Saturday and Sunday are CLOSED regardless of the clock."""
    assert session_phase(at(2024, 4, 6, 15, 0), config=app_config) is SessionPhase.CLOSED  # Sat
    assert session_phase(at(2024, 4, 7, 15, 0), config=app_config) is SessionPhase.CLOSED  # Sun


@pytest.mark.parametrize("holiday,desc", [
    (date(2024, 7, 4), "Independence Day"),
    (date(2024, 12, 25), "Christmas"),
    (date(2024, 1, 1), "New Year's Day"),
    (date(2024, 11, 28), "Thanksgiving"),
    (date(2024, 9, 2), "Labor Day"),
])
def test_session_phase_nyse_holidays_are_closed(app_config, holiday, desc):
    """NYSE full-day holidays are CLOSED even mid-session."""
    instant = datetime.combine(holiday, datetime.min.time(), tzinfo=UTC).replace(hour=15)
    assert session_phase(instant, config=app_config) is SessionPhase.CLOSED, desc


# ======================================================================
# DST-transition edge tests (spring-forward / fall-back Sundays)
# ======================================================================

def test_dst_spring_forward_sunday_is_closed_and_following_monday_opens_at_edt(app_config):
    """2024-03-10 is the spring-forward Sunday (02:00 local jumps to 03:00).

    The Sunday itself is CLOSED (weekend). The Monday after (2024-03-11) opens
    at 09:30 *Daylight* Time == 13:30 UTC (the DST transition shifted the UTC
    offset from -5 to -4).
    """
    spring_sunday = at(2024, 3, 10, 14, 30)   # 09:30 would-be local on the Sunday
    assert session_phase(spring_sunday, config=app_config) is SessionPhase.CLOSED
    monday_after = at(2024, 3, 11, 13, 30)    # 09:30 EDT on Monday
    assert session_phase(monday_after, config=app_config) is SessionPhase.REGULAR
    monday_before_close = at(2024, 3, 11, 13, 29)
    assert session_phase(monday_before_close, config=app_config) is SessionPhase.PRE_MARKET


def test_dst_fall_back_sunday_is_closed_and_following_monday_opens_at_est(app_config):
    """2024-11-03 is the fall-back Sunday. The Monday after (2024-11-04) opens
    at 09:30 *Standard* Time == 14:30 UTC (offset back to -5)."""
    fall_sunday = at(2024, 11, 3, 14, 30)
    assert session_phase(fall_sunday, config=app_config) is SessionPhase.CLOSED
    monday_after = at(2024, 11, 4, 14, 30)    # 09:30 EST on Monday
    assert session_phase(monday_after, config=app_config) is SessionPhase.REGULAR
    monday_before_close = at(2024, 11, 4, 14, 29)
    assert session_phase(monday_before_close, config=app_config) is SessionPhase.PRE_MARKET


def test_scheduler_same_local_open_resolves_to_correct_utc_across_dst(app_config):
    """A market scheduler using an injected clock opens at the correct UTC
    instant on both sides of a DST transition."""
    sch_est = MarketScheduler(app_config, now_fn=lambda: at(2024, 3, 4, 14, 30))
    sch_edt = MarketScheduler(app_config, now_fn=lambda: at(2024, 4, 1, 13, 30))
    assert sch_est.phase() is SessionPhase.REGULAR   # 09:30 EST == 14:30 UTC
    assert sch_edt.phase() is SessionPhase.REGULAR   # 09:30 EDT == 13:30 UTC


# ======================================================================
# Scheduler: trading_hours policy + intraday guards + job execution
# ======================================================================

def test_execution_allowed_market_only_vs_extended(app_config):
    """market_only restricts to REGULAR; extended admits pre/post too."""
    reg = at(2024, 4, 1, 15, 0)
    pre = at(2024, 4, 1, 11, 0)
    post = at(2024, 4, 1, 20, 30)

    app_config.trading.trading_hours = "market_only"
    sch = MarketScheduler(app_config, now_fn=lambda: reg)
    assert sch.execution_allowed() is True
    sch_pre = MarketScheduler(app_config, now_fn=lambda: pre)
    assert sch_pre.execution_allowed() is False
    sch_post = MarketScheduler(app_config, now_fn=lambda: post)
    assert sch_post.execution_allowed() is False

    app_config.trading.trading_hours = "extended"
    assert MarketScheduler(app_config, now_fn=lambda: pre).execution_allowed() is True
    assert MarketScheduler(app_config, now_fn=lambda: post).execution_allowed() is True

    app_config.trading.trading_hours = "24h"
    # 24h still respects weekends/holidays (CLOSED) but admits any session window
    assert MarketScheduler(app_config, now_fn=lambda: pre).execution_allowed() is True
    assert MarketScheduler(app_config, now_fn=lambda: at(2024, 4, 6, 15, 0)).execution_allowed() is False


def test_entries_allowed_respects_stop_new_entries_guard(app_config):
    """No new entries at/after automation.stop_new_entries (15:45 ET)."""
    # 15:00 ET == 19:00 UTC (EDT): entries allowed
    before = at(2024, 4, 1, 19, 0)
    sch = MarketScheduler(app_config, now_fn=lambda: before)
    assert sch.entries_allowed() is True
    # 15:45 ET == 19:45 UTC: entries blocked
    at_cutoff = at(2024, 4, 1, 19, 45)
    assert MarketScheduler(app_config, now_fn=lambda: at_cutoff).entries_allowed() is False
    # 16:30 ET == 20:30 UTC: after close, blocked by execution_allowed too
    after = at(2024, 4, 1, 20, 30)
    assert MarketScheduler(app_config, now_fn=lambda: after).entries_allowed() is False


def test_scheduler_runs_only_phase_eligible_jobs(app_config):
    clock = {"t": at(2024, 4, 1, 15, 0)}  # REGULAR
    sch = MarketScheduler(app_config, now_fn=lambda: clock["t"])
    ran = []

    sch.add("regular_only", lambda: ran.append("regular"))
    sch.add("pre_only", lambda: ran.append("pre"), phases=(SessionPhase.PRE_MARKET,))
    results = sch.run_due()
    assert "regular_only" in results
    assert "pre_only" not in results
    assert ran == ["regular"]


def test_scheduler_interval_throttle_and_no_wall_clock(app_config):
    """A job throttled by interval only re-runs after the interval elapses,
    driven entirely by the injected clock."""
    clock = {"t": at(2024, 4, 1, 15, 0)}
    sch = MarketScheduler(app_config, now_fn=lambda: clock["t"])
    calls = []
    sch.add("monitor", lambda: calls.append(sch.now()), interval_seconds=300)
    sch.run_due()
    assert len(calls) == 1
    # advance 200s: still within 300s interval -> not due
    clock["t"] = clock["t"] + timedelta(seconds=200)
    sch.run_due()
    assert len(calls) == 1
    # advance another 200s (400s total > 300s): due again
    clock["t"] = clock["t"] + timedelta(seconds=200)
    sch.run_due()
    assert len(calls) == 2


def test_scheduler_failure_is_isolated(app_config):
    """One failing callback must not abort the whole run_due batch."""
    sch = MarketScheduler(app_config, now_fn=lambda: at(2024, 4, 1, 15, 0))

    def boom():
        raise RuntimeError("nope")

    sch.add("boom", boom)
    sch.add("ok", lambda: "done")
    results = sch.run_due()
    assert "error" in results["boom"]
    assert results["ok"] == "done"


def test_scheduler_persists_last_runs_across_restart(app_config, db):
    """last_run timestamps survive a restart via the DB KV store."""
    clock = {"t": at(2024, 4, 1, 15, 0)}
    sch = MarketScheduler(app_config, now_fn=lambda: clock["t"], db=db)
    sch.add("job", lambda: True, interval_seconds=300)
    sch.run_due()
    assert db.kv_get("scheduler:last_runs")["job"] is not None

    # Simulate a restart: new scheduler, same DB, clock advanced past interval.
    clock["t"] = clock["t"] + timedelta(seconds=400)
    sch2 = MarketScheduler(app_config, now_fn=lambda: clock["t"], db=db)
    sch2.add("job", lambda: True, interval_seconds=300)
    # The restored last_run means the job is already due again after the advance.
    assert "job" in sch2.due()


def test_legacy_scheduler_shim_still_works():
    """Pre-Phase-9 Scheduler/Job/run_once import path is preserved."""
    sch = Scheduler()
    sch.add("a", lambda: 1)
    sch.add("b", lambda: 2)
    out = sch.run_once()
    assert out == {"a": 1, "b": 2}


# ======================================================================
# Approval queue: bypass, TTL, transitions, persistence
# ======================================================================

def test_approval_bypass_full_auto(app_config):
    """full_auto bypasses the queue; semi_automated never does."""
    app_config.trading.automation_mode = "full_auto"
    q = ApprovalQueue(app_config, now_fn=lambda: at(2024, 4, 1, 15, 0))
    assert q.bypass() is True
    assert q.requires_approval() is False

    app_config.trading.automation_mode = "semi_automated"
    q2 = ApprovalQueue(app_config, now_fn=lambda: at(2024, 4, 1, 15, 0))
    assert q2.bypass() is False
    assert q2.requires_approval() is True


def test_approval_bypass_hybrid_requires_high_confidence(app_config):
    """hybrid bypasses only above the restricted confidence gate."""
    app_config.trading.automation_mode = "hybrid"
    q = ApprovalQueue(app_config, now_fn=lambda: at(2024, 4, 1, 15, 0))
    threshold = app_config.trading.min_confidence_restricted  # 0.75
    assert q.bypass(confidence=threshold) is True
    assert q.bypass(confidence=threshold - 0.01) is False


def test_approval_enqueue_approve_execute_lifecycle(app_config):
    clock = {"t": at(2024, 4, 1, 15, 0)}
    q = ApprovalQueue(app_config, now_fn=lambda: clock["t"])
    sig = q.enqueue("s1", "AAPL", "buy", 10, 150.0, confidence=0.8)
    assert sig.status == PENDING
    assert q.pending() == [sig]
    assert q.next_approved() is None

    approved = q.approve("s1", by="alice")
    assert approved.status == APPROVED
    assert approved.decision_by == "alice"
    assert q.next_approved() is sig

    executed = q.mark_executed("s1")
    assert executed.status == EXECUTED
    assert q.next_approved() is None


def test_approval_reject_and_cancel(app_config):
    clock = {"t": at(2024, 4, 1, 15, 0)}
    q = ApprovalQueue(app_config, now_fn=lambda: clock["t"])
    q.enqueue("s1", "AAPL", "buy", 10, 150.0)
    q.enqueue("s2", "MSFT", "buy", 5, 300.0)
    assert q.reject("s1", reason="too risky").status == REJECTED
    assert q.cancel("s2").status == CANCELLED
    assert q.pending() == []


def test_approval_transition_table_blocks_illegal_moves(app_config):
    """The _ALLOWED table is the authoritative approval-transition gate."""
    clock = {"t": at(2024, 4, 1, 15, 0)}
    q = ApprovalQueue(app_config, now_fn=lambda: clock["t"])
    q.enqueue("s1", "AAPL", "buy", 10, 150.0)
    q.approve("s1")
    # EXECUTED is terminal -> cannot be re-approved/rejected
    q.mark_executed("s1")
    with pytest.raises(ApprovalError):
        q.approve("s1")
    with pytest.raises(ApprovalError):
        q.reject("s1")
    # Verify the authoritative transition table directly.
    assert APPROVED in _ALLOWED[PENDING]
    assert EXECUTED in _ALLOWED[APPROVED]
    assert APPROVED not in _ALLOWED[EXECUTED]
    assert REJECTED not in _ALLOWED[APPROVED]
    assert _ALLOWED[REJECTED] == frozenset()
    assert _ALLOWED[EXPIRED] == frozenset()
    assert _ALLOWED[EXECUTED] == frozenset()


def test_approval_illegal_transition_raises(app_config):
    clock = {"t": at(2024, 4, 1, 15, 0)}
    q = ApprovalQueue(app_config, now_fn=lambda: clock["t"])
    q.enqueue("s1", "AAPL", "buy", 10, 150.0)
    q.reject("s1")  # terminal
    with pytest.raises(ApprovalError):
        q.approve("s1")
    with pytest.raises(ApprovalError):
        q.mark_executed("nonexistent")


def test_approval_ttl_expires_pending(app_config):
    """Pending signals past their TTL are expired and dropped from execution."""
    clock = {"t": at(2024, 4, 1, 15, 0)}
    q = ApprovalQueue(app_config, now_fn=lambda: clock["t"], ttl_seconds=600)
    q.enqueue("s1", "AAPL", "buy", 10, 150.0)
    assert q.pending() != []
    # advance 601s
    clock["t"] = clock["t"] + timedelta(seconds=601)
    expired = q.expire_due()
    assert len(expired) == 1
    assert expired[0].status == EXPIRED
    assert q.pending() == []
    assert q.next_approved() is None  # expired is not approved


def test_approval_ttl_not_yet_expired_keeps_pending(app_config):
    clock = {"t": at(2024, 4, 1, 15, 0)}
    q = ApprovalQueue(app_config, now_fn=lambda: clock["t"], ttl_seconds=600)
    q.enqueue("s1", "AAPL", "buy", 10, 150.0)
    clock["t"] = clock["t"] + timedelta(seconds=599)
    assert q.expire_due() == []
    assert len(q.pending()) == 1


def test_approval_queue_persists_across_restart(app_config, db):
    """The queue survives a restart via the DB KV store."""
    clock = {"t": at(2024, 4, 1, 15, 0)}
    q = ApprovalQueue(app_config, now_fn=lambda: clock["t"], db=db)
    q.enqueue("s1", "AAPL", "buy", 10, 150.0)
    q.approve("s1")
    assert db.kv_get("approval_queue") is not None

    # Restart: new queue rehydrates pending+approved signals.
    q2 = ApprovalQueue(app_config, now_fn=lambda: clock["t"], db=db)
    restored = q2.get("s1")
    assert restored is not None
    assert restored.status == APPROVED
    assert restored.symbol == "AAPL"
    # The automation_log should have recorded the enqueue + approve.
    log = db.fetch_automation_log()
    assert any(r["action"] == "enqueue" for _, r in log.iterrows())
    assert any("transition:APPROVED" in r["action"] for _, r in log.iterrows())


# ======================================================================
# Recovery ramp: full timeline + REAL RiskGateway integration
# ======================================================================

def test_ramp_multiplier_pure_function_matches_config(app_config):
    """ramp_multiplier is the pure config-ladder expression (verbatim body)."""
    assert ramp_multiplier(0.0, config=app_config) == pytest.approx(0.25)   # day1-3
    assert ramp_multiplier(2.99, config=app_config) == pytest.approx(0.25)
    assert ramp_multiplier(3.0, config=app_config) == pytest.approx(0.50)   # day4-7
    assert ramp_multiplier(6.99, config=app_config) == pytest.approx(0.50)
    assert ramp_multiplier(7.0, config=app_config) == pytest.approx(0.75)   # week2
    assert ramp_multiplier(13.99, config=app_config) == pytest.approx(0.75)
    assert ramp_multiplier(14.0, config=app_config) == pytest.approx(1.00)  # week3+


def test_recovery_full_timeline_freeze_and_restart(app_config):
    """Full ramp timeline: 25 -> 50 -> 75 -> 100, HALT freezes, resume restarts."""
    clock = {"t": datetime(2024, 4, 1, 13, 30, tzinfo=UTC)}
    ramp = RecoveryRamp(app_config, now_fn=lambda: clock["t"])

    # Starts frozen (never resumed) -> 0
    assert ramp.multiplier() == 0.0
    assert ramp.phase() is RecoveryPhase.NONE

    # Human resume restarts the timeline at day 0.
    ramp.resume(equity=100_000.0)
    assert ramp.multiplier() == pytest.approx(0.25)
    assert ramp.phase() is RecoveryPhase.DAYS_1_3

    clock["t"] += timedelta(days=4)
    assert ramp.multiplier() == pytest.approx(0.50)
    assert ramp.phase() is RecoveryPhase.DAYS_4_7

    clock["t"] += timedelta(days=4)
    assert ramp.multiplier() == pytest.approx(0.75)
    assert ramp.phase() is RecoveryPhase.WEEK_2

    clock["t"] += timedelta(days=7)
    assert ramp.multiplier() == pytest.approx(1.00)
    assert ramp.phase() is RecoveryPhase.WEEK_3_PLUS

    # Entering HALTED freezes the ramp (elapsed clock stops, entries blocked).
    ramp.mark_halted()
    assert ramp.frozen is True
    assert ramp.multiplier() == 0.0
    clock["t"] += timedelta(days=30)   # time passes but ramp is frozen
    assert ramp.multiplier() == 0.0

    # Human-approved resume RESTARTS the timeline at day 0 -> back to 25%.
    ramp.resume()
    assert ramp.multiplier() == pytest.approx(0.25)
    assert ramp.phase() is RecoveryPhase.DAYS_1_3


def test_recovery_cooling_off_blocks_entries(app_config):
    """cooling_off_days pause: no entries during the window, resume blocked."""
    clock = {"t": datetime(2024, 4, 1, 13, 30, tzinfo=UTC)}
    ramp = RecoveryRamp(app_config, now_fn=lambda: clock["t"])
    ramp.resume(equity=100_000.0)
    # Level-4 drawdown halt with cooling-off (config: 5 days).
    ramp.mark_halted(cooling_off=True)
    assert ramp.in_cooling_off() is True
    assert ramp.multiplier() == 0.0
    # Resume is blocked during cooling-off (mirrors breaker locked_until gate).
    with pytest.raises(RecoveryError):
        ramp.resume()
    # After cooling-off elapses, resume works and restarts the timeline.
    clock["t"] += timedelta(days=6)
    assert ramp.in_cooling_off() is False
    ramp.resume(equity=100_000.0)
    assert ramp.multiplier() == pytest.approx(0.25)


def test_recovery_size_order_caps_quantity(app_config):
    clock = {"t": datetime(2024, 4, 1, 13, 30, tzinfo=UTC)}
    ramp = RecoveryRamp(app_config, now_fn=lambda: clock["t"])
    ramp.resume(equity=100_000.0)
    assert ramp.size_order(100) == pytest.approx(25.0)     # day1-3: 25%
    clock["t"] += timedelta(days=5)
    assert ramp.size_order(100) == pytest.approx(50.0)     # day4-7: 50%
    clock["t"] += timedelta(days=3)
    assert ramp.size_order(100) == pytest.approx(75.0)     # week2: 75%
    clock["t"] += timedelta(days=7)
    assert ramp.size_order(100) == pytest.approx(100.0)    # week3+: 100%
    # Frozen: 0
    ramp.mark_halted()
    assert ramp.size_order(100) == 0.0


def test_recovery_caps_order_size_through_real_risk_gateway(app_config):
    """INTEGRATION: the ramp-capped quantity flows through the REAL
    RiskGateway.transmit -> PaperBroker.submit and the real broker ledger
    reflects the reduced size. No parallel limit logic."""
    clock = {"t": datetime(2024, 4, 1, 13, 30, tzinfo=UTC)}
    ramp = RecoveryRamp(app_config, now_fn=lambda: clock["t"])
    ramp.resume(equity=100_000.0)   # day1-3 -> 25%

    broker = PaperBroker(config=app_config, clock=lambda: 0.0,
                         fee_bps=0.0, slippage_bps=0.0)
    # Sanity: without the ramp a 100-share order fills 100.
    full = broker.place_order(Order("AAPL", "buy", 100, price=50.0))
    assert full.state is OrderState.FILLED
    assert broker.positions["AAPL"] == 100.0

    # With the ramp, an intended 100-share order is capped to 25 and the REAL
    # broker fills exactly 25 (one quarter of the intended size).
    sized_qty = ramp.size_order(100)
    assert sized_qty == pytest.approx(25.0)
    sized = broker.place_order(Order("MSFT", "buy", sized_qty, price=50.0))
    assert sized.state is OrderState.FILLED
    assert sized.filled_quantity == pytest.approx(25.0)
    assert broker.positions["MSFT"] == pytest.approx(25.0)
    # MSFT did NOT get the full 100 — the ramp governed the real gateway order.
    assert broker.positions["MSFT"] != 100.0


def test_recovery_blocks_order_when_frozen_via_real_gateway(app_config):
    """When the ramp is frozen the sized quantity is 0, so no order is placed
    through the real gateway (the orchestrator skips it)."""
    clock = {"t": datetime(2024, 4, 1, 13, 30, tzinfo=UTC)}
    ramp = RecoveryRamp(app_config, now_fn=lambda: clock["t"])
    # Never resumed -> frozen -> size 0.
    assert ramp.size_order(100) == 0.0
    broker = PaperBroker(config=app_config, clock=lambda: 0.0,
                         fee_bps=0.0, slippage_bps=0.0)
    sized_qty = ramp.size_order(100)
    assert sized_qty == 0.0
    # A zero-quantity order is rejected by the gateway (quantity<=0).
    with pytest.raises(PermissionError):
        broker.place_order(Order("AAPL", "buy", sized_qty, price=50.0))
    assert broker.positions == {}


def test_recovery_observe_breaker_latches_halt(app_config, mem_db):
    """observe_breaker reads the live CircuitBreakerManager state and freezes
    the ramp when the breaker enters HALTED (cross-restart consistency)."""
    clock = {"t": datetime(2024, 4, 1, 13, 30, tzinfo=UTC)}
    breaker = CircuitBreakerManager(app_config, db=mem_db, now_fn=lambda: clock["t"])
    ramp = RecoveryRamp(app_config, now_fn=lambda: clock["t"], db=mem_db)
    ramp.resume(equity=100_000.0)
    assert ramp.multiplier() == pytest.approx(0.25)

    # Drive the breaker into HALTED via a level-3 daily loss (-2%).
    breaker.evaluate(PortfolioSnapshot(timestamp=clock["t"], equity=100_000.0, cash=100_000.0))
    clock["t"] += timedelta(hours=1)
    breaker.evaluate(PortfolioSnapshot(timestamp=clock["t"], equity=98_000.0, cash=100_000.0))
    assert breaker.state.value == "HALTED"

    # The orchestrator's per-cycle observe_breaker freezes the ramp.
    ramp.observe_breaker(breaker)
    assert ramp.halted is True
    assert ramp.multiplier() == 0.0


def test_recovery_persists_across_restart(app_config, db):
    """Ramp state (frozen / restart time) survives a restart via the DB."""
    clock = {"t": datetime(2024, 4, 1, 13, 30, tzinfo=UTC)}
    ramp = RecoveryRamp(app_config, now_fn=lambda: clock["t"], db=db)
    ramp.resume(equity=100_000.0)
    clock["t"] += timedelta(days=5)
    assert ramp.multiplier() == pytest.approx(0.50)
    snap = ramp.snapshot()
    assert snap.frozen is False
    assert snap.recovery_start is not None

    # New ramp, same DB: rehydrates state.
    ramp2 = RecoveryRamp(app_config, now_fn=lambda: clock["t"], db=db)
    assert ramp2.frozen is False
    # Elapsed time is preserved across the restart.
    assert ramp2.multiplier() == pytest.approx(0.50)


# ======================================================================
# Daily digest
# ======================================================================

def _seed_digest_db(db, day=date(2024, 4, 1)):
    """Insert a perf row, an open trade, and a closed trade for `day`."""
    db.upsert_performance_metric(
        day, portfolio_value=101_500.0, cash=50_000.0, invested_value=51_500.0,
        daily_return=0.015, drawdown=-0.01, portfolio_id="default")
    db.insert_paper_trade("AAPL", "buy", 10, datetime(2024, 4, 1, 14, 0, tzinfo=UTC),
                          150.0, strategy="momentum")
    open_trade = db.insert_paper_trade("MSFT", "buy", 5, datetime(2024, 4, 1, 15, 0, tzinfo=UTC),
                                       300.0, strategy="meanrev")
    db.close_paper_trade(open_trade, datetime(2024, 4, 1, 19, 0, tzinfo=UTC), 310.0)
    # log_circuit_breaker_event(category, action_taken, *, level, state_before, ...)
    db.log_circuit_breaker_event("daily_loss", "flatten_all", level=4,
                                 state_before="NORMAL", state_after="HALTED",
                                 timestamp=datetime(2024, 4, 1, 16, 0, tzinfo=UTC))
    db.log_limit_breach("gateway:asset_pct", "DENIED", entity="NVDA",
                        value=0.12, threshold=0.10,
                        timestamp=datetime(2024, 4, 1, 16, 30, tzinfo=UTC))


def test_build_digest_aggregates_all_sources(db):
    _seed_digest_db(db)
    digest = build_digest(db, day=date(2024, 4, 1),
                          now_fn=lambda: at(2024, 4, 1, 22, 0))
    assert isinstance(digest, DailyDigest)
    assert digest.date == "2024-04-01"
    assert digest.equity == pytest.approx(101_500.0)
    assert digest.cash == pytest.approx(50_000.0)
    assert digest.daily_return == pytest.approx(0.015)
    assert digest.drawdown == pytest.approx(-0.01)
    assert digest.snapshot_source == "performance_metrics"
    # One open position (AAPL) remains; MSFT was closed today.
    assert digest.open_position_count == 1
    assert digest.open_positions[0]["symbol"] == "AAPL"
    assert digest.trades_today == 1
    assert digest.new_positions_today == 2   # AAPL + MSFT both opened today
    # Realized P&L from the MSFT close (5 * (310-150) ... but entry was 300):
    # 5*(310-300) = +50
    assert digest.realized_pnl == pytest.approx(50.0)
    assert digest.breaker_count == 1
    assert digest.breaker_events[0]["category"] == "daily_loss"
    assert digest.breach_count == 1
    assert digest.breaches[0]["entity"] == "NVDA"


def test_render_text_contains_key_sections(db):
    _seed_digest_db(db)
    digest = build_digest(db, day=date(2024, 4, 1),
                          now_fn=lambda: at(2024, 4, 1, 22, 0))
    text = render_text(digest)
    assert "Daily Digest" in text
    assert "AAPL" in text
    assert "Breaker events: 1" in text
    assert "Limit breaches: 1" in text
    assert "Realized P&L" in text


def test_build_digest_empty_day_returns_zeros(db):
    """A day with no activity yields a zero-valued digest, not an error."""
    digest = build_digest(db, day=date(2024, 4, 2),
                          now_fn=lambda: at(2024, 4, 2, 22, 0))
    assert digest.equity is None
    assert digest.realized_pnl == 0.0
    assert digest.open_position_count == 0
    assert digest.breaker_count == 0


# ======================================================================
# Startup reconciliation
# ======================================================================

def test_reconcile_matched_positions(db):
    db.insert_paper_trade("AAPL", "buy", 10, datetime(2024, 4, 1, 14, 0, tzinfo=UTC), 150.0)
    db.insert_paper_trade("MSFT", "sell", 5, datetime(2024, 4, 1, 14, 30, tzinfo=UTC), 300.0)
    broker_positions = [
        {"symbol": "AAPL", "quantity": 10, "side": "long"},
        {"symbol": "MSFT", "quantity": 5, "side": "short"},
    ]
    result = reconcile_positions(db, broker_positions,
                                 now_fn=lambda: at(2024, 4, 1, 22, 0))
    assert result.ok is True
    assert result.halted is False
    assert set(result.matched) == {"AAPL", "MSFT"}
    assert result.db_only == [] and result.broker_only == []


def test_reconcile_db_only_and_broker_only_divergence(db):
    db.insert_paper_trade("AAPL", "buy", 10, datetime(2024, 4, 1, 14, 0, tzinfo=UTC), 150.0)
    broker_positions = [
        {"symbol": "AAPL", "quantity": 10, "side": "long"},
        {"symbol": "TSLA", "quantity": 7, "side": "long"},  # broker-only
    ]
    result = reconcile_positions(db, broker_positions,
                                 now_fn=lambda: at(2024, 4, 1, 22, 0))
    assert result.ok is False
    assert result.halted is True
    assert result.db_only == []                      # DB has no TSLA
    assert result.broker_only == [{"symbol": "TSLA", "quantity": 7.0}]


def test_reconcile_quantity_mismatch(db):
    db.insert_paper_trade("AAPL", "buy", 10, datetime(2024, 4, 1, 14, 0, tzinfo=UTC), 150.0)
    broker_positions = [{"symbol": "AAPL", "quantity": 8, "side": "long"}]
    result = reconcile_positions(db, broker_positions,
                                 now_fn=lambda: at(2024, 4, 1, 22, 0))
    assert result.ok is False
    assert result.halted is True
    assert len(result.quantity_mismatches) == 1
    mm = result.quantity_mismatches[0]
    assert mm["symbol"] == "AAPL"
    assert mm["db_quantity"] == pytest.approx(10.0)
    assert mm["broker_quantity"] == pytest.approx(8.0)
    assert mm["delta"] == pytest.approx(2.0)


def test_reconcile_halts_via_breaker_on_mismatch(db, app_config):
    """A mismatch escalates to the breaker as POSITION_MISMATCH, halting new
    entries per policy."""
    breaker = CircuitBreakerManager(app_config, db=db, now_fn=lambda: at(2024, 4, 1, 22, 0))
    db.insert_paper_trade("AAPL", "buy", 10, datetime(2024, 4, 1, 14, 0, tzinfo=UTC), 150.0)
    broker_positions = [{"symbol": "AAPL", "quantity": 9, "side": "long"}]
    result = reconcile_positions(db, broker_positions, breaker=breaker,
                                 now_fn=lambda: at(2024, 4, 1, 22, 0))
    assert result.halted is True
    # The breaker now carries a sticky POSITION_MISMATCH -> next evaluate halts.
    policy = breaker.evaluate(
        PortfolioSnapshot(timestamp=at(2024, 4, 1, 22, 0), equity=100_000.0, cash=100_000.0))
    assert policy.trading_halted is True
    assert not policy.allow_new_entries


def test_reconcile_logs_to_automation_log(db):
    db.insert_paper_trade("AAPL", "buy", 10, datetime(2024, 4, 1, 14, 0, tzinfo=UTC), 150.0)
    broker_positions = [{"symbol": "AAPL", "quantity": 10, "side": "long"}]
    reconcile_positions(db, broker_positions, now_fn=lambda: at(2024, 4, 1, 22, 0))
    log = db.fetch_automation_log(routine="reconcile")
    assert len(log) == 1
    assert log.iloc[0]["action"] == "startup_reconciliation"
    assert log.iloc[0]["result"] == "ok"
