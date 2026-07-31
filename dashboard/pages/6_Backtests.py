"""Dashboard page — backtest report artifacts (thin renderer)."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from dashboard import data as ddata
from dashboard._runtime import dashboard_config
from dashboard._ui import auto_refresh

st.set_page_config(page_title="Backtests — fin-trade", page_icon="🧪", layout="wide")

config = dashboard_config()
auto_refresh(config)

st.title("🧪 Backtest reports")
report_dir = config.resolve_path(config.backtesting.report_dir)
artifacts = ddata.backtests_view(Path(report_dir))
if not artifacts:
    st.info(f"No report artifacts in `{report_dir}`.")
else:
    st.dataframe(artifacts, use_container_width=True, hide_index=True)
st.caption("Local report directory only (offline).")
