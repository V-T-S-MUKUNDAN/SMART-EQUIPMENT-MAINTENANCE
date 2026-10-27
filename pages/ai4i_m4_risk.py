import pandas as pd
import plotly.express as px
import streamlit as st

from equipment import charts, config, ui
from equipment.ai4i import risk as R

ui.guard("ai4i")
ui.banner("ai4i", "Module 04 - Operating-Condition Risk Indicator")
ui.require_ready(["ai4i_machine_data"])
f = ui.ai4i_filters()
base_cfg = R.RiskConfig.default()
cfg = ui.ai4i_cfg()
ui.note(R.DISCLAIMER)

with st.expander("Scoring formula, weights and thresholds (configurable)"):
    st.markdown("""
Four factors, each scaled to 0-1 from **operating conditions only** (the failure labels are never inputs):

| Factor | 0-1 value | Reference (published dataset description) |
|---|---|---|
| Tool wear | `min(1, tool_wear / 240)` | tool-wear failures occur at 200-240 min |
| Overstrain | `min(1, tool_wear x torque / limit)` | limit 11000 / 12000 / 13000 minNm for type L / M / H |
| Power | `min(1, abs(power - 6250) / 2750)` with power = torque x rpm x 2pi/60 | safe band 3500-9000 W |
| Heat dissipation | `r_diff x r_speed` (0 at the dataset-average value, 1 at the limit) | temperature difference 8.6 K and 1380 rpm |

`score = 100 x sum(weight_i x factor_i) / sum(weight_i)`. Contributions add up exactly to the score.
""")
    cols = st.columns(4)
    new_w = {c: cols[i].number_input(cfg.labels[c][:34], 0.0, 100.0, float(cfg.weights[c]), 1.0, key=f"aw_{c}")
             for i, c in enumerate(R.COMPONENTS)}
    tc = st.columns(3)
    t_med = tc[0].number_input("Medium from", 0.0, 100.0, float(cfg.thresholds["medium"]), key="at_med")
    t_high = tc[1].number_input("High from", 0.0, 100.0, float(cfg.thresholds["high"]), key="at_high")
    t_crit = tc[2].number_input("Critical from", 0.0, 100.0, float(cfg.thresholds["critical"]), key="at_crit")
    b1, b2 = st.columns(2)
    if b1.button("Apply settings", key="ai_apply"):
        trial = cfg.copy()
        trial.weights, trial.thresholds = new_w, {"medium": t_med, "high": t_high, "critical": t_crit}
        try:
            trial.validate()
            st.session_state["ai_cfg"] = trial
            st.rerun()
        except ValueError as exc:
            st.error(f"Invalid settings: {exc}")
    if b2.button("Reset to defaults (config/ai4i_risk.json)", key="ai_reset"):
        for k in [k for k in st.session_state if k.startswith(("aw_", "at_"))]:
            del st.session_state[k]
        st.session_state["ai_cfg"] = base_cfg
        st.rerun()
    if cfg.key() != base_cfg.key():
        st.warning("Custom settings are active for this session.")

risk = ui.ai_risk_scores(f, cfg)
if risk.empty:
    ui.empty_state()
    st.stop()
s = R.summary(risk)
ui.kpi_row([("Records scored", ui.fmt_int(s["records"]), None), ("Average", f"{s['avg_score']:.2f}", None),
            ("Median", f"{s['median']:.2f}", None), ("Highest", f"{s['max']:.2f}", None),
            ("High", ui.fmt_int(s["by_category"]["High"]), None), ("Critical", ui.fmt_int(s["by_category"]["Critical"]), None)])

c1, c2 = st.columns(2)
with c1:
    ui.show_fig(charts.category_bar(s["by_category"], "Records per risk category", "records"), key="a4_cat")
with c2:
    ui.show_fig(charts.histogram(risk, "risk_score", "Indicator distribution", cfg.thresholds, 40), key="a4_hist")

st.subheader("Do the thresholds make sense on observed data?")
st.caption("Evaluation only: the recorded failure labels are used *after* scoring to describe how the indicator relates to "
           "recorded failures in this same dataset. This is in-sample and descriptive - not a validated prediction.")
cv = ui.ai_eval("category_validation", f, cfg)
c1, c2 = st.columns(2)
with c1:
    ui.show_table(cv)
    fig = px.bar(cv, x="risk_category", y="observed_failure_rate_pct", text="observed_failure_rate_pct",
                 color="risk_category", color_discrete_map=config.RISK_COLORS, title="Observed failure rate by category (%)")
    fig.update_layout(showlegend=False)
    ui.show_fig(fig, height=300, key="a4_cv")
with c2:
    dv = ui.ai_eval("decile_validation", f, cfg)
    fig = px.bar(dv, x="decile", y="failure_rate_pct", text="failure_rate_pct", title="Observed failure rate by score decile (%)")
    ui.show_fig(fig, height=300, key="a4_dec")
rs = ui.ai_eval("rank_separation", f, cfg)
if not rs.empty and rs["auc"].iloc[0] == rs["auc"].iloc[0] and rs["auc"].iloc[0] is not None:
    st.metric("Rank separation (AUC, in-sample)", f"{rs['auc'].iloc[0]:.3f}",
              help="Probability that a randomly chosen failed record scores higher than a randomly chosen non-failed record "
                   "(0.5 = no separation). Descriptive on this dataset only.")
st.caption("AI4I labels are synthetic and generated from rules close to the ones used for the factors, so good separation "
           "reflects how the data were built and does not demonstrate performance on real equipment.")
with st.expander("Threshold sweep: share of records flagged vs share of recorded failures captured"):
    ts = ui.ai_eval("threshold_sweep", f, cfg)
    ui.show_table(ts)
    ui.download(ts, "ai4i_risk_threshold_sweep.csv", key="a4_dl_ts")

st.subheader("Records ranked by indicator")
cats = st.multiselect("Category", config.RISK_CATEGORIES, default=config.RISK_CATEGORIES, key="a4_cats")
view = risk[risk["risk_category"].isin(cats)]
cols = ["udi", "product_id", "machine_type", "risk_score", "risk_category", "tool_wear_min", "torque_nm",
        "rotational_speed_rpm", "temp_diff_k", "power_w", "c_tool_wear", "c_strain", "c_power", "c_heat"]
ui.show_table(view[cols].round(2), height=320, column_config={
    "risk_score": st.column_config.ProgressColumn("risk_score", min_value=0, max_value=100, format="%.2f")})
ui.download(view.drop(columns=["machine_failure", "twf", "hdf", "pwf", "osf", "rnf"]).round(3),
            "ai4i_risk_scores.csv", "Download scored records (CSV, without labels)", key="a4_dl")

st.subheader("Why does a record get its score?")
udi = st.number_input("UDI", int(risk["udi"].min()), int(risk["udi"].max()), int(risk["udi"].iloc[0]), key="a4_udi")
hit = risk[risk["udi"] == udi]
if hit.empty:
    ui.empty_state("That UDI is not in the current filter selection.")
else:
    r = hit.iloc[0]
    st.markdown(f"**UDI {udi}** (Product {r['product_id']}, type {r['machine_type']}): indicator **{r['risk_score']:.2f}** - "
                f"**{r['risk_category']}**")
    st.write(R.explain(r, cfg, top=4))
    contrib = pd.DataFrame({"udi": [str(udi)] * 4, "component": R.COMPONENTS, "points": [r[f"c_{c}"] for c in R.COMPONENTS]})
    ui.show_fig(charts.contribution_bar(contrib, cfg.labels, y="udi"), height=200, key="a4_contrib")
    ui.show_table(pd.DataFrame({
        "Factor": [cfg.labels[c] for c in R.COMPONENTS],
        "Normalised (0-1)": [round(r[f"f_{c}"], 3) for c in R.COMPONENTS],
        "Weight": [cfg.weights[c] for c in R.COMPONENTS],
        "Points": [round(r[f"c_{c}"], 2) for c in R.COMPONENTS]}))
    with st.expander("Source record (traceability)"):
        ui.show_table(pd.DataFrame([r[["udi", "product_id", "machine_type", "air_temp_k", "process_temp_k",
                                       "rotational_speed_rpm", "torque_nm", "tool_wear_min", "temp_diff_k",
                                       "power_w"]]]).round(2))
        st.caption("Recorded labels for this record (not used in the score): "
                   f"Machine failure={int(r['machine_failure'])}, TWF={int(r['twf'])}, HDF={int(r['hdf'])}, "
                   f"PWF={int(r['pwf'])}, OSF={int(r['osf'])}, RNF={int(r['rnf'])}")
