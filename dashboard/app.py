"""Phase 11 dashboard — Streamlit entry point (OPTIONAL tier).

Offline, read-mostly multi-page control surface. Reads ONLY the local
sqlite DB. The two mutation paths live on the home page:

* **Kill switch** — Phase-10 token-confirmation flow (see _ui.render_kill_switch_panel).
* **Approval queue** — Phase-9 approve/reject (see _ui.render_approval_panel).

Run headless (boot smoke):
    streamlit run dashboard/app.py --server.headless=true --server.port=0

Every page value comes from the PURE providers in ``dashboard/data.py``; this
file is a thin renderer. Auto-refresh is config-driven
(``dashboard.refresh_interval_seconds``).
"""

from __future__ import annotations

import streamlit as st

from dashboard import actions, data as ddata
from dashboard._runtime import boot_check, dashboard_config, dashboard_db
from dashboard._ui import (
    auto_refresh,
    render_approval_panel,
    render_breaker_state_panel,
    render_kill_switch_panel,
)

st.set_page_config(
    page_title="fin-trade dashboard",
    page_icon="📊",
    layout="wide",
)


# ---- tiny formatting helpers (defined before use) ----
def _fmt_money(v) -> str:
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "—"


def _fmt_pct(v) -> str:
    return f"{v * 100:.2f}%" if isinstance(v, (int, float)) else "—"


def _load():
    """Load config + open the local DB + build control components."""
    config = dashboard_config()
    db = dashboard_db(config)
    try:  # fail-fast boot check: exercise every provider once
        boot_check(config, db)
    except Exception as exc:  # keep the UI up; surface the error inline
        st.error(f"Dashboard boot check failed: {exc}")
    broker, breaker, queue = actions.build_control(config, db)
    return config, db, broker, breaker, queue


config, db, broker, breaker, queue = _load()

# ---- sidebar ----
st.sidebar.title(config.dashboard.title)
st.sidebar.caption(
    f"DB: `{db.path}`  \nRefresh: every {config.dashboard.refresh_interval_seconds}s "
    "(config-driven)")
last = st.session_state.get("dashboard_last_refresh")
if last:
    st.sidebar.caption(f"Last refresh: {last}")
if st.sidebar.button("Refresh now"):
    st.rerun()
auto_refresh(config)

# ---- title ----
st.title("📊 fin-trade — overview")

# ---- breaker + mutation panels (always visible) ----
col_a, col_b = st.columns(2)
with col_a:
    render_breaker_state_panel(config, db)
    render_kill_switch_panel(broker, breaker)
with col_b:
    render_approval_panel(queue)

st.divider()

# ---- overview metrics ----
ov = ddata.overview_view(db)
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Equity", _fmt_money(ov.get("latest_equity")))
c2.metric("Open positions", ov["open_positions_count"])
c3.metric("Realized P&L", _fmt_money(ov["realized_pnl"]))
c4.metric("Daily return", _fmt_pct(ov.get("daily_return")))
c5.metric("Drawdown", _fmt_pct(ov.get("drawdown")))

st.caption(
    "Read-only view of the local sqlite DB. Mutation is limited to the kill "
    "switch (token-confirmed) and the approval queue (human oversight).")
