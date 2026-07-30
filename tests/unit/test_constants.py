"""Tests for utils/constants.py — enums and machine tables."""

from __future__ import annotations

from utils.constants import (
    SIGNAL_THRESHOLD_SETS,
    STATE_POLICY_DEFAULTS,
    STATE_SEVERITY,
    VALID_STATE_TRANSITIONS,
    AlertLevel,
    CircuitBreakerState,
    OrderStatus,
    SignalType,
    Timeframe,
    TradingMode,
)


class TestEnums:
    def test_trading_mode_values(self) -> None:
        assert TradingMode.PAPER.value == "paper"
        assert TradingMode.LIVE.value == "live"
        assert TradingMode.SHADOW.value == "shadow"

    def test_circuit_breaker_states_complete(self) -> None:
        expected = {"NORMAL", "CAUTION", "RESTRICTED", "DEFENSIVE", "HALTED",
                    "EMERGENCY", "SUSPENDED"}
        assert {s.value for s in CircuitBreakerState} == expected

    def test_state_severity_is_strictly_ordered(self) -> None:
        ordered = [CircuitBreakerState.NORMAL, CircuitBreakerState.CAUTION,
                   CircuitBreakerState.RESTRICTED, CircuitBreakerState.DEFENSIVE,
                   CircuitBreakerState.HALTED, CircuitBreakerState.EMERGENCY,
                   CircuitBreakerState.SUSPENDED]
        severities = [STATE_SEVERITY[s] for s in ordered]
        assert severities == sorted(severities)
        assert len(set(severities)) == len(severities)

    def test_order_status_open_terminal_partition(self) -> None:
        for status in OrderStatus:
            assert status.is_open != status.is_terminal

    def test_signal_type_actionable(self) -> None:
        assert SignalType.STRONG_BUY.is_actionable
        assert SignalType.BUY.is_actionable
        assert SignalType.SELL.is_actionable
        assert not SignalType.HOLD.is_actionable


class TestStateMachineTables:
    def test_every_state_has_transitions_entry(self) -> None:
        assert set(VALID_STATE_TRANSITIONS) == set(CircuitBreakerState)

    def test_every_state_has_policy_defaults(self) -> None:
        assert set(STATE_POLICY_DEFAULTS) == set(CircuitBreakerState)
        for policy in STATE_POLICY_DEFAULTS.values():
            assert 0.0 <= float(policy["position_size_multiplier"]) <= 1.0

    def test_escalation_always_possible_from_normal(self) -> None:
        for state in CircuitBreakerState:
            if state is CircuitBreakerState.NORMAL:
                continue
            assert state in VALID_STATE_TRANSITIONS[CircuitBreakerState.NORMAL]

    def test_suspended_only_returns_to_halted(self) -> None:
        assert VALID_STATE_TRANSITIONS[CircuitBreakerState.SUSPENDED] == {
            CircuitBreakerState.HALTED}

    def test_no_self_transitions(self) -> None:
        for state, targets in VALID_STATE_TRANSITIONS.items():
            assert state not in targets

    def test_halted_cannot_jump_to_normal(self) -> None:
        assert CircuitBreakerState.NORMAL not in VALID_STATE_TRANSITIONS[
            CircuitBreakerState.HALTED]

    def test_halted_states_block_entries_in_policy(self) -> None:
        for state in (CircuitBreakerState.HALTED, CircuitBreakerState.EMERGENCY,
                      CircuitBreakerState.SUSPENDED):
            assert STATE_POLICY_DEFAULTS[state]["allow_new_entries"] is False
            assert STATE_POLICY_DEFAULTS[state]["position_size_multiplier"] == 0.0

    def test_normal_state_full_permissions(self) -> None:
        policy = STATE_POLICY_DEFAULTS[CircuitBreakerState.NORMAL]
        assert policy["allow_new_entries"] is True
        assert policy["position_size_multiplier"] == 1.0


class TestSignalThresholds:
    def test_threshold_sets_are_ordered(self) -> None:
        for name, thresholds in SIGNAL_THRESHOLD_SETS.items():
            assert thresholds["strong_sell"] <= thresholds["weak_sell"]
            assert thresholds["weak_sell"] < 0 < thresholds["weak_buy"]
            assert thresholds["weak_buy"] <= thresholds["strong_buy"], name

    def test_restricted_is_stricter_than_normal(self) -> None:
        normal = SIGNAL_THRESHOLD_SETS["NORMAL"]
        restricted = SIGNAL_THRESHOLD_SETS["RESTRICTED"]
        assert restricted["strong_buy"] > normal["strong_buy"]
        assert restricted["strong_sell"] < normal["strong_sell"]

    def test_defensive_blocks_buys(self) -> None:
        defensive = SIGNAL_THRESHOLD_SETS["DEFENSIVE"]
        assert defensive["strong_buy"] > 1.0  # unreachable -> never buys

    def test_timeframe_metadata(self) -> None:
        assert Timeframe.M1.seconds == 60
        assert Timeframe.H1.yf_interval == "60m"
        assert Timeframe.W1.yf_interval == "1wk"
        assert Timeframe.H4.pandas_freq == "4h"
        assert Timeframe.D1.value == "1d"


class TestAlertLevels:
    def test_ordering(self) -> None:
        assert AlertLevel.NONE < AlertLevel.INFO < AlertLevel.YELLOW \
            < AlertLevel.ORANGE < AlertLevel.RED < AlertLevel.EMERGENCY
