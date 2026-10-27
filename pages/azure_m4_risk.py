import pandas as pd
import plotly.express as px
import streamlit as st

from equipment import charts, config, ui
from equipment.azure import risk as R

ui.guard("azure")
ui.banner("azure", "Module 04 - Equipment Health & Risk Scoring")
ui.require_ready(ui.AZURE_TABLES)
f = ui.azure_filters()
base_cfg = R.RiskConfig.default()
cfg = ui.azure_cfg()

ui.note("An <b>explainable historical risk indicator</b> built from recorded failures, errors, telemetry and "
        "maintenance. There is <b>no validated future-failure prediction model</b> behind it.")

with st.expander("Scoring model: components, weights and thresholds (configurable)", expanded=False):
    st.markdown(
        "Each component is scaled to 0-1 across **all machines** (min-max, or a linear recency decay), then multiplied "
        "by its weight. Weights are relative: they are re-normalised so that the score is out of 100, and per machine "
        "over the components that have data. Because contributions add up exactly to the score, every score can be "
        "explained component by component.")
    new_w, cols = {}, st.columns(4)
    for i, c in enumerate(R.COMPONENTS):
        new_w[c] = cols[i % 4].number_input(cfg.labels[c], min_value=0.0, max_value=100.0, step=1.0,
                                            value=float(cfg.weights[c]), key=f"w_{c}")
    tcols = st.columns(3)
    t_med = tcols[0].number_input("Medium from", 0.0, 100.0, float(cfg.thresholds["medium"]), key="t_med")
    t_high = tcols[1].number_input("High from", 0.0, 100.0, float(cfg.thresholds["high"]), key="t_high")
    t_crit = tcols[2].number_input("Critical from", 0.0, 100.0, float(cfg.thresholds["critical"]), key="t_crit")
    pcols = st.columns(3)
    look = pcols[0].number_input("Recency look-back (days)", 1, 730, int(cfg.parameters["recency_lookback_days"]), key="p_look")
    twin = pcols[1].number_input("Telemetry window (days)", 1, 365, int(cfg.parameters["telemetry_window_days"]), key="p_twin")
    sig = pcols[2].number_input("Telemetry band (sigma multiple)", 0.5, 6.0, float(cfg.parameters["telemetry_sigma_k"]),
                                step=0.5, key="p_sig")
    b1, b2 = st.columns(2)
    if b1.button("Apply settings"):
        trial = cfg.copy()
        trial.weights, trial.thresholds = new_w, {"medium": t_med, "high": t_high, "critical": t_crit}
        trial.parameters = {"recency_lookback_days": look, "telemetry_window_days": twin, "telemetry_sigma_k": sig}
        try:
            trial.validate()
            st.session_state["az_cfg"] = trial
            st.rerun()
        except ValueError as exc:
            st.error(f"Invalid settings: {exc}")
    if b2.button("Reset to defaults (config/azure_risk.json)"):
        for k in [k for k in st.session_state if k.startswith(("w_", "t_", "p_"))]:
            del st.session_state[k]
        st.session_state["az_cfg"] = base_cfg
        st.rerun()
    nw = cfg.normalised_weights()
    st.dataframe(pd.DataFrame({"Component": [cfg.labels[c] for c in R.COMPONENTS],
                               "Weight (active)": [cfg.weights[c] for c in R.COMPONENTS],
                               "Share of score (%)": [round(nw[c], 1) for c in R.COMPONENTS]}),
                 hide_index=True, width="content")
    if cfg.key() != base_cfg.key():
        st.warning("Custom settings are active for this session (they differ from config/azure_risk.json).")

risk = ui.az_risk_scores(f, cfg)
if risk.empty:
    ui.empty_state()
    st.stop()
s = R.summary(risk)

ui.kpi_row([("Machines scored", str(s["machines"]), None), ("Average score", f"{s['avg_score']:.2f}", None),
            ("Median score", f"{s['median']:.2f}", None), ("Highest score", f"{s['max']:.2f}", None),
            ("High-risk machines", str(s["by_category"]["High"]), None),
            ("Critical-risk machines", str(s["by_category"]["Critical"]), None)])
st.caption(f"Evidence window {f.start} to {f.end} (scores are as of {f.end}). Thresholds are applied as configured and are "
           "not adjusted to produce any particular number of high-risk machines. Review them against the distribution below.")

c1, c2 = st.columns(2)
with c1:
    ui.show_fig(charts.category_bar(s["by_category"]), key="m4_cat")
with c2:
    ui.show_fig(charts.histogram(risk, "risk_score", "Score distribution", cfg.thresholds), key="m4_hist")

st.subheader("Risk scores")
view = risk[["priority_rank", "machine_id", "model", "age", "risk_score", "risk_category", "failure_count", "error_count",
             "planned_maintenance_count", "tele_exceed_rate", "days_since_maint", "data_flags"]].rename(columns={
    "priority_rank": "Rank", "machine_id": "Machine", "model": "Model", "age": "Age", "risk_score": "Risk score",
    "risk_category": "Category", "failure_count": "Failures", "error_count": "Errors",
    "planned_maintenance_count": "Planned maint.", "tele_exceed_rate": "Telemetry outside band",
    "days_since_maint": "Days since maint.", "data_flags": "Data notes"})
ui.show_table(view, height=320, column_config={
    "Risk score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.2f"),
    "Telemetry outside band": st.column_config.NumberColumn(format="percent"),
    "Days since maint.": st.column_config.NumberColumn(format="%.0f")})
full = risk.copy()
ui.download(full, "azure_risk_scores_with_components.csv", "Download scores with all component values (CSV)", key="m4_dl")

st.subheader("Component contributions")
topn = st.slider("Machines shown", 5, min(40, len(risk)), min(15, len(risk)), key="m4_topn")
contrib = R.contribution_frame(risk.head(topn), cfg)
ui.show_fig(charts.contribution_bar(contrib, cfg.labels, title=f"Score composition - top {topn} machines (points)"),
            height=max(360, 24 * topn), key="m4_contrib")

st.subheader("Explain and trace one machine")
mid = st.selectbox("Machine", risk["machine_id"].tolist(), key="m4_machine")
row = risk[risk.machine_id == mid].iloc[0]
st.markdown(f"**Machine {mid}** ({row['model']}, age {int(row['age'])}): score **{row['risk_score']:.2f}** - "
            f"**{row['risk_category']}** (rank {int(row['priority_rank'])} of {len(risk)} shown/scored)")
st.write(R.explain(row, cfg, top=8))
detail = pd.DataFrame([{
    "Component": cfg.labels[c], "Raw measure": {
        "fail_freq": f"{int(row['failure_count'])} failures", "fail_rec": f"last failure {row['last_failure'] or 'none'}",
        "err_freq": f"{int(row['error_count'])} errors", "err_rec": f"last error {row['last_error'] or 'none'}",
        "tele": "n/a" if pd.isna(row["tele_exceed_rate"]) else f"{row['tele_exceed_rate']:.2%} of readings outside band",
        "maint_burden": f"{int(row['planned_maintenance_count'])} planned maintenance events",
        "maint_rec": f"{row['days_since_maint']:.0f} days since last maintenance",
        "age": f"{int(row['age'])} years"}[c],
    "Normalised (0-1)": None if pd.isna(row[f"n_{c}"]) else round(float(row[f"n_{c}"]), 3),
    "Weight": cfg.weights[c], "Points": round(float(row[f"c_{c}"]), 2)} for c in R.COMPONENTS])
ui.show_table(detail)
st.caption("Points sum to the risk score. Missing telemetry removes its weight and re-normalises the remaining "
           "components (see Data notes).")

with st.expander("Underlying records for this score (traceability)"):
    ev = ui.cached_call("equipment.azure.risk.evidence", int(mid), f, cfg)
    t1, t2, t3, t4 = st.tabs(["Failures", "Errors", "Maintenance", "Telemetry outside band"])
    for tab, key, name in ((t1, "failures", "failures"), (t2, "errors", "errors"), (t3, "maintenance", "maintenance"),
                           (t4, "telemetry", "telemetry")):
        with tab:
            if ev[key].empty:
                ui.empty_state(f"No {name} records in the evidence window.")
            else:
                ui.show_table(ev[key], height=260)
                ui.download(ev[key], f"azure_m{mid}_{name}_evidence.csv", key=f"m4_ev_{key}")

with st.expander("Methodology, limitations and distribution review"):
    st.markdown("""
- **Failure / error frequency**: counts in the evidence window, min-max scaled across machines.
- **Failure / error recency**: `max(0, 1 - days_since_last_event / look-back)`; 0 when no event is recorded.
- **Telemetry deviation**: share of readings in the last telemetry-window days outside the fleet baseline band
  (mean +/- k x std of each sensor); the four sensor shares are averaged so correlated sensors are not summed.
- **Planned-maintenance burden**: maintenance events that are *not* the repair of a recorded failure (so failures are
  not counted twice), min-max scaled.
- **Time since last maintenance**: days since the latest maintenance in the window (full window if none), min-max scaled.
- **Machine age**: included because age correlates with failure count in this dataset (see Module 02, Model & age).

**Limitations.** The score summarises *historical recorded events*; it has not been validated as a predictor of future
failures. Min-max scaling is relative to the current fleet, so scores are comparable within a run, not across datasets or
time windows. Components are partly correlated (failures, errors and recency), which weights cannot fully remove.
""")
