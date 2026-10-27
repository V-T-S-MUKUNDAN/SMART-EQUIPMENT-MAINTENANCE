import plotly.express as px
import streamlit as st

from equipment import charts, config, ui
from equipment.ai4i import risk as R

ui.guard("ai4i")
ui.banner("ai4i", "AI4I 2020 - Overview")
ui.require_ready(["ai4i_machine_data"])
f, cfg = ui.ai4i_filters(), ui.ai4i_cfg()
ui.note("AI4I 2020 holds <b>10,000 independent, labelled operating records</b> (synthetic). Rows are not distinct "
        "machines and UDI is not a time stamp, so there are no trends over time here.")

o = ui.ai_q("overview", f).iloc[0]
if o["total_records"] == 0:
    ui.empty_state()
    st.stop()
risk = ui.ai_risk_scores(f, cfg)
s = R.summary(risk)
ui.kpi_row([
    ("Records", ui.fmt_int(o["total_records"]), "Records matching the sidebar filters"),
    ("Machine failures", ui.fmt_int(o["machine_failures"]), "Records with Machine failure = 1"),
    ("Failure rate", f"{o['failure_rate_pct']:.2f}%", "Failures / records"),
    ("No failure", ui.fmt_int(o["records_without_failure"]), None),
    ("Avg risk indicator", f"{s['avg_score']:.1f}", "Operating-condition indicator, 0-100 (Module 04)"),
    ("High + Critical records", ui.fmt_int(s["by_category"]["High"] + s["by_category"]["Critical"]), None),
])

c1, c2 = st.columns(2)
with c1:
    bt = ui.ai_q("failure_by_type", f)
    fig = px.bar(bt, x="machine_type", y="failure_rate_pct", text="failure_rate_pct", hover_data=["records", "failures"],
                 title="Failure rate by machine type (%)")
    ui.show_fig(fig, key="a_ov_type")
with c2:
    fm = ui.ai_q("failure_modes", f)
    fig = px.bar(fm, x="mode", y="records_with_mode", text="records_with_mode", hover_data=["name"],
                 title="Records per failure-mode label")
    ui.show_fig(fig, key="a_ov_modes")
c3, c4 = st.columns(2)
with c3:
    cb = ui.ai_q("condition_bins", f)
    tw = cb[cb["variable"] == "Tool wear [min]"]
    fig = px.bar(tw, x="bin_low", y="failure_rate_pct", hover_data=["records", "failures"],
                 title="Failure rate by tool-wear bin (50 min bins, % of records)")
    fig.update_xaxes(title="Tool wear bin start [min]")
    ui.show_fig(fig, key="a_ov_wear")
with c4:
    ui.show_fig(charts.category_bar(s["by_category"], "Operating-condition risk category (records)", "records"),
                key="a_ov_cat")
st.caption("Association is not causation: failure rates by condition describe how this dataset was recorded/generated.")

st.subheader("Top 10 records by operating-condition risk indicator")
top = risk.head(10).copy()
top["Why"] = top.apply(lambda r: R.explain(r, cfg), axis=1)
ui.show_table(top[["udi", "product_id", "machine_type", "risk_score", "risk_category", "tool_wear_min", "torque_nm",
                   "rotational_speed_rpm", "Why"]])
st.caption(R.DISCLAIMER)
