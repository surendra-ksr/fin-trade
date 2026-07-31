"""Dashboard page — recent orders / signals (thin renderer)."""
from __future__ import annotations

import streamlit as st

from dashboard import data as ddata
from dashboard._runtime import dashboard_config, dashboard_db
from dashboard._ui import auto_refresh

st.set_page_config(page_title="Orders — fin-trade", page_icon="📨", layout="wide")

config = dashboard_config()
db = dashboard_db(config)
auto_refresh(config)

st.title("📨 Recent orders & signals")
df = ddata.orders_view(db, limit=config.dashboard.max_log_rows)
if df.empty:
    st.info("No signals recorded.")
else:
    st.dataframe(df, use_container_width=True, hide_index=True)
st.caption("Read-only view of the local sqlite DB (trade_signals feed).")
