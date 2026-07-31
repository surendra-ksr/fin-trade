"""Dashboard page — registered models (thin renderer)."""
from __future__ import annotations

import streamlit as st

from dashboard import data as ddata
from dashboard._runtime import dashboard_config, dashboard_db
from dashboard._ui import auto_refresh

st.set_page_config(page_title="Models — fin-trade", page_icon="🧠", layout="wide")

config = dashboard_config()
db = dashboard_db(config)
auto_refresh(config)

st.title("🧠 Registered models")
df = ddata.models_view(db)
if df.empty:
    st.info("No models registered (the model_registry table is created lazily).")
else:
    st.dataframe(df, use_container_width=True, hide_index=True)
st.caption("Read-only view of the local sqlite DB (model_registry).")
