import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from equipment import charts, config, ui
from equipment.azure import priority as P
from equipment.azure import risk as R

ui.guard("azure")
ui.banner("azure", "Executive Overview")
ui.require_ready(ui.AZURE_TABLES)
f, cfg = ui.azure_filters(), ui.azure_cfg()

k = ui.az_q("kpis", f).iloc[0]
risk = ui.az_risk_scores(f, cfg)
s = R.summary(risk)

ui.kpi_row([
    ("Total machines", ui.fmt_int(k["machines"]), "Machines in the current machine filter"),
    ("Telemetry records", ui.fmt_int(k["telemetry_records"]), "Hourly sensor readings in the date range"),
    ("Recorded failures", ui.fmt_int(k["failures"]), "Historical component failures (not predictions)"),
    ("Error events", ui.fmt_int(k["errors"]), "Operational errors logged"),
    ("Maintenance events", ui.fmt_int(k["maintenance_events"]), "Planned and failure-linked maintenance"),
    ("Avg risk score", "n/a" if s["avg_score"] is None else f"{s['avg_score']:.1f}",
     "Mean of the explainable 0-100 machine risk score (see Module 04)"),
])
st.caption(f"Date range {f.start} to {f.end}"
           + (f" | {len(f.machines)} machine(s) selected" if f.machines else " | all machines")
           + (f" | components: {', '.join(f.components)}" if f.components else "")
           + ". Risk scores are as of the end of the date range; the component filter does not change them.")

if k["machines"] == 0 or risk.empty:
    ui.empty_state()
    st.stop()

c1, c2 = st.columns(2)
with c1:
    ui.show_fig(charts.category_bar(s["by_category"], "Machines by risk category"), key="ov_cat")
    st.caption(f"Thresholds: Medium >= {cfg.thresholds['medium']}, High >= {cfg.thresholds['high']}, "
               f"Critical >= {cfg.thresholds['critical']} (configurable in Module 04).")
with c2:
    trend = ui.az_q("failure_trend", f)
    if trend["failures"].sum() == 0:
        ui.empty_state("No recorded failures in the selected range.")
    else:
        fig = go.Figure()
        fig.add_bar(x=trend["month"], y=trend["failures"], name="Failures per month")
        fig.add_scatter(x=trend["month"], y=trend["rolling_3m_avg"], name="3-month rolling average", mode="lines+markers")
        fig.update_layout(title="Historical failure trend")
        ui.show_fig(fig, key="ov_trend")

c3, c4 = st.columns(2)
with c3:
    top = ui.az_q("failures_by_machine", f).head(10)
    top = top[top["failures"] > 0]
    if top.empty:
        ui.empty_state("No failures recorded for the selected machines.")
    else:
        top = top.assign(machine=top["machine_id"].astype(str))
        fig = px.bar(top.sort_values("failures"), x="failures", y="machine", color="model", orientation="h",
                     title="Most failure-prone machines (top 10)")
        fig.update_yaxes(type="category", title="Machine ID")
        ui.show_fig(fig, key="ov_top")
with c4:
    comp = ui.az_q("failures_by_component", f)
    if comp.empty:
        ui.empty_state()
    else:
        ui.show_fig(px.bar(comp, x="component", y="failures", text="failures", title="Failures by component"),
                    key="ov_comp")

st.subheader("Top 10 maintenance-priority machines")
prio = P.build_priority_table(risk, cfg)
cols = ["Priority", "Machine ID", "Model", "Age", "Risk score", "Category", "Failures", "Errors", "Last maintenance"]
ui.show_table(prio[cols].head(10))
ui.download(prio, "azure_maintenance_priority.csv", "Download full priority table (CSV)", key="ov_dl")

st.subheader("Why are the highest-priority machines ranked there?")
st.caption("Each score is the sum of the weighted components below, so the bars add up to the risk score.")
contrib = R.contribution_frame(risk.head(3), cfg)
for _, r in risk.head(3).iterrows():
    with st.expander(f"#{int(r['priority_rank'])}  Machine {int(r['machine_id'])} ({r['model']}, age {int(r['age'])}) "
                     f"- score {r['risk_score']:.1f} ({r['risk_category']})", expanded=(r['priority_rank'] == risk['priority_rank'].min())):
        st.write(R.explain(r, cfg, top=4))
        ui.show_fig(charts.contribution_bar(contrib[contrib.machine_id == r["machine_id"]], cfg.labels,
                                            title="Score composition (points)"), height=200, key=f"ov_ex_{r['machine_id']}")
st.caption("Risk scores summarise historical recorded events and telemetry. They are not a validated "
           "prediction of future failures. Suggested actions are inspection recommendations only.")
