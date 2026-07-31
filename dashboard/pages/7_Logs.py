"""Dashboard page — audit logs (thin renderer)."""
from __future__ import annotations

import streamlit as st

from dashboard import data as ddata
from dashboard._runtime import dashboard_config, dashboard_db
from dashboard._ui import auto_refresh

st.set_page_config(page_title="Logs — fin-trade", page_icon="📜", layout="wide")

config = dashboard_config()
db = dashboard_db(config)
auto_refresh(config)

st.title("📜 Audit logs")
logs = ddata.logs_view(db, limit=config.dashboard.max_log_rows)

st.subheader("Circuit-breaker events")
if logs["circuit_breakers"].empty:
    st.info("No circuit-breaker events.")
else:
    st.dataframe(logs["circuit_breakers"], use_container_width=True, hide_index=True)

st.subheader("Automation log")
if logs["automation"].empty:
    st.info("No automation events.")
else:
    st.dataframe(logs["automation"], use_container_width=True, hide_index=True)
st.caption("Read-only view of the local sqlite DB (circuit_breaker_log + automation_log).")
