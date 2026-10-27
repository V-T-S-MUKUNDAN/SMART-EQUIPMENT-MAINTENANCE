import streamlit as st

from equipment import config, ui
from equipment.ai4i import risk as R

ui.guard("ai4i")
ui.banner("ai4i", "Module 05 - Inspection Priority & Decision Support")
ui.require_ready(["ai4i_machine_data"])
f, cfg = ui.ai4i_filters(), ui.ai4i_cfg()
ui.note("AI4I rows are <b>records and operating-condition groups, not distinct physical machines</b>. The ranking below "
        "points to operating conditions that merit inspection; suggested actions are rule-based recommendations, not "
        "diagnoses, and do not guarantee failure prevention.")

risk = ui.ai_risk_scores(f, cfg)
if risk.empty:
    ui.empty_state()
    st.stop()

tab_g, tab_r = st.tabs(["Operating-condition groups", "Individual records"])
with tab_g:
    st.markdown("Groups = machine type x tool-wear band, ranked by the **average indicator score** of their records. "
                "Recorded failures are shown for context only. Groups under 30 records are flagged low-sample.")
    gr = ui.ai_eval("group_ranking", f, cfg)
    gr["Suggested inspection (recommendation)"] = gr.apply(
        lambda r: R.ACTIONS[max(R.COMPONENTS, key=lambda c: r[f"avg_c_{c}"])], axis=1)
    c1, c2 = st.columns([2, 1])
    types = c1.multiselect("Machine type", sorted(gr["machine_type"].unique()), placeholder="All types", key="a5_gtypes")
    hide_low = c2.checkbox("Hide low-sample groups", value=False, key="a5_hidelow")
    v = gr if not types else gr[gr["machine_type"].isin(types)]
    if hide_low:
        v = v[v["low_sample"] == 0]
    if v.empty:
        ui.empty_state()
    else:
        ui.show_table(v, height=400)
        ui.download(v, "ai4i_group_priority.csv", key="a5_dl_g")

with tab_r:
    c1, c2, c3 = st.columns([2, 2, 1])
    cats = c1.multiselect("Risk category", config.RISK_CATEGORIES, default=["High", "Critical"], key="a5_cats")
    search = c2.text_input("Search UDI / Product ID", key="a5_search")
    n = c3.number_input("Max rows", 10, 5000, 200, step=10, key="a5_n")
    v = risk[risk["risk_category"].isin(cats)]
    if search.strip():
        s = search.strip().lower()
        v = v[v["udi"].astype(str).str.contains(s) | v["product_id"].str.lower().str.contains(s)]
    st.caption(f"{len(v):,} record(s) match; showing up to {int(n)}.")
    if v.empty:
        ui.empty_state("No records match the filters/search.")
    else:
        out = v.head(int(n)).copy()
        out["Main reasons"] = out.apply(lambda r: R.explain(r, cfg), axis=1)
        out["Suggested inspection (recommendation)"] = out.apply(R.suggested_action, axis=1)
        cols = ["udi", "product_id", "machine_type", "risk_score", "risk_category", "tool_wear_min", "torque_nm",
                "rotational_speed_rpm", "Main reasons", "Suggested inspection (recommendation)"]
        ui.show_table(out[cols].round(2), height=460, column_config={
            "risk_score": st.column_config.ProgressColumn("risk_score", min_value=0, max_value=100, format="%.2f"),
            "Main reasons": st.column_config.TextColumn(width="large"),
            "Suggested inspection (recommendation)": st.column_config.TextColumn(width="large")})
        ui.download(out[cols].round(3), "ai4i_record_priority.csv", key="a5_dl_r")
