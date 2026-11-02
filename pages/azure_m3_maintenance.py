import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from equipment import ui
from equipment.azure import maintenance as M

ui.guard("azure")
ui.banner("azure", "Module 03 - Maintenance Effectiveness Analysis")
ui.require_ready(ui.AZURE_TABLES)
f = ui.azure_filters()
ui.note(M.LIMITATIONS)

machines = ui.az_q("machine_list")
choices = list(f.machines) if f.machines else machines["machine_id"].tolist()
c1, c2, c3 = st.columns([1, 2, 1])
mid = c1.selectbox("Machine", choices, key="m3_machine")
ev = ui.cached_call("equipment.azure.maintenance.events", int(mid))
if f.components:
    ev = ev[ev["component"].isin(f.components)]
ev = ev[(ev["ts"] >= f.start) & (ev["ts"] < f.end_excl)]
if ev.empty:
    ui.empty_state("This machine has no maintenance events in the selected date range / component filter.")
    st.stop()
ev = ev.assign(label=ev["ts"] + "  |  " + ev["component"] + "  |  " + ev["kind"])
choice = c2.selectbox("Maintenance event", ev["label"].tolist(), key="m3_event")
window = c3.select_slider("Comparison window (days)", options=[1, 2, 3, 5, 7, 10, 14, 21, 30], value=7, key="m3_window")
min_obs = st.slider("Minimum readings required in each window", 6, 120, 24, key="m3_minobs",
                    help="Results are marked insufficient below this number of hourly readings.")
sel = ev[ev["label"] == choice].iloc[0]

res = ui.cached_call("equipment.azure.maintenance.compare", int(mid), sel["ts"], int(window), int(min_obs), sel["component"])
st.markdown(f"**Machine {mid}** - {sel['kind']} maintenance of **{sel['component']}** at **{sel['ts']}**. "
            f"Before = {window} days before; after = {window} days after (the reading at the event time is excluded).")

k1, k2, k3 = st.columns(3)
k1.metric("Observations before", res["n_before"])
k2.metric("Observations after", res["n_after"])
k3.metric("Sufficient data", "Yes" if res["sufficient"] else "No")
for m in res["messages"]:
    st.warning(m)
if res["table"].empty:
    ui.empty_state("No telemetry readings fall in either window for this event.")
    st.stop()

t = res["table"]
summary = pd.DataFrame({
    "Metric": t["metric"],
    "Before mean": t["before_mean"], "After mean": t["after_mean"], "Change in mean": t["change_mean"],
    "% change (mean)": t["pct_change_mean"],
    "Before median": t["before_median"], "After median": t["after_median"], "Change in median": t["change_median"],
    "Before min": t["before_min"], "After min": t["after_min"],
    "Before max": t["before_max"], "After max": t["after_max"],
    "Before std": t["before_std"], "After std": t["after_std"], "Change in std": t["change_std"],
    "% change (std)": t["pct_change_std"],
    "n before": t["before_n_obs"], "n after": t["after_n_obs"],
}).round(3)
ui.show_table(summary.style.format(na_rep="n/a (undefined)", precision=3))
ui.download(summary, f"azure_maintenance_before_after_m{mid}.csv", key="m3_dl")
st.caption("Percentage change is 'n/a' when the baseline (before) value is missing or zero. Std = sample standard deviation.")

# visual: before vs after means and distributions
raw = ui.cached_call("equipment.azure.maintenance.readings", int(mid), sel["ts"], int(window))
c1, c2 = st.columns(2)
with c1:
    fig = go.Figure()
    fig.add_bar(x=t["metric"], y=t["before_mean"], name="Before")
    fig.add_bar(x=t["metric"], y=t["after_mean"], name="After")
    fig.update_layout(barmode="group", title="Mean telemetry before vs after")
    ui.show_fig(fig, key="m3_means")
with c2:
    if not raw.empty:
        metric = st.selectbox("Sensor for distribution", M.METRICS, key="m3_metric")
        fig = px.box(raw, x="period", y=metric, color="period", category_orders={"period": ["before", "after"]},
                     points="outliers", title=f"{metric}: distribution before vs after")
        ui.show_fig(fig, key="m3_box")
if not raw.empty:
    metric_ts = st.selectbox("Sensor for time series", M.METRICS, key="m3_metric_ts")
    fig = px.line(raw, x="ts", y=metric_ts, color="period", title=f"{metric_ts} around the maintenance event")
    fig.add_vline(x=sel["ts"], line_dash="dash", line_color="#c0392b")
    ui.show_fig(fig, key="m3_ts")

st.subheader("Other events inside the comparison windows (potential confounders)")
conf = res["confounders"]
if conf.empty:
    st.success("No other maintenance, failure or error events were recorded for this machine in either window.")
else:
    ui.show_table(conf)
    st.caption("Other events in the same windows make it harder to attribute any change to this maintenance. "
               "'at event' means the same timestamp as the selected event; a failure-linked repair lists its own failure here.")

st.subheader("Fleet-wide observed change by maintained component")
st.caption("Average (after - before) of window means across all maintenance events with enough readings on both sides, "
           "using the window length and minimum above. Observed averages only; no causal claim.")
if st.toggle("Compute fleet-wide summary (about 1 second)", key="m3_fleet"):
    comp = ui.cached_call("equipment.azure.maintenance.component_summary", int(window), int(min_obs))
    ui.show_table(comp)
    ui.download(comp, "azure_maintenance_component_effect.csv", key="m3_dl_comp")
