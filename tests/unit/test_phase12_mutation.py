"""Phase 12 mutation spot-checks on THREE safety thresholds.

For each threshold (daily-loss ladder, VIX ladder, rate cap):
- copy config,
- flip threshold to a weakened value,
- prove targeted safety test would FAIL (i.e., breaker no longer triggers),
- revert (original config untouched).

These tests themselves PASS, but they prove that weakening the threshold
breaks safety — the targeted tests would FAIL under the mutated config.

Deterministic; zero network.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import pytest

from risk.circuit_breakers import CircuitBreakerManager, PortfolioSnapshot, PositionInfo
from utils.constants import PositionSide
from trading.paper_broker import PaperBroker
from trading.order_types import Order, OrderState

UTC = timezone.utc


class Clock:
    def __init__(self, t):
        self._t = t

    def __call__(self):
        return self._t

    def advance(self, **kw):
        from datetime import timedelta
        self._t = self._t + timedelta(**kw)
        return self._t

    @property
    def now(self):
        return self._t

    def ts(self):
        return self._t.timestamp()


def test_mutation_daily_loss_ladder_weakened_breaks_halt(app_config):
    """Mutate daily-loss ladder: set level3 to -10% (weakened), prove -2.2% no longer HALTS."""
    # Original behavior: -2.2% daily loss triggers HALT
    clock = Clock(datetime(2024, 4, 1, 13, 30, tzinfo=UTC))
    mgr_orig = CircuitBreakerManager(config=app_config, now_fn=clock)
    mgr_orig.evaluate(PortfolioSnapshot(timestamp=clock.now, equity=100_000.0, cash=100_000.0))
    policy_orig = mgr_orig.evaluate(PortfolioSnapshot(timestamp=clock.now, equity=97_800.0, cash=100_000.0))
    assert policy_orig.state.value == "HALTED", "original should HALT at -2.2%"

    # Mutated config: weaken daily_loss ladder to -10% levels
    mutated = copy.deepcopy(app_config)
    mutated.circuit_breakers.daily_loss.level1_pct = -0.10
    mutated.circuit_breakers.daily_loss.level2_pct = -0.11
    mutated.circuit_breakers.daily_loss.level3_pct = -0.12
    mutated.circuit_breakers.daily_loss.level4_pct = -0.13

    mgr_mut = CircuitBreakerManager(config=mutated, now_fn=clock)
    mgr_mut.evaluate(PortfolioSnapshot(timestamp=clock.now, equity=100_000.0, cash=100_000.0))
    policy_mut = mgr_mut.evaluate(PortfolioSnapshot(timestamp=clock.now, equity=97_800.0, cash=100_000.0))
    # With weakened thresholds, -2.2% should NOT HALT — this is the FAIL that proves mutation breaks safety
    assert policy_mut.state.value != "HALTED", "mutated weakened ladder should NOT HALT (proves targeted test would FAIL)"
    # Targeted original test: test_level3_red_halts_closes_worst_half_and_locks would FAIL under mutated config
    # because it expects HALT at -2.2% but mutated gives NORMAL


def test_mutation_vix_ladder_weakened_breaks_size_reduction(app_config):
    """Mutate VIX ladder: set thresholds high (50/60/70/80), prove VIX 27 no longer reduces."""
    clock = Clock(datetime(2024, 4, 1, 13, 30, tzinfo=UTC))
    mgr_orig = CircuitBreakerManager(config=app_config, now_fn=clock)
    mgr_orig.evaluate(PortfolioSnapshot(timestamp=clock.now, equity=100_000.0, cash=100_000.0, vix=20.0))
    policy_orig = mgr_orig.evaluate(PortfolioSnapshot(timestamp=clock.now, equity=100_000.0, cash=100_000.0, vix=27.0))
    assert policy_orig.position_size_multiplier <= 0.5, "original VIX 27 should reduce size to <=0.5"

    mutated = copy.deepcopy(app_config)
    mutated.circuit_breakers.vix.reduce_25 = 50.0
    mutated.circuit_breakers.vix.reduce_50 = 60.0
    mutated.circuit_breakers.vix.reduce_75 = 70.0
    mutated.circuit_breakers.vix.exit_all = 80.0

    mgr_mut = CircuitBreakerManager(config=mutated, now_fn=clock)
    mgr_mut.evaluate(PortfolioSnapshot(timestamp=clock.now, equity=100_000.0, cash=100_000.0, vix=20.0))
    policy_mut = mgr_mut.evaluate(PortfolioSnapshot(timestamp=clock.now, equity=100_000.0, cash=100_000.0, vix=27.0))
    # Weakened ladder: VIX 27 should NOT reduce
    assert policy_mut.position_size_multiplier == pytest.approx(1.0), "mutated weakened VIX ladder should NOT reduce"
    # Original test test_vix_ladder_sizing would FAIL under mutated config


def test_mutation_rate_cap_weakened_breaks_burst_rejection(app_config):
    """Mutate rate cap: set max_orders_per_minute to 100, prove 15 burst now passes."""
    # Original cap 10
    assert app_config.circuit_breakers.technical.max_orders_per_minute == 10

    clock = Clock(datetime(2024, 4, 1, 13, 30, tzinfo=UTC))
    mgr_orig = CircuitBreakerManager(config=app_config, now_fn=clock)
    # Simulate 15 orders quickly
    for i in range(10):
        mgr_orig.register_order_submission(f"S{i}", "BUY", 1, now=clock.now)
    result_orig = mgr_orig.can_submit_order("AAPL", "BUY", 1, now=clock.now)
    assert result_orig.allowed is False, "original cap should deny 11th order"

    mutated = copy.deepcopy(app_config)
    mutated.circuit_breakers.technical.max_orders_per_minute = 100

    mgr_mut = CircuitBreakerManager(config=mutated, now_fn=clock)
    for i in range(15):
        mgr_mut.register_order_submission(f"S{i}", "BUY", 1, now=clock.now)
    result_mut = mgr_mut.can_submit_order("AAPL", "BUY", 1, now=clock.now)
    assert result_mut.allowed is True, "mutated weakened cap should allow burst (proves original test would FAIL)"

    # Also PaperBroker level — isolate rate cap from portfolio open-positions cap by passing empty snapshot
    from risk.position_limits import PortfolioSnapshot as GatewaySnapshot
    snap_empty = GatewaySnapshot(equity=100_000.0, cash=100_000.0, positions=[], breaker_state="NORMAL")
    broker_orig = PaperBroker(config=app_config, clock=lambda: clock.ts(), fee_bps=0.0, slippage_bps=0.0)
    for i in range(10):
        broker_orig.place_order(Order(f"A{i}", "buy", 1, price=100.0, client_id=f"orig-{i}"), portfolio=snap_empty)
    eleventh = broker_orig.place_order(Order("K", "buy", 1, price=100.0, client_id="orig-10"), portfolio=snap_empty)
    assert eleventh.state == OrderState.REJECTED

    broker_mut = PaperBroker(config=mutated, clock=lambda: clock.ts(), fee_bps=0.0, slippage_bps=0.0)
    for i in range(10):
        broker_mut.place_order(Order(f"A{i}", "buy", 1, price=100.0, client_id=f"mut-{i}"), portfolio=snap_empty)
    eleventh_mut = broker_mut.place_order(Order("K", "buy", 1, price=100.0, client_id="mut-10"), portfolio=snap_empty)
    assert eleventh_mut.state == OrderState.FILLED, "weakened cap should allow 11th"
