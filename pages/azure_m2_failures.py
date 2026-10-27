import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from equipment import ui

ui.guard("azure")
ui.banner("azure", "Module 02 - Equipment Failure & Error Analytics")
ui.require_ready(ui.AZURE_TABLES)
f = ui.azure_filters()
ui.note("Everything on this page describes <b>recorded historical events</b> in the selected range. "
        "Nothing here is a prediction of future failures.")

k = ui.az_q("kpis", f).iloc[0]
ui.kpi_row([("Recorded failures", ui.fmt_int(k["failures"]), None), ("Error events", ui.fmt_int(k["errors"]), None),
            ("Machines", ui.fmt_int(k["machines"]), None),
            ("Failures per machine", "n/a" if not k["machines"] else f"{k['failures'] / k['machines']:.2f}", None)])
if k["machines"] == 0:
    ui.empty_state()
    st.stop()

tabs = st.tabs(["Failures", "Errors", "Repeated patterns", "Trends", "Failures + errors", "Model & age", "Machine drill-down"])

with tabs[0]:
    by_comp = ui.az_q("failures_by_component", f)
    by_mach = ui.az_q("failures_by_machine", f)
    c1, c2 = st.columns(2)
    with c1:
        if by_comp.empty:
            ui.empty_state("No failures in the selected range.")
        else:
            ui.show_fig(px.bar(by_comp, x="component", y="failures", text="failures", title="Failures by component"),
                        key="m2_comp")
            ui.show_table(by_comp)
    with c2:
        n_top = st.slider("Most failure-prone machines to show", 5, 30, 10, key="m2_topn")
        top = by_mach[by_mach["failures"] > 0].head(n_top)
        if top.empty:
            ui.empty_state()
        else:
            fig = px.bar(top.assign(machine=top["machine_id"].astype(str)).sort_values("failures"), x="failures",
                         y="machine", color="model", orientation="h", title=f"Top {n_top} machines by failures")
            fig.update_yaxes(type="category", title="Machine ID")
            ui.show_fig(fig, height=max(320, 22 * n_top), key="m2_top")
    st.markdown("**Failures by machine and component** (conditional aggregation)")
    matrix = ui.az_q("failure_matrix", f)
    if matrix.empty:
        ui.empty_state()
    else:
        ui.show_table(matrix, height=300)
        ui.download(matrix, "azure_failures_by_machine_component.csv", key="m2_dl_matrix")
    st.markdown("**All machines ranked by failures**")
    ui.show_table(by_mach, height=300)
    ui.download(by_mach, "azure_failures_by_machine.csv", key="m2_dl_byM")

with tabs[1]:
    et = ui.az_q("errors_by_type", f)
    em = ui.az_q("errors_by_machine", f)
    c1, c2 = st.columns(2)
    with c1:
        if et.empty:
            ui.empty_state("No errors in the selected range.")
        else:
            ui.show_fig(px.bar(et, x="error_type", y="errors", text="errors", title="Error frequency by type"), key="m2_et")
            ui.show_table(et)
    with c2:
        if not em.empty:
            top = em.head(15)
            fig = px.bar(top.assign(machine=top["machine_id"].astype(str)).sort_values("errors"), x="errors",
                         y="machine", orientation="h", title="Machines with the most errors (top 15)")
            fig.update_yaxes(type="category", title="Machine ID")
            ui.show_fig(fig, height=420, key="m2_em")
    mat = ui.az_q("error_matrix", f)
    if not mat.empty:
        st.markdown("**Errors by machine and type**")
        ui.show_table(mat, height=300)
        ui.download(mat, "azure_errors_by_machine_type.csv", key="m2_dl_emat")

with tabs[2]:
    st.markdown("**Repeated failures** - the same component failing again on the same machine "
                "(gap measured with `LAG()` over each machine/component history).")
    rf = ui.az_q("repeated_failures", f)
    if rf.empty:
        ui.empty_state("No component failed more than once in the selected range.")
    else:
        ui.show_table(rf, height=320)
        ui.download(rf, "azure_repeated_failures.csv", key="m2_dl_rf")
    st.markdown("**Repeated errors** - the same error type recurring on the same machine within a short interval.")
    hrs = st.slider("Repeat window (hours)", 1, 72, 24, key="m2_rep_hours")
    re_ = ui.az_q("repeated_errors", f, repeat_hours=hrs)
    if re_.empty:
        ui.empty_state(f"No error type repeated within {hrs} hours on the same machine.")
    else:
        ui.show_table(re_, height=320)
        ui.download(re_, "azure_repeated_errors.csv", key="m2_dl_re")
    st.markdown("**Errors shortly before failures** (correlated subquery)")
    h2 = st.slider("Look-back window before a failure (hours)", 6, 168, 72, key="m2_ebf_hours")
    ebf = ui.az_q("errors_before_failures", f, hours=h2)
    if ebf.empty:
        ui.empty_state()
    else:
        ui.show_table(ebf)
        ref = ebf["uniform_expected_errors_per_window"].iloc[0]
        st.caption(f"Descriptive only. For reference, if each machine's errors were spread evenly in time, about {ref:.2f} "
                   f"errors would be expected in any {h2}-hour window. A pattern of errors before failures is an "
                   "association in recorded events, not proof that errors cause failures.")

with tabs[3]:
    tr = ui.az_q("failure_trend", f)
    if tr["failures"].sum() == 0:
        ui.empty_state("No failures in the selected range.")
    else:
        fig = go.Figure()
        fig.add_bar(x=tr["month"], y=tr["failures"], name="Failures")
        fig.add_scatter(x=tr["month"], y=tr["rolling_3m_avg"], mode="lines+markers", name="3-month rolling average")
        fig.add_scatter(x=tr["month"], y=tr["cumulative_failures"], mode="lines", name="Cumulative", yaxis="y2")
        fig.update_layout(title="Monthly failures (window functions: LAG, running SUM, rolling AVG)",
                          yaxis2=dict(overlaying="y", side="right", title="Cumulative"))
        ui.show_fig(fig, key="m2_trend")
        ui.show_table(tr)
        ui.download(tr, "azure_failure_trend.csv", key="m2_dl_tr")
        st.caption("Months with no recorded failures appear as zero. The maintenance log starts in mid-2014, "
                   "but failure and telemetry records begin in January 2015.")
        tc = ui.az_q("failure_trend_by_component", f)
        if not tc.empty:
            ui.show_fig(px.bar(tc, x="month", y="failures", color="component", title="Monthly failures by component"),
                        key="m2_trend_comp")

with tabs[4]:
    st.markdown("Machines **above the fleet average for both failures and errors** in the selected range "
                "(averages are computed over all machines, so the machine filter does not move the thresholds).")
    fb = ui.az_q("frequent_both", f)
    both = fb[fb["both_above_average"] == 1]
    if both.empty:
        ui.empty_state("No machine is above average on both measures for the current selection.")
    else:
        st.success(f"{len(both)} machine(s) exceed the fleet average on both failures and errors.")
        ui.show_table(both, height=300)
    fig = px.scatter(fb, x="errors", y="failures", color=fb["both_above_average"].map({1: "Above average on both", 0: "Other"}),
                     hover_data=["machine_id", "model", "age"], title="Errors vs failures per machine")
    fig.add_vline(x=fb["fleet_avg_errors"].iloc[0], line_dash="dot")
    fig.add_hline(y=fb["fleet_avg_failures"].iloc[0], line_dash="dot")
    ui.show_fig(fig, key="m2_both")
    ui.download(fb, "azure_failures_and_errors_by_machine.csv", key="m2_dl_fb")

with tabs[5]:
    mc = ui.az_q("model_comparison", f)
    ab = ui.az_q("age_band_comparison", f)
    corr = ui.az_q("age_correlation", f).iloc[0]
    c1, c2 = st.columns(2)
    with c1:
        if not mc.empty:
            ui.show_fig(px.bar(mc, x="model", y="failures_per_machine", text="failures_per_machine",
                               title="Failures per machine by model"), key="m2_model")
            ui.show_table(mc)
    with c2:
        if not ab.empty:
            ui.show_fig(px.bar(ab, x="age_band", y="failures_per_machine", text="failures_per_machine",
                               title="Failures per machine by age band"), key="m2_age")
            ui.show_table(ab)
    if corr["machines"] and corr["corr_age_failures"] == corr["corr_age_failures"]:
        st.caption(f"Pearson correlation (computed in SQL) across {int(corr['machines'])} machines: age vs failures = "
                   f"{corr['corr_age_failures']:.2f}; age vs errors = {corr['corr_age_errors']:.2f}. "
                   "Correlation describes association; model and age are confounded with other machine differences.")

with tabs[6]:
    machines = ui.az_q("machine_list")
    options = f.machines if f.machines else tuple(machines["machine_id"])
    mid = st.selectbox("Machine", options, key="m2_machine")
    tl = ui.az_q("machine_timeline", f, machine_id=int(mid))
    info = machines[machines.machine_id == mid].iloc[0]
    st.write(f"**Machine {mid}** - {info['model']}, age {info['age']} years")
    if tl.empty:
        ui.empty_state("No failures, errors or maintenance events for this machine in the selected range.")
    else:
        fig = px.scatter(tl, x="ts", y="event_type", color="detail", title="Event timeline (failures, errors, maintenance)")
        ui.show_fig(fig, key="m2_tl")
        ui.show_table(tl.sort_values("ts", ascending=False), height=260)
        ui.download(tl, f"azure_machine_{mid}_events.csv", key="m2_dl_tl")
    td = ui.az_q("machine_telemetry_daily", f, machine_id=int(mid))
    if td.empty:
        ui.empty_state("No telemetry for this machine in the selected range.")
    else:
        long = td.melt(id_vars=["day"], value_vars=["volt", "rotate", "pressure", "vibration"], var_name="sensor")
        fig = px.line(long, x="day", y="value", facet_row="sensor", title="Daily mean telemetry")
        fig.update_yaxes(matches=None)
        ui.show_fig(fig, height=620, key="m2_td")
