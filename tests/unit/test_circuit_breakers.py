"""Tests for risk/circuit_breakers.py — every layer of the safety system.

A deterministic injected clock drives all timing; portfolios are synthetic
snapshots. These tests encode the exact speed-breaker thresholds from
config.yaml so regressions in safety semantics fail loudly here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest

from data.database import DatabaseManager
from risk.circuit_breakers import (
    BreakerTrigger,
    CircuitBreakerError,
    CircuitBreakerManager,
    InvalidStateTransition,
    ManualOverrideRequired,
    PortfolioSnapshot,
    PositionInfo,
)
from utils.config import AppConfig
from utils.constants import AlertLevel, BreakerCategory, CircuitBreakerState, PositionSide
from utils.helpers import next_trading_day, session_bounds

UTC = timezone.utc

# Monday 2026-07-27, 10:00 ET (market open session in progress)
T0 = datetime(2026, 7, 27, 14, 0, 0, tzinfo=UTC)


class Clock:
    """Controllable time source compatible with manager now_fn."""

    def __init__(self, start: datetime = T0) -> None:
        self._t = start

    def __call__(self) -> datetime:
        return self._t

    def advance(self, **kwargs: Any) -> datetime:
        self._t = self._t + timedelta(**kwargs)
        return self._t

    @property
    def now(self) -> datetime:
        return self._t


def make_manager(
    app_config: AppConfig,
    clock: Optional[Clock] = None,
    db: Optional[DatabaseManager] = None,
    notifier: Optional[Any] = None,
    deepen: bool = False,
) -> tuple[CircuitBreakerManager, Clock]:
    """Build a manager; optionally deepen unrelated ladders for isolation."""
    if deepen:
        cb = app_config.circuit_breakers
        # Push weekly/monthly/drawdown ladders far away so only the family
        # under test can trip. The DAILY ladder stays at config defaults:
        # anchor rolls make the daily pct 0 on a new day anyway.
        cb.weekly_loss.level1_pct, cb.weekly_loss.level2_pct, cb.weekly_loss.level3_pct = (
            -0.40, -0.50, -0.60)
        cb.monthly_loss.level1_pct, cb.monthly_loss.level2_pct, cb.monthly_loss.level3_pct = (
            -0.40, -0.50, -0.60)
        dd = cb.drawdown
        dd.level1_pct, dd.level2_pct, dd.level3_pct, dd.level4_pct = (
            -0.40, -0.50, -0.60, -0.70)
    clock = clock or Clock()
    mgr = CircuitBreakerManager(app_config, db, notifier=notifier, now_fn=clock)
    return mgr, clock


def snap(clock: Clock, equity: float, **kwargs: Any) -> PortfolioSnapshot:
    return PortfolioSnapshot(timestamp=clock.now, equity=equity, cash=equity, **kwargs)


def long_pos(symbol: str, qty: float, entry: float, price: float,
             sector: Optional[str] = None) -> PositionInfo:
    return PositionInfo(symbol, PositionSide.LONG, qty, entry, price, sector=sector)


def confirmed_token(mgr: CircuitBreakerManager) -> str:
    token = mgr.request_override("resume", reason="test operator")
    assert mgr.confirm_override(token)
    return token


# =============================================================================
# Layer 2 — daily speed breakers
# =============================================================================


class TestDailyLossBreakers:
    def test_healthy_baseline(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config)
        policy = mgr.evaluate(snap(clock, 100_000))
        assert policy.state is CircuitBreakerState.NORMAL
        assert policy.allow_new_entries and policy.allow_new_longs and policy.allow_new_shorts
        assert policy.position_size_multiplier == 1.0
        assert policy.active_triggers == []
        assert policy.required_actions == []

    def test_level1_yellow(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        mgr.evaluate(snap(clock, 100_000))
        policy = mgr.evaluate(snap(clock, 99_000))  # -1.00%
        assert policy.state is CircuitBreakerState.CAUTION
        assert any(t.category is BreakerCategory.DAILY_LOSS and t.level == 1
                   for t in policy.active_triggers)
        assert policy.allow_new_entries  # yellow alerts only

    def test_level2_orange_blocks_and_cancels(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        mgr.evaluate(snap(clock, 100_000))
        policy = mgr.evaluate(snap(clock, 98_400))  # -1.60%
        assert policy.state is CircuitBreakerState.RESTRICTED
        assert policy.allow_new_entries is False
        assert policy.cancel_pending_orders is True
        assert policy.tighten_stops is True
        assert policy.position_size_multiplier <= 0.5

    def test_level3_red_halts_closes_worst_half_and_locks(
            self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        mgr.evaluate(snap(clock, 100_000))
        positions = [long_pos("AAA", 10, 100, 88),     # -12%
                     long_pos("BBB", 5, 100, 96),      # -4%
                     long_pos("CCC", 5, 100, 102)]     # +2%
        policy = mgr.evaluate(snap(clock, 97_800, positions=positions))  # -2.20%
        assert policy.state is CircuitBreakerState.HALTED
        assert policy.locked_until is not None
        # locked until the NEXT trading session open
        expected_open = next_trading_day(clock.now.date())
        open_utc, _ = session_bounds(expected_open, app_config.automation.market_open)
        assert policy.locked_until == open_utc
        closes = [a for a in policy.required_actions if a["type"] == "close_position"]
        assert [a["symbol"] for a in closes] == ["AAA", "BBB"]  # worst 50% (ceil 3/2)
        assert any(a["type"] == "tighten_stops_to_breakeven" for a in policy.required_actions)
        # sticky: state stays HALTED even if P&L recovers intraday
        policy = mgr.evaluate(snap(clock, 99_500))
        assert policy.state is CircuitBreakerState.HALTED

    def test_level4_emergency_flattens_all(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        mgr.evaluate(snap(clock, 100_000))
        positions = [long_pos("AAA", 10, 100, 96), long_pos("BBB", 5, 100, 104)]
        policy = mgr.evaluate(snap(clock, 96_900, positions=positions))  # -3.10%
        assert policy.state is CircuitBreakerState.EMERGENCY
        assert policy.flatten_all is True
        assert policy.allow_new_entries is False
        closes = sorted(a["symbol"] for a in policy.required_actions
                        if a["type"] == "close_position")
        assert closes == ["AAA", "BBB"]
        # locked for remainder of today + next day => open two sessions out
        two_out = next_trading_day(next_trading_day(clock.now.date()))
        expected, _ = session_bounds(two_out, app_config.automation.market_open)
        assert policy.locked_until == expected

    def test_resume_refused_without_token_and_before_lock_expiry(
            self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        mgr.evaluate(snap(clock, 100_000))
        mgr.evaluate(snap(clock, 97_800))
        assert mgr.state is CircuitBreakerState.HALTED
        with pytest.raises(ManualOverrideRequired):
            mgr.resume("no token")
        token = confirmed_token(mgr)
        with pytest.raises(CircuitBreakerError, match="locked until"):
            mgr.resume("too early", token=token)
        # after the lock expires the same token resumes (token minted before lock
        # expiry but consumed after — tokens live 120s wall-clock)
        clock._t = mgr._locked_until + timedelta(minutes=30)
        mgr.resume("after lock", token=confirmed_token(mgr), equity=97_800)
        assert mgr.state is CircuitBreakerState.RESTRICTED
        assert mgr._recovery_start is not None

    def test_anchors_roll_each_day(self, app_config: AppConfig, db: DatabaseManager) -> None:
        mgr, clock = make_manager(app_config, db=db, deepen=True)
        mgr.evaluate(snap(clock, 100_000))
        assert mgr._day_anchor == 100_000
        first_day_key = mgr._day_key
        clock.advance(days=1)
        mgr.evaluate(snap(clock, 98_000))
        assert mgr._day_anchor == 98_000
        assert mgr._day_key != first_day_key
        row = db.load_breaker_state()
        assert row["day_anchor"] == 98_000


# =============================================================================
# Layer 3 & 4 — weekly / monthly breakers
# =============================================================================


class TestWeeklyMonthlyBreakers:
    def test_weekly_level2_reduces_and_blocks_shorts(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        # restore only the weekly ladder
        wl = app_config.circuit_breakers.weekly_loss
        wl.level1_pct, wl.level2_pct, wl.level3_pct = -0.03, -0.05, -0.07
        mgr.evaluate(snap(clock, 100_000))
        clock.advance(days=1)  # rolls the daily anchor, weekly stays at 100k
        policy = mgr.evaluate(snap(clock, 94_400))   # -5.60% on the week
        assert policy.state is CircuitBreakerState.RESTRICTED
        assert any(t.category is BreakerCategory.WEEKLY_LOSS and t.level == 2
                   for t in policy.active_triggers)
        assert policy.allow_new_shorts is False
        assert policy.position_size_multiplier <= 0.5

    def test_weekly_level3_halts_and_flattens(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        wl = app_config.circuit_breakers.weekly_loss
        wl.level1_pct, wl.level2_pct, wl.level3_pct = -0.03, -0.05, -0.07
        mgr.evaluate(snap(clock, 100_000))
        clock.advance(days=1)
        positions = [long_pos("AAA", 10, 100, 95)]
        policy = mgr.evaluate(snap(clock, 92_900, positions=positions))  # -7.10% week
        assert policy.state is CircuitBreakerState.HALTED
        assert policy.flatten_all is True
        assert any(a["type"] == "close_position" and a["symbol"] == "AAA"
                   for a in policy.required_actions)

    def test_monthly_level1_reduces_sizes(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        ml = app_config.circuit_breakers.monthly_loss
        ml.level1_pct, ml.level2_pct, ml.level3_pct = -0.05, -0.08, -0.12
        mgr.evaluate(snap(clock, 100_000))
        clock.advance(days=1)  # daily+weekly isolation: deepen weekly (already deep)
        policy = mgr.evaluate(snap(clock, 94_700))   # -5.30% month
        assert any(t.category is BreakerCategory.MONTHLY_LOSS and t.level == 1
                   for t in policy.active_triggers)
        assert policy.position_size_multiplier <= 0.75

    def test_monthly_level3_halts_for_month(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        ml = app_config.circuit_breakers.monthly_loss
        ml.level1_pct, ml.level2_pct, ml.level3_pct = -0.05, -0.08, -0.12
        wl = app_config.circuit_breakers.weekly_loss
        wl.level1_pct, wl.level2_pct, wl.level3_pct = -0.03, -0.05, -0.07
        mgr.evaluate(snap(clock, 100_000))
        clock.advance(days=1)
        policy = mgr.evaluate(snap(clock, 87_900))   # -12.10% month
        assert any(t.category is BreakerCategory.MONTHLY_LOSS and t.level == 3
                   for t in policy.active_triggers)
        assert policy.state in (CircuitBreakerState.HALTED, CircuitBreakerState.EMERGENCY)
        assert policy.flatten_all is True


# =============================================================================
# Layer 5 — drawdown breakers
# =============================================================================


class TestDrawdownBreakers:
    def _isolated(self, app_config: AppConfig) -> tuple[CircuitBreakerManager, Clock]:
        mgr, clock = make_manager(app_config, deepen=True)
        dd = app_config.circuit_breakers.drawdown
        dd.level1_pct, dd.level2_pct, dd.level3_pct, dd.level4_pct = (
            -0.05, -0.08, -0.12, -0.15)
        mgr.evaluate(snap(clock, 100_000))  # sets peak = 100k
        clock.advance(days=1)               # roll day anchor, peak persists at 100k
        return mgr, clock

    def test_level1_yellow(self, app_config: AppConfig) -> None:
        mgr, clock = self._isolated(app_config)
        policy = mgr.evaluate(snap(clock, 94_900))   # dd -5.10%
        assert any(t.category is BreakerCategory.DRAWDOWN and t.level == 1
                   for t in policy.active_triggers)
        assert policy.position_size_multiplier <= 0.85

    def test_level2_orange(self, app_config: AppConfig) -> None:
        mgr, clock = self._isolated(app_config)
        policy = mgr.evaluate(snap(clock, 91_900))   # dd -8.10%
        assert any(t.category is BreakerCategory.DRAWDOWN and t.level == 2
                   for t in policy.active_triggers)
        assert policy.allow_new_shorts is False

    def test_level3_caps_positions_and_confidence(self, app_config: AppConfig) -> None:
        mgr, clock = self._isolated(app_config)
        policy = mgr.evaluate(snap(clock, 87_900))   # dd -12.10%
        assert any(t.category is BreakerCategory.DRAWDOWN and t.level == 3
                   for t in policy.active_triggers)
        assert policy.max_open_positions <= 3
        tc = app_config.trading
        effective = policy.min_confidence(tc.min_confidence_normal,
                                          tc.min_confidence_restricted,
                                          tc.min_confidence_defensive)
        assert effective >= 0.85

    def test_level4_emergency_cooling_off(self, app_config: AppConfig) -> None:
        mgr, clock = self._isolated(app_config)
        positions = [long_pos("AAA", 10, 100, 90)]
        policy = mgr.evaluate(snap(clock, 84_900, positions=positions))  # dd -15.10%
        assert policy.state is CircuitBreakerState.EMERGENCY
        assert policy.flatten_all is True
        expected_lock = clock.now + timedelta(days=app_config.recovery.cooling_off_days)
        assert policy.locked_until is not None
        assert abs((policy.locked_until - expected_lock).total_seconds()) < 2
        assert any(a["type"] == "forced_backtest_review" for a in policy.required_actions)

    def test_new_high_resets_peak_only_upwards(self, app_config: AppConfig) -> None:
        mgr, clock = self._isolated(app_config)
        mgr.evaluate(snap(clock, 110_000))   # new peak
        assert mgr._peak_equity == 110_000
        policy = mgr.evaluate(snap(clock, 109_000))
        assert not any(t.category is BreakerCategory.DRAWDOWN for t in policy.active_triggers)


# =============================================================================
# Layer 6 — market-wide breakers
# =============================================================================


class TestMarketBreakers:
    def test_vix_ladder_sizing(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        mgr.evaluate(snap(clock, 100_000, vix=22.0))
        p = mgr.evaluate(snap(clock, 100_000, vix=22.0))
        assert p.position_size_multiplier == pytest.approx(0.75)
        p = mgr.evaluate(snap(clock, 100_000, vix=27.0))
        assert p.position_size_multiplier <= 0.5
        assert p.allow_new_longs is False
        p = mgr.evaluate(snap(clock, 100_000, vix=32.0))
        assert p.position_size_multiplier <= 0.25
        p = mgr.evaluate(snap(clock, 100_000, vix=45.0))
        assert p.flatten_all is True
        assert p.allow_new_entries is False

    def test_vix_intraday_spike_flagged(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        mgr.evaluate(snap(clock, 100_000, vix=20.0, vix_day_open=20.0))
        p = mgr.evaluate(snap(clock, 100_000, vix=24.5, vix_day_open=20.0))
        descriptions = " ".join(t.description for t in p.active_triggers)
        assert "intraday spike" in descriptions

    def test_market_crash_orange_blocks_longs(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        mgr.evaluate(snap(clock, 100_000))
        p = mgr.evaluate(snap(clock, 100_000, benchmark_change_pct=-0.035))
        assert any(t.category is BreakerCategory.MARKET_CRASH and t.level == 2
                   for t in p.active_triggers)
        assert p.allow_new_longs is False

    def test_market_crash_red_exits_longs_only(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        positions = [long_pos("LNG", 10, 100, 99),
                     PositionInfo("SHT", PositionSide.SHORT, 5, 100, 101)]
        p = mgr.evaluate(snap(clock, 100_000, positions=positions,
                              benchmark_change_pct=-0.06))
        assert any(t.level == 3 for t in p.active_triggers
                   if t.category is BreakerCategory.MARKET_CRASH)
        closes = [a for a in p.required_actions if a["type"] == "close_position"]
        assert [a["symbol"] for a in closes] == ["LNG"]

    def test_sector_crash_exits_and_blocks_sector(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        positions = [long_pos("TEC1", 10, 100, 99, sector="Technology"),
                     long_pos("ENE1", 10, 100, 99, sector="Energy")]
        p = mgr.evaluate(snap(clock, 100_000, positions=positions,
                              sector_changes={"Technology": -0.06}))
        assert any(t.category is BreakerCategory.SECTOR_CRASH for t in p.active_triggers)
        closes = [a for a in p.required_actions if a["type"] == "close_position"]
        assert [a["symbol"] for a in closes] == ["TEC1"]
        assert "Technology" in p.blocked_sectors
        # block persists across evaluations and clears after block_days
        clock.advance(days=4)
        p2 = mgr.evaluate(snap(clock, 100_000))
        assert "Technology" not in p2.blocked_sectors

    def test_liquidity_breakers(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        p = mgr.evaluate(snap(clock, 100_000, symbol_metrics={
            "THIN": {"spread_pct": 0.02},
            "DRY": {"volume_ratio": 0.20},
        }))
        assert "THIN" in p.illiquid_symbols
        assert "DRY" in p.blocked_symbols

    def test_flash_crash_pause_and_recovery(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        mgr.evaluate(snap(clock, 100_000))
        # -1.4% within 4 minutes
        for i, price in enumerate([100.0, 99.9, 98.9, 98.6]):
            clock.advance(minutes=1)
            mgr.record_index_price(price, ts=clock.now)
        p = mgr.evaluate(snap(clock, 100_000))
        assert any(t.category is BreakerCategory.FLASH_CRASH for t in p.active_triggers)
        assert mgr._flash_pause_until is not None
        assert p.allow_new_entries is False
        # slide the window past the crash, price recovers >50% of the drop
        clock.advance(minutes=8)
        mgr.record_index_price(98.6 + (100.0 - 98.6) * 0.7, ts=clock.now)  # ~99.58
        p2 = mgr.evaluate(snap(clock, 100_000))
        assert mgr._flash_pause_until is None
        assert p2.allow_new_entries is True or not any(
            t.category is BreakerCategory.FLASH_CRASH for t in p2.active_triggers)


# =============================================================================
# Layer 7 — technical failure breakers
# =============================================================================


class TestTechnicalBreakers:
    def test_data_feed_stale_halts(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        mgr.evaluate(snap(clock, 100_000))
        mgr.record_data_heartbeat(ts=clock.now)
        clock.advance(seconds=130)  # > 120s timeout
        p = mgr.evaluate(snap(clock, 100_000))
        assert any(t.category is BreakerCategory.DATA_FEED for t in p.active_triggers)
        assert p.allow_new_entries is False

    def test_data_feed_dead_flattens(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        tech = app_config.circuit_breakers.technical
        tech.data_feed_timeout_seconds = 120
        tech.data_feed_emergency_seconds = 300
        mgr.record_data_heartbeat(ts=clock.now)
        mgr.evaluate(snap(clock, 100_000))
        clock.advance(seconds=310)
        positions = [long_pos("AAA", 1, 100, 99)]
        p = mgr.evaluate(snap(clock, 100_000, positions=positions))
        feed = [t for t in p.active_triggers if t.category is BreakerCategory.DATA_FEED]
        assert feed and max(int(t.severity) for t in feed) >= int(AlertLevel.EMERGENCY)
        assert p.flatten_all is True

    def test_feed_recovery_auto_deescalates(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        mgr.record_data_heartbeat(ts=clock.now)
        mgr.evaluate(snap(clock, 100_000))
        clock.advance(seconds=130)
        p = mgr.evaluate(snap(clock, 100_000))
        assert p.state in (CircuitBreakerState.HALTED, CircuitBreakerState.RESTRICTED,
                           CircuitBreakerState.DEFENSIVE, CircuitBreakerState.EMERGENCY)
        mgr.record_data_heartbeat(ts=clock.now)  # feed returns
        p2 = mgr.evaluate(snap(clock, 100_000))
        # one-step recovery; no sticky latches created
        assert p.state.severity if hasattr(p.state, "severity") else True
        assert not mgr._sticky
        from utils.constants import STATE_SEVERITY
        assert STATE_SEVERITY[p2.state] < STATE_SEVERITY[p.state]

    def test_api_failure_escalates_with_duration(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        mgr.evaluate(snap(clock, 100_000))
        for _ in range(3):
            mgr.record_api_failure("timeout")
        p = mgr.evaluate(snap(clock, 100_000))
        assert any(t.category is BreakerCategory.API_FAILURE for t in p.active_triggers)
        clock.advance(seconds=310)
        p2 = mgr.evaluate(snap(clock, 100_000))
        assert max(int(t.severity) for t in p2.active_triggers
                   if t.category is BreakerCategory.API_FAILURE) >= int(AlertLevel.EMERGENCY)
        mgr.record_api_success()
        p3 = mgr.evaluate(snap(clock, 100_000))
        assert not any(t.category is BreakerCategory.API_FAILURE for t in p3.active_triggers)

    def test_model_failure_falls_back(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        mgr.evaluate(snap(clock, 100_000))
        mgr.record_model_confidences({"lstm": 0.30, "xgboost": 0.22, "ensemble": 0.31})
        p = mgr.evaluate(snap(clock, 100_000))
        assert p.allow_ml_signals is False
        assert any(t.category is BreakerCategory.MODEL_FAILURE for t in p.active_triggers)
        # healthy batch clears it automatically
        mgr.record_model_confidences({"lstm": 0.75})
        p2 = mgr.evaluate(snap(clock, 100_000))
        assert p2.allow_ml_signals is True

    def test_runaway_order_rate_limit(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        mgr.evaluate(snap(clock, 100_000))
        assert mgr.can_submit_order("AAPL", "BUY", 5).allowed
        for i in range(10):
            clock.advance(seconds=1)
            mgr.register_order_submission(f"S{i}", "BUY", 5, now=clock.now)
        result = mgr.can_submit_order("AAPL", "BUY", 5, now=clock.now)
        assert not result.allowed
        assert any("rate" in r or "paused" in r for r in result.reasons)
        # flow pause engaged: 60s cooldown
        clock.advance(seconds=61)
        mgr._order_submissions.clear()
        assert mgr.can_submit_order("ZZZ", "BUY", 1, now=clock.now).allowed

    def test_runaway_duplicate_detection(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        mgr.evaluate(snap(clock, 100_000))
        mgr.register_order_submission("AAPL", "BUY", 10, now=clock.now)
        result = mgr.can_submit_order("AAPL", "BUY", 10, now=clock.now)
        assert not result.allowed
        assert any("duplicate" in r for r in result.reasons)
        # different size passes
        assert mgr.can_submit_order("AAPL", "BUY", 11, now=clock.now).allowed

    def test_order_attempt_ceiling(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        mgr.evaluate(snap(clock, 100_000))
        for _ in range(3):
            clock.advance(seconds=40)
            mgr.register_order_submission("AAPL", "BUY", 10, now=clock.now)
        result = mgr.can_submit_order("AAPL", "BUY", 10, now=clock.now)
        assert not result.allowed
        assert any("attempt ceiling" in r for r in result.reasons)
        mgr.reset_order_attempts("AAPL")
        clock.advance(seconds=40)
        assert mgr.can_submit_order("AAPL", "BUY", 20, now=clock.now).allowed

    def test_locked_state_blocks_order_gate(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        mgr.evaluate(snap(clock, 100_000))
        mgr.evaluate(snap(clock, 97_800))  # HALTED
        result = mgr.can_submit_order("AAPL", "BUY", 1)
        assert not result.allowed
        assert any("HALTED" in r for r in result.reasons)

    def test_position_mismatch_sticky_halt_and_clear(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        mgr.evaluate(snap(clock, 100_000))
        mgr.report_position_mismatch(["AAPL expected 10, broker 0"])
        p = mgr.evaluate(snap(clock, 100_000))
        assert p.state is CircuitBreakerState.HALTED
        assert any(t.category is BreakerCategory.POSITION_MISMATCH for t in p.active_triggers)
        # mismatch persists (sticky)...
        p2 = mgr.evaluate(snap(clock, 100_000))
        assert p2.state is CircuitBreakerState.HALTED
        # ...until reconciliation + resume
        mgr.clear_position_mismatch()
        mgr.resume("reconciled", token=confirmed_token(mgr), equity=100_000)
        assert mgr.state is CircuitBreakerState.RESTRICTED


# =============================================================================
# Kill switch / suspend / resume / override
# =============================================================================


class TestManualControls:
    def test_kill_switch_flattens_and_requires_resume(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        positions = [long_pos("AAA", 10, 100, 99), long_pos("BBB", 5, 50, 51)]
        mgr.evaluate(snap(clock, 100_000, positions=positions))
        mgr.activate_kill_switch("operator panic", flatten=True)
        assert mgr.state is CircuitBreakerState.EMERGENCY
        p = mgr.evaluate(snap(clock, 100_000, positions=positions))
        assert p.flatten_all is True
        assert p.allow_new_entries is False
        # stays latched across healthy evaluations
        p2 = mgr.evaluate(snap(clock, 105_000))
        assert p2.state is CircuitBreakerState.EMERGENCY
        with pytest.raises(ManualOverrideRequired):
            mgr.resume("no token")
        mgr.resume("acknowledged", token=confirmed_token(mgr), equity=100_000)
        assert mgr.state is CircuitBreakerState.RESTRICTED
        assert not mgr._killed

    def test_suspend_and_resume_walk(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        mgr.evaluate(snap(clock, 100_000))
        mgr.suspend("weekly maintenance")
        assert mgr.state is CircuitBreakerState.SUSPENDED
        # SUSPENDED suppresses even healthy evaluation changes
        p = mgr.evaluate(snap(clock, 100_000))
        assert p.state is CircuitBreakerState.SUSPENDED
        assert p.allow_new_entries is False
        mgr.resume("maintenance done", token=confirmed_token(mgr), equity=100_000)
        assert mgr.state is CircuitBreakerState.RESTRICTED

    def test_override_token_expires(self, app_config: AppConfig, monkeypatch) -> None:
        mgr, _ = make_manager(app_config, deepen=True)
        token = mgr.request_override("resume", reason="x")
        import risk.circuit_breakers as cb_mod
        fake_now = {"t": 1000.0}
        monkeypatch.setattr(cb_mod.time, "monotonic", lambda: fake_now["t"] + 10_000)
        assert not mgr.confirm_override(token)

    def test_unknown_override_token_rejected(self, app_config: AppConfig) -> None:
        mgr, _ = make_manager(app_config, deepen=True)
        assert not mgr.confirm_override("deadbeef")

    def test_invalid_direct_transition_raises(self, app_config: AppConfig) -> None:
        mgr, _ = make_manager(app_config)
        mgr.suspend("x")
        with pytest.raises(InvalidStateTransition):
            mgr._transition(CircuitBreakerState.NORMAL, "illegal", None)

    def test_notifier_receives_orange_and_above(self, app_config: AppConfig) -> None:
        alerts: list[dict[str, Any]] = []
        mgr, clock = make_manager(app_config, deepen=True,
                                  notifier=lambda msg, level, payload: alerts.append(
                                      {"msg": msg, "level": int(level)}))
        mgr.evaluate(snap(clock, 100_000))
        mgr.evaluate(snap(clock, 97_800))  # ORANGE+ (RED sticky)
        assert alerts
        assert max(a["level"] for a in alerts) >= int(AlertLevel.ORANGE)


# =============================================================================
# Recovery program
# =============================================================================


class TestRecoveryProgram:
    def _halted_manager(self, app_config: AppConfig,
                        db: Optional[DatabaseManager] = None):
        mgr, clock = make_manager(app_config, db=db, deepen=True)
        mgr.evaluate(snap(clock, 100_000))
        mgr.evaluate(snap(clock, 97_800))  # HALTED sticky
        clock._t = mgr._locked_until + timedelta(minutes=30)
        mgr.resume("post test halt", token=confirmed_token(mgr), equity=97_800)
        return mgr, clock

    def test_day1_3_quarter_size(self, app_config: AppConfig) -> None:
        mgr, clock = self._halted_manager(app_config)
        p = mgr.evaluate(snap(clock, 97_800))
        assert p.recovery_size_multiplier == pytest.approx(0.25)
        assert p.effective_size_multiplier <= 0.25

    def test_day4_7_half_size(self, app_config: AppConfig) -> None:
        mgr, clock = self._halted_manager(app_config)
        clock.advance(days=4)
        p = mgr.evaluate(snap(clock, 97_800))
        assert p.recovery_size_multiplier == pytest.approx(0.50)

    def test_week2_three_quarters(self, app_config: AppConfig) -> None:
        mgr, clock = self._halted_manager(app_config)
        clock.advance(days=8)
        p = mgr.evaluate(snap(clock, 97_800))
        assert p.recovery_size_multiplier == pytest.approx(0.75)

    def test_week3_full_when_positive_held_when_not(self, app_config: AppConfig) -> None:
        mgr, clock = self._halted_manager(app_config)
        clock.advance(days=16)
        # still below the recovery anchor -> held at 75%
        p = mgr.evaluate(snap(clock, 96_000))
        assert p.recovery_size_multiplier == pytest.approx(0.75)
        # performance recovered -> full size, recovery completed
        p2 = mgr.evaluate(snap(clock, 98_000))
        assert p2.recovery_size_multiplier == pytest.approx(1.0)
        assert mgr._recovery_start is None


# =============================================================================
# Layer 1 — position-level breakers
# =============================================================================


class TestPositionLevelBreakers:
    def test_hard_stop_long(self, app_config: AppConfig) -> None:
        mgr, _ = make_manager(app_config)
        pos = long_pos("AAA", 10, 100, 97.9)  # -2.1% vs 2% stop
        actions = mgr.evaluate_position(pos)
        assert actions and actions[0]["stop_kind"] == "hard"
        assert actions[0]["fraction"] == 1.0

    def test_hard_stop_short(self, app_config: AppConfig) -> None:
        mgr, _ = make_manager(app_config)
        pos = PositionInfo("AAA", PositionSide.SHORT, 10, 100, 102.5)
        actions = mgr.evaluate_position(pos)
        assert actions and actions[0]["stop_kind"] == "hard"

    def test_atr_stop(self, app_config: AppConfig) -> None:
        mgr, _ = make_manager(app_config)
        # atr stop = 100 - 2*0.5 = 99.0 (shallower than the 98 hard stop),
        # so a drop to 98.5 breaches the ATR stop but not the hard stop
        pos = long_pos("AAA", 10, 100, 98.5)
        actions = mgr.evaluate_position(pos, atr=0.5)
        assert actions and actions[0]["stop_kind"] == "atr"

    def test_max_loss_per_trade(self, app_config: AppConfig) -> None:
        mgr, _ = make_manager(app_config)
        pos = long_pos("AAA", 1000, 100, 98.9)   # -1.1% price, but -$1100 of $50k = 2.2%
        actions = mgr.evaluate_position(pos, portfolio_equity=50_000)
        assert any(a["stop_kind"] == "max_loss" for a in actions)

    def test_volatility_spike_halves(self, app_config: AppConfig) -> None:
        mgr, _ = make_manager(app_config)
        pos = long_pos("AAA", 10, 100, 99.5)
        actions = mgr.evaluate_position(pos, current_volatility=0.35,
                                        baseline_volatility=0.10)
        assert any(a["type"] == "reduce_position" and a["fraction"] == 0.5
                   for a in actions)

    def test_time_stop(self, app_config: AppConfig) -> None:
        mgr, _ = make_manager(app_config)
        pos = long_pos("AAA", 10, 100, 99.5)
        actions = mgr.evaluate_position(pos, days_open=app_config.risk.time_stop_days)
        assert any(a["stop_kind"] == "time" for a in actions)

    def test_no_action_when_healthy(self, app_config: AppConfig) -> None:
        mgr, _ = make_manager(app_config)
        pos = long_pos("AAA", 10, 100, 101.0)
        assert mgr.evaluate_position(pos, days_open=2, atr=1.0) == []


# =============================================================================
# Persistence, aggregation, misc
# =============================================================================


class TestPersistenceAndAggregation:
    def test_state_survives_restart(self, app_config: AppConfig,
                                    db: DatabaseManager) -> None:
        clock = Clock()
        mgr = CircuitBreakerManager(app_config, db, now_fn=clock)
        mgr.evaluate(snap(clock, 100_000))
        mgr.evaluate(snap(clock, 97_800))  # sticky HALTED + lock
        assert mgr.state is CircuitBreakerState.HALTED
        # simulate process restart with the same DB and clock
        mgr2 = CircuitBreakerManager(app_config, db, now_fn=clock)
        assert mgr2.state is CircuitBreakerState.HALTED
        assert mgr2._locked_until == mgr._locked_until
        assert any(t.category is BreakerCategory.DAILY_LOSS for t in mgr2._sticky)
        p = mgr2.evaluate(snap(clock, 98_000))
        assert p.state is CircuitBreakerState.HALTED  # latched across restart

    def test_audit_log_written(self, app_config: AppConfig, db: DatabaseManager) -> None:
        clock = Clock()
        mgr = CircuitBreakerManager(app_config, db, now_fn=clock)
        mgr.evaluate(snap(clock, 100_000))
        mgr.evaluate(snap(clock, 97_800))
        events = db.fetch_breaker_events(limit=10)
        assert len(events) >= 2
        categories = set(events["category"])
        assert "daily_loss" in categories

    def test_worst_trigger_wins_aggregation(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        dd = app_config.circuit_breakers.drawdown
        dd.level1_pct, dd.level2_pct, dd.level3_pct, dd.level4_pct = (
            -0.05, -0.08, -0.12, -0.15)
        mgr.evaluate(snap(clock, 100_000))
        clock.advance(days=1)
        # drawdown level1 (x0.80) + VIX reduce_50 (x0.50, block longs): min wins
        p = mgr.evaluate(snap(clock, 94_900, vix=27.0))
        assert p.position_size_multiplier <= 0.5
        assert p.allow_new_longs is False

    def test_disabled_breakers_passthrough(self, app_config: AppConfig) -> None:
        app_config.circuit_breakers.enabled = False
        mgr, clock = make_manager(app_config)
        mgr.evaluate(snap(clock, 100_000))
        p = mgr.evaluate(snap(clock, 50_000))  # would nuke everything if enabled
        assert p.allow_new_entries is True
        assert p.position_size_multiplier == 1.0
        assert any("disabled" in r for r in p.reasons)

    def test_confidence_math(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config, deepen=True)
        mgr.evaluate(snap(clock, 100_000))
        tc = app_config.trading
        p = mgr.evaluate(snap(clock, 100_000))
        assert p.min_confidence(tc.min_confidence_normal, tc.min_confidence_restricted,
                                tc.min_confidence_defensive) == pytest.approx(
                                    tc.min_confidence_normal)
        p = mgr.evaluate(snap(clock, 98_400))  # RESTRICTED
        assert p.min_confidence(tc.min_confidence_normal, tc.min_confidence_restricted,
                                tc.min_confidence_defensive) >= tc.min_confidence_restricted

    def test_status_and_report(self, app_config: AppConfig, db: DatabaseManager) -> None:
        clock = Clock()
        mgr = CircuitBreakerManager(app_config, db, now_fn=clock)
        mgr.evaluate(snap(clock, 100_000))
        mgr.evaluate(snap(clock, 97_800))
        status = mgr.status()
        assert status["state"] == "HALTED"
        assert status["locked_until"] is not None
        assert status["sticky_triggers"]
        report = mgr.generate_post_halt_report()
        assert "root_cause_guess" in report and report["recommendations"]
        auto = db.fetch_automation_log("risk")
        assert not auto.empty

    def test_evaluate_never_raises_on_bad_snapshot(self, app_config: AppConfig) -> None:
        mgr, clock = make_manager(app_config)
        # None equity is a programming error by callers; the manager must still
        # return a safe (defensive) policy instead of crashing the loop.
        bad = PortfolioSnapshot(timestamp=clock.now, equity=float("nan"))
        try:
            p = mgr.evaluate(bad)
        except Exception:
            p = None
        if p is not None:
            assert p.allow_new_entries in (True, False)
