import plotly.express as px
import streamlit as st

from equipment import ui

ui.guard("ai4i")
ui.banner("ai4i", "Module 02 - Failures, Failure Modes & Operating Conditions")
ui.require_ready(["ai4i_machine_data"])
f = ui.ai4i_filters()
ui.note("Rates below describe the <b>records in this dataset</b>. They show association between conditions and recorded "
        "failures, not causation. UDI is an identifier, not a time stamp, so nothing here is a trend over time.")

o = ui.ai_q("overview", f).iloc[0]
if o["total_records"] == 0:
    ui.empty_state()
    st.stop()
ui.kpi_row([("Total records", ui.fmt_int(o["total_records"]), None),
            ("Machine failures", ui.fmt_int(o["machine_failures"]), None),
            ("Failure rate", f"{o['failure_rate_pct']:.3f}%", None),
            ("Records without failure", ui.fmt_int(o["records_without_failure"]), None),
            ("Records with >1 mode flag", ui.fmt_int(o["records_with_multiple_modes"]), None)])

tabs = st.tabs(["Failure overview", "Operating conditions", "Failure modes", "Window-function analysis"])

with tabs[0]:
    bt = ui.ai_q("failure_by_type", f)
    c1, c2 = st.columns(2)
    with c1:
        ui.show_fig(px.bar(bt, x="machine_type", y="failures", text="failures", title="Failures by machine type"),
                    key="a2_type_n")
    with c2:
        ui.show_fig(px.bar(bt, x="machine_type", y="failure_rate_pct", text="failure_rate_pct",
                           title="Failure rate by machine type (%)"), key="a2_type_r")
    ui.show_table(bt)
    ui.download(bt, "ai4i_failure_by_type.csv", key="a2_dl_type")
    st.markdown("**Failure rate (%) by machine type within tool-wear bands**")
    tw = ui.ai_q("type_by_wear_band", f)
    ui.show_table(tw)
    ui.download(tw, "ai4i_type_by_tool_wear_band.csv", key="a2_dl_tw")

with tabs[1]:
    st.markdown("Fixed-width, documented bins: air temperature 1 K from 295 | process temperature 1 K from 305 | "
                "rotational speed 250 rpm from 1000 | torque 10 Nm from 0 | tool wear 50 min from 0. "
                "Bins with fewer than 30 records are flagged as low-sample and excluded from the rate ranking.")
    vs = ui.ai_q("variable_summary", f)
    ui.show_table(vs)
    st.caption("`correlation_with_failure` is the Pearson (point-biserial) correlation between the measurement and the 0/1 "
               "failure label, computed in SQL. A small value does not rule out a strong non-linear pattern, and a large one is not causation.")
    cb = ui.ai_q("condition_bins", f)
    variable = st.selectbox("Operating condition", cb["variable"].unique().tolist(), key="a2_var")
    d = cb[cb["variable"] == variable].copy()
    d["bin"] = d["bin_low"].astype(str) + " - " + d["bin_high"].astype(str)
    d["Sample"] = d["low_sample"].map({0: "n >= 30", 1: "low sample (n < 30)"})
    fig = px.bar(d, x="bin", y="failure_rate_pct", color="Sample", hover_data=["records", "failures", "lift_vs_overall"],
                 title=f"Failure rate by {variable} bin (%)")
    fig.update_xaxes(title=variable, type="category")
    ui.show_fig(fig, key="a2_bins")
    ui.show_table(d.drop(columns=["bin", "Sample"]), height=300)
    ui.download(cb, "ai4i_condition_bins.csv", "Download all condition bins (CSV)", key="a2_dl_bins")
    st.caption("`rate_rank` = RANK() of the bin's failure rate within the variable; `rate_change_vs_prev_bin_pp` = LAG() difference to "
               "the neighbouring (lower-valued) bin; `cumulative_failures` = running SUM() across ordered bins; `lift` = bin rate / overall rate.")

with tabs[2]:
    st.markdown("Each failure mode is counted **independently**. A record can carry several mode flags, so mode counts "
                "need not add up to the number of failed records.")
    fm = ui.ai_q("failure_modes", f)
    c1, c2 = st.columns(2)
    with c1:
        ui.show_fig(px.bar(fm, x="mode", y="records_with_mode", text="records_with_mode", hover_data=["name"],
                           title="Records per failure-mode label"), key="a2_fm")
    with c2:
        ui.show_fig(px.bar(fm, x="mode", y="rate_of_all_records_pct", text="rate_of_all_records_pct",
                           title="Mode rate (% of all records)"), key="a2_fmr")
    ui.show_table(fm)
    ui.download(fm, "ai4i_failure_modes.csv", key="a2_dl_fm")
    sum_modes = int(fm["records_with_mode"].sum())
    st.caption(f"Sum of mode counts = {sum_modes}; machine failures = {int(o['machine_failures'])}. "
               "The difference comes from multi-mode records and label inconsistencies shown below.")
    c3, c4 = st.columns(2)
    with c3:
        st.markdown("**Modes flagged per record**")
        ui.show_table(ui.ai_q("modes_per_record", f))
        st.markdown("**Failure modes by machine type**")
        ui.show_table(ui.ai_q("modes_by_type", f))
    with c4:
        st.markdown("**Modes flagged together on the same record**")
        co = ui.ai_q("mode_cooccurrence", f)
        if co.empty:
            ui.empty_state("No record carries two mode flags in the current selection.")
        else:
            ui.show_table(co)
    st.markdown("**Consistency of `Machine failure` with the individual mode labels** (flagged, never modified)")
    lc = ui.ai_q("label_consistency", f)
    ui.show_table(lc)
    bad = ui.ai_q("inconsistent_records", f)
    if bad.empty:
        st.success("No inconsistent records in the current selection.")
    else:
        st.warning(f"{len(bad)} record(s) have an ambiguous combination: Machine failure = 1 with no mode flag, or a mode "
                   "flag with Machine failure = 0 (for example RNF). They are kept exactly as recorded.")
        ui.show_table(bad, height=240)
        ui.download(bad, "ai4i_inconsistent_records.csv", key="a2_dl_bad")
    st.markdown("**Do the labels agree with the failure rules in the dataset description?**")
    ra = ui.ai_q("rule_agreement", f)
    ui.show_table(ra)
    st.caption("Rules come from the published dataset description (HDF: temperature difference < 8.6 K and speed < 1380 rpm; "
               "PWF: power < 3500 W or > 9000 W; OSF: tool wear x torque > 11000/12000/13000 minNm for L/M/H). "
               "TWF (random tool-wear window) and RNF (0.1% random chance) have no deterministic rule.")

with tabs[3]:
    st.markdown("Groups are **machine type x tool-wear band**. `ROW_NUMBER()` orders all groups by failure rate, `RANK()` orders "
                "them within a type, `LAG()` compares neighbouring wear bands of the same type, and window `SUM()` gives "
                "running totals and shares. The ordering is by tool wear, **not by time or UDI**.")
    gw = ui.ai_q("group_window_analysis", f)
    ui.show_table(gw, height=420)
    ui.download(gw, "ai4i_group_window_analysis.csv", key="a2_dl_gw")
    fig = px.line(gw, x="tool_wear_band", y="failure_rate_pct", color="machine_type", markers=True,
                  category_orders={"tool_wear_band": ["0-49", "50-99", "100-149", "150-199", "200-239", "240+"]},
                  title="Failure rate by tool-wear band and machine type (%)")
    ui.show_fig(fig, key="a2_gw")
    st.caption("Groups with fewer than 30 records are flagged `low_sample` - their rates are unstable.")
