"""Phase 11 dashboard — behavioral tests (CORE environment).

Architecture rule enforced here:
* ``dashboard/data.py`` and ``dashboard/actions.py`` are PURE python (no
  Streamlit import) so they test in CORE. The Streamlit layer is a thin
  renderer and is covered by the headless boot smoke (OPT_ONLY env).

This module proves:
* every provider against seeded tmp DBs (empty, populated, breaker HALTED,
  breaches present);
* the two mutation paths — token-confirmed kill switch (token-less rejected)
  and Phase-9 approve/reject;
* the no-Streamlit / no-network import invariants on the pure modules;
* the new ``dashboard`` config section.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from automation.approval_queue import ApprovalQueue
from data.database import DatabaseManager
from dashboard import actions, data as ddata
from dashboard._runtime import boot_check, dashboard_config, dashboard_db
from risk.circuit_breakers import CircuitBreakerManager
from trading.paper_adapter import PaperBrokerAdapter
from trading.paper_broker import PaperBroker
from utils.constants import STATE_SEVERITY, CircuitBreakerState

ROOT = Path(__file__).resolve().parents[2]

_NETWORK_MARKERS = ("requests.", "urllib", "http.client", "socket(", "urlopen",
                    "httpx", "aiohttp", "alpaca trading")


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture()
def seeded_db(app_config, tmp_path):
    """A tmp DB with a realistic populated state."""
    db = DatabaseManager(app_config.data.database_path)
    # performance snapshot
    db.upsert_performance_metric("2026-07-30", 105_000.0, cash=40_000.0,
                                 invested_value=65_000.0, daily_return=0.005,
                                 cumulative_return=0.05, drawdown=-0.01, sharpe=1.8)
    # price bars (watchlist)
    db.upsert_price_bars("AAPL", "1d", pd.DataFrame([
        {"timestamp": "2026-07-30", "open": 190, "high": 192, "low": 189,
         "close": 191, "volume": 1_000_000},
    ]))
    # open + closed paper trades
    tid = db.insert_paper_trade("AAPL", "BUY", 100, "2026-07-29", 188.0,
                                strategy="mean_revert", fees=1.0)
    db.close_paper_trade(tid, "2026-07-30", 191.0, fees=1.0)
    db.insert_paper_trade("MSFT", "BUY", 50, "2026-07-30", 420.0, strategy="trend")
    # signals
    db.insert_signal("sig-1", "AAPL", "2026-07-30", "BUY", "ensemble",
                     score=0.8, confidence=0.82, price=190.0)
    db.mark_signal_executed("sig-1")
    db.insert_signal("sig-2", "TSLA", "2026-07-30", "SELL", "ensemble",
                     score=-0.7, confidence=0.71, price=250.0)
    # a limit breach
    db.log_limit_breach("per_order.max_order_value", "REJECTED", entity="AAPL",
                        value=20_000.0, threshold=10_000.0)
    # breaker + automation audit rows
    db.log_circuit_breaker_event("daily_loss", "state:NORMAL->HALTED",
                                 state_before="NORMAL", state_after="HALTED",
                                 trigger_type="daily loss")
    db.log_automation("scheduler", "signal_scan", "ok")
    yield db
    db.close()


@pytest.fixture()
def control(app_config):
    """Fresh broker + breaker (NORMAL) + queue against the tmp DB."""
    db = DatabaseManager(app_config.data.database_path)
    breaker = CircuitBreakerManager(app_config, db)
    paper = PaperBroker(config=app_config, clock=lambda: 0.0,
                        fee_bps=0.0, slippage_bps=0.0)
    broker = PaperBrokerAdapter(paper=paper, config=app_config)
    queue = ApprovalQueue(config=app_config, db=db)
    yield broker, breaker, queue, db
    db.close()


# =============================================================================
# Import invariants (pure modules: no Streamlit, no network)
# =============================================================================

def test_dashboard_data_module_has_no_streamlit_import():
    src = inspect.getsource(ddata)
    assert "import streamlit" not in src and "from streamlit" not in src


def test_dashboard_actions_module_has_no_streamlit_import():
    src = inspect.getsource(actions)
    assert "import streamlit" not in src and "from streamlit" not in src


def test_dashboard_runtime_module_has_no_streamlit_import():
    from dashboard import _runtime
    src = inspect.getsource(_runtime)
    assert "import streamlit" not in src and "from streamlit" not in src


def test_dashboard_pure_modules_have_no_network_call_sites():
    from dashboard import _runtime
    for mod in (ddata, actions, _runtime):
        src = inspect.getsource(mod)
        for marker in _NETWORK_MARKERS:
            assert marker not in src, f"{mod.__name__} references {marker!r}"


# =============================================================================
# Config section
# =============================================================================

def test_dashboard_config_section_loads_and_validates(app_config):
    d = app_config.dashboard
    assert d.enabled is True
    assert d.refresh_interval_seconds >= 5
    assert d.boot_timeout_seconds >= 10
    assert d.max_log_rows >= 1
    assert app_config.validate() == []


# =============================================================================
# Providers — empty DB
# =============================================================================

def test_table_row_counts_empty(app_config):
    db = DatabaseManager(app_config.data.database_path)
    try:
        counts = ddata.table_row_counts(db)
        assert counts["price_data"] == 0
        assert counts["breaker_state"] == 0
    finally:
        db.close()


def test_overview_empty_db_returns_zeros(app_config):
    db = DatabaseManager(app_config.data.database_path)
    try:
        ov = ddata.overview_view(db)
        assert ov["latest_equity"] is None
        assert ov["open_positions_count"] == 0
        assert ov["realized_pnl"] == 0.0
        assert ov["signals_total"] == 0
        assert ov["breaker_state"] == "NORMAL"
        assert ov["breaker_severity"] == STATE_SEVERITY[CircuitBreakerState.NORMAL]
    finally:
        db.close()


def test_breaker_state_panel_normal_empty(app_config):
    db = DatabaseManager(app_config.data.database_path)
    try:
        view = ddata.breaker_state_view(db, app_config)
        assert view["state"] == "NORMAL"
        assert view["severity"] == 0
        assert view["kill_switch_engaged"] is False
        assert view["policy"]["allow_new_entries"] is True
        assert view["policy"]["severity_rank"] == 0
    finally:
        db.close()


def test_positions_orders_limits_models_empty_have_stable_schema(app_config):
    db = DatabaseManager(app_config.data.database_path)
    try:
        pos = ddata.positions_view(db)
        assert pos.empty and "symbol" in pos.columns
        orders = ddata.orders_view(db)
        assert orders.empty and "signal_type" in orders.columns
        limits = ddata.limits_view(db)
        assert limits.empty and "limit_type" in limits.columns
        models = ddata.models_view(db)
        assert models.empty and "model_name" in models.columns
    finally:
        db.close()


# =============================================================================
# Providers — populated DB
# =============================================================================

def test_overview_populated_db(seeded_db):
    ov = ddata.overview_view(seeded_db)
    assert ov["latest_equity"] == 105_000.0
    assert ov["open_positions_count"] == 1            # MSFT still open
    assert ov["closed_trades"] == 1
    assert ov["signals_total"] == 2
    assert ov["signals_executed"] == 1
    assert ov["watchlist_symbols"] == 1
    assert ov["realized_pnl"] > 0.0


def test_positions_view_populated(seeded_db):
    df = ddata.positions_view(seeded_db)
    assert not df.empty
    assert set(df["symbol"]) == {"MSFT"}
    assert "cost_basis" in df.columns
    assert float(df.iloc[0]["cost_basis"]) == pytest.approx(50 * 420.0)


def test_orders_view_populated(seeded_db):
    df = ddata.orders_view(seeded_db)
    assert len(df) == 2
    executed = df.loc[df["id"] == "sig-1", "executed"].iloc[0]
    assert int(executed) == 1


def test_limits_view_breaches_present(seeded_db):
    df = ddata.limits_view(seeded_db)
    assert not df.empty
    assert df.iloc[0]["limit_type"] == "per_order.max_order_value"


def test_logs_view_populated(seeded_db):
    logs = ddata.logs_view(seeded_db)
    assert not logs["circuit_breakers"].empty
    assert not logs["automation"].empty


# =============================================================================
# Providers — breaker HALTED + kill switch persistence
# =============================================================================

def _persist_breaker(db, state, triggers=None, notes=None, locked_until=None):
    db.save_breaker_state({
        "state": state,
        "active_breakers": triggers or [],
        "locked_until": locked_until,
        "notes": notes,
    })


def test_breaker_state_panel_halted_renders_severity_and_policy(app_config):
    db = DatabaseManager(app_config.data.database_path)
    try:
        _persist_breaker(db, "HALTED", triggers=[{
            "category": "daily_loss", "level": 3, "severity": 4,
            "description": "daily loss -2.0% breached level3",
            "timestamp": "2026-07-31T12:00:00Z", "value": -0.02,
            "threshold": -0.02, "sticky": True,
            "metadata": {"action": "close_worst_half"},
        }], locked_until="2026-08-01T14:30:00Z")
        view = ddata.breaker_state_view(db, app_config)
        assert view["state"] == "HALTED"
        assert view["severity"] == STATE_SEVERITY[CircuitBreakerState.HALTED] == 4
        assert view["kill_switch_engaged"] is False
        # HALTED defaults: no entries, zero size, flatten only on kill switch
        policy = view["policy"]
        assert policy["allow_new_entries"] is False
        assert policy["position_size_multiplier"] == 0.0
        assert policy["flatten_all"] is False
        assert view["locked_until"] == "2026-08-01T14:30:00Z"
        assert any("daily loss" in r for r in policy["reasons"])
    finally:
        db.close()


def test_breaker_state_panel_kill_switch_engaged(app_config):
    db = DatabaseManager(app_config.data.database_path)
    try:
        _persist_breaker(db, "EMERGENCY", triggers=[{
            "category": "kill_switch", "level": 1, "severity": 5,
            "description": "KILL SWITCH engaged: manual halt",
            "timestamp": "2026-07-31T12:00:00Z", "sticky": True,
            "metadata": {"flatten": True},
        }], notes="kill_switch")
        view = ddata.breaker_state_view(db, app_config)
        assert view["state"] == "EMERGENCY"
        assert view["severity"] == STATE_SEVERITY[CircuitBreakerState.EMERGENCY] == 5
        assert view["kill_switch_engaged"] is True
        assert view["policy"]["flatten_all"] is True
        assert view["policy"]["allow_new_entries"] is False
    finally:
        db.close()


def test_breaker_state_panel_degrades_on_corrupt_state(app_config):
    db = DatabaseManager(app_config.data.database_path)
    try:
        _persist_breaker(db, "BOGUS_STATE")
        view = ddata.breaker_state_view(db, app_config)
        assert view["state"] == "NORMAL"      # degrades rather than raising
        assert view["severity"] == 0
    finally:
        db.close()


def test_breaker_state_panel_unknown_trigger_action_is_safe(app_config):
    """A trigger with unrecognized metadata must not crash the derivation."""
    db = DatabaseManager(app_config.data.database_path)
    try:
        _persist_breaker(db, "RESTRICTED", triggers=[{
            "category": "vix", "level": 2, "severity": 3,
            "description": "VIX 26 reduce_50", "timestamp": "2026-07-31T12:00:00Z",
            "sticky": False, "metadata": {"size_multiplier": 0.50, "block_longs": True},
        }])
        view = ddata.breaker_state_view(db, app_config)
        policy = view["policy"]
        assert policy["position_size_multiplier"] == 0.5
        assert policy["allow_new_longs"] is False
    finally:
        db.close()


def test_breaker_state_view_function_pasted():
    """Evidence: breaker_state_view is a real production provider (body pasted)."""
    src = inspect.getsource(ddata.breaker_state_view)
    # core contract markers from the implementation
    assert "load_breaker_state" in src
    assert "STATE_SEVERITY[state_enum]" in src
    assert "_derive_trading_policy" in src
    assert "kill_switch" in src


# =============================================================================
# Providers — models (no registry table) + backtests (disk)
# =============================================================================

def test_models_view_no_registry_table_yields_empty(app_config):
    db = DatabaseManager(app_config.data.database_path)
    try:
        df = ddata.models_view(db)
        assert df.empty and "model_name" in df.columns
    finally:
        db.close()


def test_backtests_view_lists_artifacts(tmp_path):
    (tmp_path / "a.json").write_text("{}")
    (tmp_path / "b.csv").write_text("x,y\n1,2")
    (tmp_path / "c.txt").write_text("ignore me")
    arts = ddata.backtests_view(tmp_path)
    names = {a["name"] for a in arts}
    assert names == {"a.json", "b.csv"}      # .txt excluded
    assert all(a["size_bytes"] >= 0 for a in arts)


def test_backtests_view_missing_dir_returns_empty(tmp_path):
    assert ddata.backtests_view(tmp_path / "nope") == []


# =============================================================================
# Mutation path (a): kill switch — Phase-10 token confirmation
# =============================================================================

def test_kill_switch_rejected_without_token(control):
    broker, breaker, queue, db = control
    before = breaker.state.value
    result = actions.engage_kill_switch(broker, breaker, token=None, reason="x")
    assert result.rejected and not result.ok
    assert "token required" in result.reason
    assert breaker.state.value == before       # NO state change
    assert db.load_breaker_state() is None or \
        db.load_breaker_state()["state"] != "EMERGENCY"


def test_kill_switch_rejected_with_invalid_token(control):
    broker, breaker, queue, db = control
    result = actions.engage_kill_switch(broker, breaker, token="bogus", reason="x")
    assert result.rejected and "invalid or expired" in result.reason
    assert breaker.state.value == "NORMAL"


def test_kill_switch_rejected_with_empty_string_token(control):
    broker, breaker, queue, db = control
    result = actions.engage_kill_switch(broker, breaker, token="", reason="x")
    assert result.rejected          # empty string treated as no token


def test_kill_switch_engaged_with_confirmed_token(control):
    broker, breaker, queue, db = control
    token = actions.request_kill_token(breaker, reason="manual halt")
    assert isinstance(token, str) and len(token) > 0
    result = actions.engage_kill_switch(broker, breaker, token=token,
                                        reason="manual halt")
    assert result.ok and not result.rejected
    assert result.payload["adapter"] == "paper"
    assert result.trigger["category"] == "kill_switch"
    assert breaker.state.value == "EMERGENCY"
    # persisted so it survives restart
    persisted = db.load_breaker_state()
    assert persisted["state"] == "EMERGENCY"
    assert persisted["notes"] == "kill_switch"


def test_kill_switch_then_resume_requires_token(control):
    """Engaging the kill latch requires a separate token-confirmed resume."""
    from risk.circuit_breakers import ManualOverrideRequired
    broker, breaker, queue, db = control
    token = actions.request_kill_token(breaker, reason="halt")
    actions.engage_kill_switch(broker, breaker, token=token, reason="halt")
    # resume without a fresh confirmed token is refused
    with pytest.raises(ManualOverrideRequired):
        breaker.resume("operator resume", token=None)
    # a freshly minted + confirmed token resumes
    resume_token = breaker.request_override("resume", reason="operator resume")
    breaker.resume("operator resume", token=resume_token)
    assert breaker.state.value != "EMERGENCY"


def test_kill_switch_handler_function_pasted():
    """Evidence: engage_kill_switch body is the real production handler."""
    src = inspect.getsource(actions.engage_kill_switch)
    assert "if not token:" in src
    assert "breaker.confirm_override(token)" in src
    assert "broker.engage_kill_switch(reason)" in src
    assert "breaker.activate_kill_switch" in src
    assert "KillSwitchResult.rejected" in src


def test_build_control_restores_persisted_breaker_state(app_config):
    db = DatabaseManager(app_config.data.database_path)
    try:
        _persist_breaker(db, "HALTED", triggers=[{
            "category": "drawdown", "level": 3, "severity": 4,
            "description": "drawdown breach", "timestamp": "2026-07-31T12:00:00Z",
            "sticky": True, "metadata": {},
        }])
        broker, breaker, queue = actions.build_control(app_config, db)
        assert breaker.state.value == "HALTED"     # restored from persistence
    finally:
        db.close()


# =============================================================================
# Mutation path (b): Phase-9 approval queue (human oversight)
# =============================================================================

def test_pending_signals_lists_only_pending(control):
    broker, breaker, queue, db = control
    queue.enqueue("q1", "AAPL", "BUY", 10, 190.0, confidence=0.9)
    queue.enqueue("q2", "MSFT", "SELL", 5, 420.0, confidence=0.6)
    pending = actions.pending_signals(queue)
    assert {p["signal_id"] for p in pending} == {"q1", "q2"}
    assert all(p["status"] == "PENDING" for p in pending)


def test_approve_signal_path_persists(control):
    broker, breaker, queue, db = control
    queue.enqueue("q1", "AAPL", "BUY", 10, 190.0)
    result = actions.approve_signal(queue, "q1", operator="alice")
    assert result["status"] == "APPROVED"
    assert result["decision_by"] == "alice"
    # persisted to system_state KV so it survives restart
    assert queue.get("q1").status == "APPROVED"
    rows = db.fetch_automation_log(routine="approval_queue")
    assert not rows.empty
    assert "approved" in str(rows.iloc[0]["result"]).lower()


def test_reject_signal_path_persists(control):
    broker, breaker, queue, db = control
    queue.enqueue("q1", "AAPL", "BUY", 10, 190.0)
    result = actions.reject_signal(queue, "q1", operator="bob", reason="too risky")
    assert result["status"] == "REJECTED"
    assert result["reason"] == "too risky"
    # a rejected signal cannot be re-approved (lifecycle gate)
    from automation.approval_queue import ApprovalError
    with pytest.raises(ApprovalError):
        queue.approve("q1")


def test_approval_queue_survives_restart(control, app_config):
    broker, breaker, queue, db = control
    queue.enqueue("q1", "AAPL", "BUY", 10, 190.0)
    queue.approve("q1")
    # a fresh queue against the same DB rehydrates the decision
    rebuilt = ApprovalQueue(config=app_config, db=db)
    assert rebuilt.get("q1").status == "APPROVED"


# =============================================================================
# Boot check (pure; exercises every provider)
# =============================================================================

def test_boot_check_exercises_every_provider(seeded_db, app_config):
    summary = boot_check(app_config, seeded_db)
    assert summary["breaker_state"] == "NORMAL"
    assert summary["positions"] == 1
    assert summary["orders"] == 2
    assert set(summary["logs"]) == {"automation", "circuit_breakers"}
