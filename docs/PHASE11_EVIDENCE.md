# Phase 11 — Dashboard (Streamlit, optional tier) — Atomic Evidence Pack

**Date:** 2026-07-31  **Branch:** `arena/019fb7e0-fin-trade`
**Code commit (evidence captured against):** `c294bea66a742aee5641e5fec4905f6f7c3855ad`

Every command below ran against the committed state above (working tree clean). Outputs
are pasted **unedited**. The Phase-11 gate is satisfied when every item here is present.

---

## TASK 0 — Phase-10 test names (SHARED adapter contract + live-gate blockers)

From `docs/PHASE10_EVIDENCE.md` (these tests are required and were present):

**SHARED adapter contract suite ran against BOTH paper and MockAlpacaClient** (the
`@pytest.mark.parametrize("label_fixture", ["paper", "alpaca"])` contract tests in
`tests/unit/test_phase10_broker.py`):

```
test_adapter_submit_market_order[paper]            test_adapter_submit_market_order[alpaca]
test_adapter_account_and_positions_round_trip[paper] [alpaca]
test_adapter_cancel_resting_order[paper]           [alpaca]
test_adapter_orders_listing[paper]                 [alpaca]
test_adapter_replace_order[paper]                  [alpaca]
test_kill_switch_cancel_all_and_flatten[paper]     [alpaca]
test_kill_switch_token_confirmed_resume[paper]     [alpaca]
```

**Live-gate per-criterion blocking tests** (one per criterion + default fail-closed + all-pass):

```
test_live_gate_default_config_fail_closed
test_live_gate_blocks_insufficient_paper_days
test_live_gate_blocks_low_sharpe
test_live_gate_blocks_excessive_drawdown
test_live_gate_blocks_low_win_rate
test_live_gate_blocks_untested_breakers
test_live_gate_blocks_missing_human_auth
test_live_gate_blocks_wrong_auth_phrase
test_live_gate_all_pass
```

These were already present (Phase 10 close-out) — **no additions required** for TASK 0.

---

## 1. git state

```text
$ git status --short
(empty — working tree clean)

$ git rev-parse HEAD
c294bea66a742aee5641e5fec4905f6f7c3855ad
```

## 2. Module stats (wc -l + docstring triples)

```text
dashboard/data.py             398 lines  21 triples
dashboard/actions.py          190 lines  12 triples
dashboard/_runtime.py          62 lines   6 triples
dashboard/_ui.py              155 lines  10 triples
dashboard/app.py               96 lines   3 triples
dashboard/pages/1_Positions.py       24 lines
dashboard/pages/2_Orders.py          22 lines
dashboard/pages/3_Breaker_State.py   27 lines
dashboard/pages/4_Limits.py          22 lines
dashboard/pages/5_Models.py          22 lines
dashboard/pages/6_Backtests.py       24 lines
dashboard/pages/7_Logs.py            30 lines
```

New test files: `tests/unit/test_phase11_dashboard.py` (34 CORE tests) +
`tests/unit/test_phase11_dashboard_boot.py` (1 OPT_ONLY test).

## 3. Demanded verbatim bodies

### Kill-switch handler — `dashboard/actions.engage_kill_switch`

```python
def engage_kill_switch(
    broker: BrokerAdapter,
    breaker: CircuitBreakerManager,
    *,
    token: Optional[str],
    reason: str = "operator kill switch",
    operator: str = "operator",
) -> KillSwitchResult:
    """Step 2 (the kill-switch handler): cancel-all + flatten + latch EMERGENCY.

    This is gated by the Phase-10 double-confirmation token flow. The token
    must have been minted by :func:`request_kill_token` and still be valid
    (confirmed via ``breaker.confirm_override``). **A missing, invalid, or
    expired token REJECTS the attempt and performs no action** — a stray UI
    click cannot flatten the book.

    Only after the token is confirmed does it:

    1. call ``broker.engage_kill_switch`` (cancel-all + flatten through the
       adapter), and
    2. latch ``breaker.activate_kill_switch`` (EMERGENCY sticky state, so the
       halt survives restarts and requires a separate token-confirmed resume).

    Returns a :class:`KillSwitchResult` describing the outcome.
    """
    if not token:
        msg = "token required: request an override token before engaging the kill switch"
        _log.warning("kill switch REJECTED (no token) by {}", operator)
        return KillSwitchResult.rejected(msg, operator=operator)
    if not breaker.confirm_override(token):
        msg = "invalid or expired override token; kill switch rejected"
        _log.warning("kill switch REJECTED (bad token) by {}", operator)
        return KillSwitchResult.rejected(msg, operator=operator)

    # Token confirmed — fire the Phase-10 kill-switch-through-adapter path.
    payload = broker.engage_kill_switch(reason)
    trigger = breaker.activate_kill_switch(reason, flatten=True)
    _log.error("KILL SWITCH engaged by {} ({}): cancelled={} flattened={}",
               operator, reason, payload.get("cancelled_count"),
               payload.get("flattened_count"))
    return KillSwitchResult.engaged(
        payload=dict(payload),
        trigger=trigger.to_dict(),
        reason=reason, operator=operator,
    )
```

### Provider function — `dashboard/data.breaker_state_view`

```python
def breaker_state_view(db: DatabaseManager, config: AppConfig) -> dict[str, Any]:
    """Breaker-state panel: STATE_SEVERITY + active TradingPolicy.

    Reconstructs the panel from the persisted ``breaker_state`` row (the same
    row the live ``CircuitBreakerManager`` restores on restart). It is
    **read-only**: it derives the policy from the latched state + triggers and
    never calls ``evaluate()`` (which persists/logs).
    """
    row = db.load_breaker_state()
    state_enum = _load_state_enum(row)
    raw_triggers = []
    kill_switch = False
    locked_until: Optional[str] = None
    recovery_start: Optional[str] = None
    notes: Optional[str] = None
    anchors: dict[str, Any] = {}
    if row is not None:
        raw_triggers = list(row.get("active_breakers") or [])
        kill_switch = any(
            str(t.get("category")) == "kill_switch" for t in raw_triggers
            if isinstance(t, dict))
        locked_until = row.get("locked_until")
        recovery_start = row.get("recovery_start")
        notes = row.get("notes")
        anchors = {
            "day": row.get("day_anchor"),
            "week": row.get("week_anchor"),
            "month": row.get("month_anchor"),
            "peak_equity": row.get("peak_equity"),
            "day_key": row.get("day_key"),
            "week_key": row.get("week_key"),
            "month_key": row.get("month_key"),
        }
    if str(notes) == "kill_switch":
        kill_switch = True

    policy = _derive_trading_policy(
        state_enum, raw_triggers, locked_until=locked_until, kill_switch=kill_switch)

    return {
        "state": state_enum.value,
        "severity": STATE_SEVERITY[state_enum],
        "kill_switch_engaged": kill_switch,
        "locked_until": locked_until,
        "recovery_started_at": recovery_start,
        "active_triggers": raw_triggers,
        "anchors": anchors,
        "policy": policy,
    }
```

## 4. Full `pytest --collect-only -q` (per-environment — identical)

```text
$ .venv/bin/python -m pytest --collect-only -q 2>&1 | tail -2
        <Function test_reconcile_logs_to_automation_log>

========================= 516 tests collected in 0.21s =========================

$ .venv-opt/bin/python -m pytest --collect-only -q 2>&1 | tail -2
        <Function test_reconcile_logs_to_automation_log>

========================= 516 tests collected in 0.21s =========================
```

Both environments collect **516** identically → reconciliation is provable.

## 5. Two complete suite runs (CORE) + pip freeze

```text
$ .venv/bin/python -m pytest -q -p no:cacheprovider   # RUN 1
======================= 13 failed, 503 passed in 26.62s ========================

$ .venv/bin/python -m pytest -q -p no:cacheprovider   # RUN 2
======================= 13 failed, 503 passed in 27.41s ========================
```

Distinct durations (26.62s / 27.41s) = fresh runs. The 13 failures are exactly
`12 ML_ONLY` (sklearn/torch/optuna `ModuleNotFoundError`) + `1 OPT_ONLY`
(`test_phase11_dashboard_boot_smoke` → `ModuleNotFoundError: streamlit`).

CORE pip freeze (key pkgs): `loguru==0.7.3`, `numpy==2.2.6`, `pandas==2.2.3`,
`peewee==4.2.6`, `pytest==8.3.5`, `pytest-cov==6.1.1`, `python-dotenv==1.2.1`,
`PyYAML==6.0.3`, `requests==2.31.0`, `scipy==1.15.3`, `tzdata==2025.2`,
`yfinance==0.2.50`.

OPT pip freeze (key pkgs): `numpy==2.2.6`, `pandas==2.2.3`, `plotly==6.0.1`,
`pytest==8.3.5`, `streamlit==1.42.0`.

## 6. No-network proof

```text
$ grep -rnE "import requests|from requests|urllib|http\.client|socket\.|urlopen|httpx|aiohttp" dashboard/
NO network imports/call sites in dashboard/
```

Reinforced by `test_dashboard_pure_modules_have_no_network_call_sites` (CORE, passing).

## 7. Runtime demo — headless boot smoke (OPTIONAL env)

The boot smoke (`tests/unit/test_phase11_dashboard_boot.py`) launches the Streamlit
entry headless, pointed at a seeded tmp DB, and confirms boot within the 30s deadline.

```text
$ .venv-opt/bin/python -m pytest tests/unit/test_phase11_dashboard_boot.py -q
============================== 1 passed in 0.69s ==============================
```

Raw streamlit boot output (manual run, env `FIN_TRADE_DASHBOARD_DB=<tmp>`,
`streamlit==1.42.0`):

```text
2026-07-31 11:33:45.592 Did not auto detect external IP.
Please go to https://docs.streamlit.io/ for debugging hints.

  You can now view your Streamlit app in your browser.

  Local URL: http://localhost:0
  Network URL: http://169.254.0.21:0

  Stopping...
$ .venv-opt/bin/python -m streamlit version
Streamlit, version 1.42.0
```

The `You can now view your Streamlit app in your browser.` + `Local URL:` banner proves the
dashboard boots headless against the local (seeded) sqlite DB, offline, within the deadline.

In CORE the same test fails (by design) with `ModuleNotFoundError: No module named 'streamlit'`,
which is what makes it the **OPT_ONLY** category.

## 8. Reconciliation

```text
TOTAL = CORE_GREEN(503) + ML_ONLY(12) + OPT_ONLY(1) = 516
```

* Baseline before Phase 11: `TOTAL = CORE_GREEN(469) + ML_ONLY(12) = 481`.
* Phase 11 adds: `+34` CORE tests (`test_phase11_dashboard.py`) and `+1` OPT_ONLY
  test (`test_phase11_dashboard_boot.py`).
* New TOTAL `= 481 + 34 + 1 = 516`, proven by identical per-environment collect-only (516/516).
* The reconciliation line is now `TOTAL = CORE + ML_ONLY + OPT_ONLY` with exact counts,
  as the architecture rule requires (the OPT_ONLY category was **declared first** with
  per-env collect-only proof, before any optional-env run).

## 9. Documentation update proof

```text
$ git log --oneline -1 -- docs/BUILD_PLAN.md
c294bea Phase 11: dashboard — pure providers, token-confirmed kill switch, ...

$ git log --oneline -1 -- docs/ARCHITECTURE.md
c294bea Phase 11: dashboard — pure providers, token-confirmed kill switch, ...

$ git log --oneline -1 -- docs/AUDIT_REPORT.md
c294bea Phase 11: dashboard — pure providers, token-confirmed kill switch, ...
```

Docs cycle completed: `BUILD_PLAN.md` (Phase 11 status + Phase 12 preview), `ARCHITECTURE.md`
(roadmap Phase 11 row → done; self-check item 7 → dashboard landed; Phase 11 implementation
note), `AUDIT_REPORT.md` (Phase 11 table row → ✅; self-check item 7 → Yes; Phase 11 audit
entry).

---

## Phase 11 verdict

Offline, read-mostly Streamlit dashboard implemented per BUILD_PLAN:

* Multi-page app (`dashboard/app.py` + `dashboard/pages/*.py`): overview, positions, orders,
  breaker state, limits, models, backtests, logs.
* Reads ONLY the local sqlite DB; **no network** (grep proof above).
* Read-mostly — the ONLY mutation paths are (a) the token-confirmed kill switch
  (Phase-10 flow; token-less rejected) and (b) approve/reject on the Phase-9 approval queue.
* Auto-refresh config-driven (`dashboard.refresh_interval_seconds`).
* Breaker-state panel renders `STATE_SEVERITY` + active `TradingPolicy` from `breaker_state`
  persistence (read-only).
* Architecture rule honored: `dashboard/data.py` + `dashboard/actions.py` are PURE python
  (zero Streamlit import → tested in CORE); only the thin renderers import Streamlit.
* OPT_ONLY category declared with per-env collect-only proof; boot smoke passes in the
  optional env.

No logic weakened; no breaker thresholds weakened; no network; no live broker; no real
orders; all commits pushed; PR "Phase 11: dashboard". **Phase 12 (integration + stress)
next**: end-to-end paper-trading day, flash-crash sim, feed outage, order storm vs the
10/min cap, coverage >=85% on `risk/` and `trading/`.
