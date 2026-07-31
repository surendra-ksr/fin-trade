"""Streamlit rendering helpers for the dashboard (OPTIONAL tier).

Thin rendering ONLY — every value comes from the pure providers in
``dashboard/data.py`` and the pure mutation handlers in
``dashboard/actions.py``. Nothing here reads the DB or mutates state
directly; it just turns provider output into Streamlit widgets.
"""

from __future__ import annotations

import datetime
from typing import Any

import streamlit as st

from dashboard import actions, data as ddata
from utils.config import AppConfig
from utils.constants import STATE_SEVERITY, CircuitBreakerState

#: Severity -> display color, driven by the STATE_SEVERITY rank (0..6).
_SEVERITY_COLOR = {
    CircuitBreakerState.NORMAL: "green",
    CircuitBreakerState.CAUTION: "blue",
    CircuitBreakerState.RESTRICTED: "orange",
    CircuitBreakerState.DEFENSIVE: "orange",
    CircuitBreakerState.HALTED: "red",
    CircuitBreakerState.EMERGENCY: "red",
    CircuitBreakerState.SUSPENDED: "gray",
}


def auto_refresh(config: AppConfig) -> None:
    """Config-driven auto-refresh of the whole page.

    Uses ``st.fragment(run_every=...)`` keyed off
    ``dashboard.refresh_interval_seconds`` so every page refreshes on the
    configured cadence with no third-party component.
    """
    interval = max(5, int(config.dashboard.refresh_interval_seconds))

    @st.fragment(run_every=datetime.timedelta(seconds=interval))
    def _tick() -> None:
        st.session_state["dashboard_last_refresh"] = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()
        st.rerun()

    _tick()


def severity_badge(state_name: str, severity: int) -> None:
    """Render the breaker STATE_SEVERITY as a colored badge."""
    try:
        state = CircuitBreakerState(state_name)
    except ValueError:
        state = CircuitBreakerState.NORMAL
    color = _SEVERITY_COLOR.get(state, "gray")
    st.markdown(
        f":{color}[**{state_name}** — severity {severity}/6]"
    )


def fmt_opt(value: Any, *, fmt: str = "{:.4f}", prefix: str = "") -> str:
    """Format an optional numeric for display; '—' when None."""
    if value is None or value == "":
        return "—"
    try:
        return prefix + fmt.format(float(value))
    except (TypeError, ValueError):
        return str(value)


def render_kill_switch_panel(broker: Any, breaker: Any) -> None:
    """The kill-switch mutation path with the Phase-10 token flow.

    Two steps: (1) mint an override token, (2) re-enter it to engage.
    A token-less attempt is rejected by ``actions.engage_kill_switch`` and
    performs no action — this is proven by the mutation-path tests.
    """
    st.subheader("🛑 Kill switch")
    st.caption(
        "Phase-10 token-confirmation flow. Step 1 mints a short-lived "
        "override token; step 2 re-enters it to fire cancel-all + flatten + "
        "EMERGENCY latch. A missing/invalid token is rejected with no action."
    )
    reason = st.text_input("Reason", value="operator kill switch", key="ks_reason")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("1. Request override token", type="secondary"):
            token = actions.request_kill_token(breaker, reason=reason)
            st.session_state["ks_pending_token"] = token
            st.success("Token minted (valid 120s). Re-enter it below to engage.")
    with col2:
        token_in = st.text_input(
            "Override token", value="", key="ks_token",
            help="Paste the token from step 1 to confirm and engage.")
        if st.button("2. Engage kill switch", type="primary"):
            result = actions.engage_kill_switch(
                broker, breaker, token=token_in or None, reason=reason)
            if result.rejected:
                st.error(f"REJECTED — {result.reason}")
            else:
                st.error(
                    f"ENGAGED — cancelled {result.payload.get('cancelled_count')} "
                    f"order(s), flattened {result.payload.get('flattened_count')} "
                    f"position(s); breaker latched EMERGENCY.")
            st.rerun()


def render_approval_panel(queue: Any) -> None:
    """The Phase-9 approval-queue mutation path (human oversight)."""
    st.subheader("✅ Approval queue")
    st.caption(
        "Phase-9 lifecycle: approve/reject PENDING signals. Every decision is "
        "persisted to system_state + automation_log (survives restart).")
    pending = actions.pending_signals(queue)
    if not pending:
        st.info("No pending signals awaiting approval.")
        return
    for sig in pending:
        with st.container(border=True):
            c1, c2, c3 = st.columns([4, 1, 1])
            c1.markdown(
                f"**{sig['symbol']}** · {sig['side']} {sig['quantity']} @ "
                f"{sig['price']} · conf {fmt_opt(sig.get('confidence'), '{:.2f}')} "
                f"· `{sig['signal_id']}`")
            if c2.button("Approve", key=f"app_{sig['signal_id']}"):
                actions.approve_signal(queue, sig["signal_id"])
                st.rerun()
            if c3.button("Reject", key=f"rej_{sig['signal_id']}"):
                actions.reject_signal(queue, sig["signal_id"], reason="dashboard")
                st.rerun()


def render_breaker_state_panel(config: AppConfig, db: Any) -> None:
    """Breaker-state panel: STATE_SEVERITY + active TradingPolicy."""
    view = ddata.breaker_state_view(db, config)
    st.subheader("🚦 Breaker state")
    severity_badge(view["state"], view["severity"])
    if view["kill_switch_engaged"]:
        st.error("⚠️ KILL SWITCH ENGAGED")
    if view["locked_until"]:
        st.warning(f"Trading locked until {view['locked_until']}")
    policy = view["policy"]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Size multiplier", fmt_opt(policy["effective_size_multiplier"], "{:.2f}"))
    m2.metric("New entries", "allowed" if policy["allow_new_entries"] else "blocked")
    m3.metric("Flatten all", "yes" if policy["flatten_all"] else "no")
    m4.metric("Max open", policy["max_open_positions"])
    triggers = view.get("active_triggers") or []
    if triggers:
        st.markdown("**Active triggers:**")
        for t in triggers:
            st.markdown(f"- `{t.get('category')}` — {t.get('description', '')}")
