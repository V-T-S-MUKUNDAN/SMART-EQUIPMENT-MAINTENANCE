import streamlit as st

from equipment import config, ui
from equipment.azure import priority as P

ui.guard("azure")
ui.banner("azure", "Module 05 - Maintenance Prioritisation & Decision Support")
ui.require_ready(ui.AZURE_TABLES)
f, cfg = ui.azure_filters(), ui.azure_cfg()
ui.note(P.DISCLAIMER)

risk = ui.az_risk_scores(f, cfg)
if risk.empty:
    ui.empty_state()
    st.stop()
table = P.build_priority_table(risk, cfg)

c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
cats = c1.multiselect("Risk category", config.RISK_CATEGORIES, default=config.RISK_CATEGORIES, key="m5_cats")
models = c2.multiselect("Model", sorted(table["Model"].unique()), placeholder="All models", key="m5_models")
search = c3.text_input("Search (machine, reason, date...)", key="m5_search")
min_score = c4.number_input("Min score", 0.0, 100.0, 0.0, key="m5_min")
view = P.filter_table(table, cats or None, models or None, search, min_score)

k1, k2, k3 = st.columns(3)
k1.metric("Machines shown", len(view))
k2.metric("High + Critical", int(view["Category"].isin(["High", "Critical"]).sum()))
k3.metric("Average score (shown)", "n/a" if view.empty else f"{view['Risk score'].mean():.1f}")

if view.empty:
    ui.empty_state("No machines match the filters/search.")
    st.stop()

ui.show_table(view, height=520, column_config={
    "Risk score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.2f"),
    "Telemetry outside band": st.column_config.NumberColumn("Telemetry outside band (%)", format="%.2f"),
    "Main reasons": st.column_config.TextColumn(width="large"),
    "Suggested inspection (recommendation)": st.column_config.TextColumn(width="large")})
d1, d2 = st.columns(2)
with d1:
    ui.download(view, "azure_maintenance_priority_filtered.csv", "Download filtered table (CSV)", key="m5_dl_f")
with d2:
    ui.download(table, "azure_maintenance_priority_all.csv", "Download full table (CSV)", key="m5_dl_all")

st.subheader("Machine detail")
mid = st.selectbox("Machine", view["Machine ID"].tolist(), key="m5_machine")
r = view[view["Machine ID"] == mid].iloc[0]
st.markdown(f"**Machine {mid}** - {r['Category']} ({r['Risk score']:.1f}), priority #{r['Priority']}")
st.write("**Main reasons:** ", r["Main reasons"])
st.write("**Suggested inspection (recommendation):** ", r["Suggested inspection (recommendation)"])
st.caption(f"Supporting timestamps - last failure: {r['Last failure'] or 'none'}; last error: {r['Last error'] or 'none'}; "
           f"last maintenance: {r['Last maintenance'] or 'none'} (evidence window {f.start} to {f.end}).")
with st.expander("Underlying records"):
    ev = ui.cached_call("equipment.azure.risk.evidence", int(mid), f, cfg)
    for key in ("failures", "errors", "maintenance", "telemetry"):
        st.markdown(f"**{key.title()}**")
        if ev[key].empty:
            ui.empty_state(f"No {key} records.")
        else:
            ui.show_table(ev[key], height=200)
