"""Dashboard page — open positions (thin renderer)."""
from __future__ import annotations

import streamlit as st

from dashboard import data as ddata
from dashboard._runtime import dashboard_config, dashboard_db
from dashboard._ui import auto_refresh

st.set_page_config(page_title="Positions — fin-trade", page_icon="📂", layout="wide")

config = dashboard_config()
db = dashboard_db(config)
auto_refresh(config)

st.title("📂 Open positions")
df = ddata.positions_view(db)
if df.empty:
    st.info("No open positions.")
else:
    show = [c for c in ("symbol", "side", "quantity", "entry_price", "cost_basis",
                        "strategy", "signal_id", "entry_time") if c in df.columns]
    st.dataframe(df[show], use_container_width=True, hide_index=True)
st.caption("Read-only view of the local sqlite DB (paper_trades, status OPEN).")
