"""Dashboard page — breaker state panel (thin renderer)."""
from __future__ import annotations

import streamlit as st

from dashboard import data as ddata
from dashboard._runtime import dashboard_config, dashboard_db
from dashboard._ui import auto_refresh, render_breaker_state_panel

st.set_page_config(page_title="Breaker state — fin-trade", page_icon="🚦", layout="wide")

config = dashboard_config()
db = dashboard_db(config)
auto_refresh(config)

st.title("🚦 Breaker state")
render_breaker_state_panel(config, db)

st.divider()
view = ddata.breaker_state_view(db, config)
with st.expander("Anchors (persisted)"):
    st.json(view.get("anchors") or {})
with st.expander("Active TradingPolicy (derived)"):
    st.json(view.get("policy") or {})
st.caption(
    "STATE_SEVERITY + active TradingPolicy reconstructed from the persisted "
    "breaker_state row (read-only; never calls evaluate()).")
