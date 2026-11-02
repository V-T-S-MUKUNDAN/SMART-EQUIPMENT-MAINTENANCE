"""Azure SQL correctness: every result is checked against an independent pandas calculation."""
import pandas as pd
import pytest

from equipment import db
from equipment.azure import analytics as A

FULL = A.Filters("2015-01-01", "2015-12-31")


def ts_between(df, col, f):
    return df[(df[col] >= f.start) & (df[col] < f.end_excl)]


def _fl(frames):
    return (frames["failures"].rename(columns={"datetime": "ts", "machineID": "machine_id", "failure": "component"}),
            frames["errors"].rename(columns={"datetime": "ts", "machineID": "machine_id", "errorID": "error_type"}),
            frames["maint"].rename(columns={"datetime": "ts", "machineID": "machine_id", "comp": "component"}))


# ------------------------------------------------------------------ join fan-out
def test_naive_join_inflates_counts_but_views_do_not(conn, frames):
    real_failures = len(frames["failures"])
    naive = conn.execute("""SELECT COUNT(*) FROM azure_machines m JOIN azure_failures f USING (machine_id)
                            JOIN azure_errors e USING (machine_id) JOIN azure_maintenance x USING (machine_id)""").fetchone()[0]
    assert naive > 10 * real_failures                              # demonstrates the trap (1 failure becomes many rows)

    s = db.read_df(conn, "SELECT * FROM v_azure_machine_summary ORDER BY machine_id")
    assert s["failure_count"].sum() == real_failures
    assert s["error_count"].sum() == len(frames["errors"])
    assert s["maintenance_count"].sum() == len(frames["maint"])
    assert len(s) == 3                                              # still one row per machine


def test_machine_summary_matches_pandas(conn, frames):
    f, e, m = _fl(frames)
    exp = pd.DataFrame({"machine_id": [1, 2, 3]})
    exp["failure_count"] = exp.machine_id.map(f.groupby("machine_id").size()).fillna(0).astype(int)
    exp["error_count"] = exp.machine_id.map(e.groupby("machine_id").size()).fillna(0).astype(int)
    exp["maintenance_count"] = exp.machine_id.map(m.groupby("machine_id").size()).fillna(0).astype(int)
    got = db.read_df(conn, "SELECT * FROM v_azure_machine_summary ORDER BY machine_id")
    pd.testing.assert_frame_equal(got[exp.columns], exp, check_dtype=False)


def test_every_analytic_query_conserves_totals(conn, frames):
    """No query may report more failures/errors than exist (join fan-out detector)."""
    nf, ne = len(frames["failures"]), len(frames["errors"])
    assert A.query(conn, "failures_by_machine", FULL)["failures"].sum() == nf
    assert A.query(conn, "failures_by_component", FULL)["failures"].sum() == nf
    assert A.query(conn, "failure_matrix", FULL)["total"].sum() == nf
    assert A.query(conn, "failure_trend", FULL)["failures"].sum() == nf
    assert A.query(conn, "model_comparison", FULL)["failures"].sum() == nf
    assert A.query(conn, "age_band_comparison", FULL)["failures"].sum() == nf
    fb = A.query(conn, "frequent_both", FULL)
    assert fb["failures"].sum() == nf and fb["errors"].sum() == ne and len(fb) == 3
    assert A.query(conn, "errors_by_type", FULL)["errors"].sum() == ne
    assert A.query(conn, "errors_by_machine", FULL)["errors"].sum() == ne
    assert A.query(conn, "error_matrix", FULL)["total"].sum() == ne
    assert A.query(conn, "model_comparison", FULL)["errors"].sum() == ne
    assert A.query(conn, "errors_before_failures", FULL, hours=72)["failures"].sum() == nf


# ------------------------------------------------------------------ failure & error counts
def test_kpis_match_pandas(conn, frames):
    k = A.kpis(conn, FULL)
    assert k["machines"] == 3 and k["failures"] == 4 and k["errors"] == 6 and k["maintenance_events"] == 10
    assert k["telemetry_records"] == len(frames["telemetry"])


def test_failures_by_machine_and_component(conn, frames):
    f, _, _ = _fl(frames)
    got = A.query(conn, "failures_by_machine", FULL).set_index("machine_id")["failures"].to_dict()
    assert got == {1: 3, 2: 1, 3: 0}
    comp = A.query(conn, "failures_by_component", FULL).set_index("component")
    assert comp["failures"].to_dict() == f.groupby("component").size().to_dict()
    assert comp.loc["comp1", "machines_affected"] == 1
    assert abs(comp["share_pct"].sum() - 100) < 0.2
    mat = A.query(conn, "failure_matrix", FULL).set_index("machine_id")
    assert mat.loc[1, ["comp1", "comp2", "comp3", "comp4"]].tolist() == [2, 1, 0, 0]


def test_error_counts(conn, frames):
    _, e, _ = _fl(frames)
    by_type = A.query(conn, "errors_by_type", FULL).set_index("error_type")["errors"].to_dict()
    assert by_type == e.groupby("error_type").size().to_dict()
    by_m = A.query(conn, "errors_by_machine", FULL).set_index("machine_id")["errors"].to_dict()
    assert by_m == e.groupby("machine_id").size().to_dict()


# ------------------------------------------------------------------ timestamp filtering
def test_date_range_is_inclusive_of_end_day_and_exclusive_after(conn, frames):
    f, e, m = _fl(frames)
    for start, end in [("2015-01-10", "2015-01-10"), ("2015-01-11", "2015-02-10"), ("2015-02-10", "2015-02-10"),
                       ("2015-02-11", "2015-12-31")]:
        flt = A.Filters(start, end)
        k = A.kpis(conn, flt)
        assert k["failures"] == len(ts_between(f, "ts", flt)), (start, end)
        assert k["errors"] == len(ts_between(e, "ts", flt)), (start, end)
        assert k["maintenance_events"] == len(ts_between(m, "ts", flt)), (start, end)
        tel = ts_between(frames["telemetry"].rename(columns={"datetime": "ts"}), "ts", flt)
        assert k["telemetry_records"] == len(tel), (start, end)


def test_machine_and_component_filters(conn, frames):
    f, e, m = _fl(frames)
    flt = A.Filters("2015-01-01", "2015-12-31", machines=(1,), components=("comp1",))
    k = A.kpis(conn, flt)
    assert k["machines"] == 1
    assert k["failures"] == len(f[(f.machine_id == 1) & (f.component == "comp1")]) == 2
    assert k["errors"] == len(e[e.machine_id == 1])                # component filter does not apply to errors
    assert k["maintenance_events"] == len(m[(m.machine_id == 1) & (m.component == "comp1")])
    assert k["telemetry_records"] == len(frames["telemetry"][frames["telemetry"].machineID == 1])


def test_empty_selection_returns_empty_results_not_everything(conn):
    flt = A.Filters("2015-01-01", "2015-12-31", machines=())
    k = A.kpis(conn, flt)
    assert (k["machines"], k["failures"], k["errors"], k["telemetry_records"]) == (0, 0, 0, 0)
    assert A.query(conn, "failures_by_machine", flt).empty
    out_of_range = A.Filters("2020-01-01", "2020-12-31")
    assert A.kpis(conn, out_of_range)["failures"] == 0


# ------------------------------------------------------------------ SQL injection
def test_filter_values_cannot_inject_sql(azure_db):
    # machine ids are coerced to int: an injection string is rejected before reaching SQL
    with pytest.raises(ValueError):
        A.Filters("2015-01-01", "2015-12-31", machines=("1); DROP TABLE azure_machines;--",))
    c = db.connect(azure_db, read_only=True)
    evil = A.Filters("2015-01-01", "2015-12-31", components=("comp1' OR '1'='1", "x); DROP TABLE azure_failures;--"))
    assert A.kpis(c, evil)["failures"] == 0                         # treated as literal (non-matching) strings
    c.close()
    c = db.connect(azure_db, read_only=True)
    assert c.execute("SELECT COUNT(*) FROM azure_failures").fetchone()[0] == 4
    c.close()


# ------------------------------------------------------------------ window functions / CTEs
def test_failure_trend_has_gap_free_months_and_correct_windows(conn, frames):
    f, _, _ = _fl(frames)
    tr = A.query(conn, "failure_trend", FULL)
    months = pd.period_range("2015-01", "2015-12", freq="M").astype(str).tolist()
    assert tr["month"].tolist() == months                           # recursive CTE: all 12 months, even empty ones
    exp = f.assign(month=f.ts.str[:7]).groupby("month").size().reindex(months, fill_value=0)
    assert tr["failures"].tolist() == exp.tolist()
    assert tr["cumulative_failures"].tolist() == exp.cumsum().tolist()
    assert tr["rolling_3m_avg"].round(2).tolist() == exp.rolling(3, min_periods=1).mean().round(2).tolist()
    assert tr["mom_change"].dropna().tolist() == exp.diff().dropna().tolist()


def test_repeated_failures_use_lag_correctly(conn, frames):
    rep = A.query(conn, "repeated_failures", FULL)
    assert len(rep) == 1                                           # only machine 1 / comp1 failed twice
    r = rep.iloc[0]
    assert (r.machine_id, r.component, r.failures) == (1, "comp1", 2)
    assert r.min_gap_days == r.avg_gap_days == 31.0                # 2015-01-10 -> 2015-02-10


def test_repeated_errors_within_window(conn):
    rep = A.query(conn, "repeated_errors", FULL, repeat_hours=24 * 40)
    assert rep.loc[(rep.machine_id == 1) & (rep.error_type == "error1"), "repeats_within_window"].iloc[0] == 1
    assert A.query(conn, "repeated_errors", FULL, repeat_hours=1).empty


def test_errors_before_failures_correlated_subquery(conn, frames):
    f, e, _ = _fl(frames)
    got = A.query(conn, "errors_before_failures", FULL, hours=72).set_index("component")
    # independent pandas computation
    exp = {}
    for _, row in f.iterrows():
        t = pd.Timestamp(row.ts)
        hit = ((e.machine_id == row.machine_id) & (pd.to_datetime(e.ts) <= t) & (pd.to_datetime(e.ts) >= t - pd.Timedelta(hours=72))).any()
        exp.setdefault(row.component, [0, 0])
        exp[row.component][0] += 1
        exp[row.component][1] += int(hit)
    assert {c: (int(r.failures), int(r.preceded_by_error)) for c, r in got.iterrows()} == {c: tuple(v) for c, v in exp.items()}


def test_frequent_both_flags_match_pandas(conn, frames):
    f, e, _ = _fl(frames)
    fc = f.groupby("machine_id").size().reindex([1, 2, 3], fill_value=0)
    ec = e.groupby("machine_id").size().reindex([1, 2, 3], fill_value=0)
    exp = ((fc > fc.mean()) & (ec > ec.mean())).astype(int)
    got = A.query(conn, "frequent_both", FULL).set_index("machine_id")["both_above_average"]
    assert got.sort_index().tolist() == exp.sort_index().tolist() == [1, 0, 0]


def test_model_and_age_comparisons(conn, frames):
    mc = A.query(conn, "model_comparison", FULL).set_index("model")
    assert mc.loc["model1", "machines"] == 2 and mc.loc["model1", "failures"] == 3
    assert mc.loc["model1", "failures_per_machine"] == 1.5 and mc.loc["model2", "failures_per_machine"] == 1.0
    ab = A.query(conn, "age_band_comparison", FULL).set_index("age_band")
    assert ab["machines"].to_dict() == {"5-9 yrs": 1, "10-14 yrs": 1, "15+ yrs": 1}
    corr = A.query(conn, "age_correlation", FULL).iloc[0]
    ages = pd.Series([5, 10, 15])
    fails = pd.Series([3, 1, 0])
    assert corr["corr_age_failures"] == pytest.approx(ages.corr(fails), abs=1e-9)


# ------------------------------------------------------------------ maintenance split
def test_planned_vs_failure_linked_maintenance_matches_pandas(conn, frames):
    f, _, m = _fl(frames)
    merged = m.merge(f[["machine_id", "ts", "component"]].assign(linked=1), on=["machine_id", "ts", "component"], how="left")
    assert len(merged) == len(m)                                   # pandas merge is also 1:1
    exp = merged.assign(linked=merged.linked.fillna(0)).groupby("machine_id").agg(
        total=("linked", "size"), linked=("linked", "sum"))
    got = db.read_df(conn, "SELECT * FROM v_azure_machine_maintenance").set_index("machine_id")
    assert got["maintenance_count"].to_dict() == exp["total"].to_dict()
    assert got["failure_linked_maintenance_count"].to_dict() == exp["linked"].astype(int).to_dict()
    assert (got["planned_maintenance_count"] + got["failure_linked_maintenance_count"] == got["maintenance_count"]).all()
    mc = A.query(conn, "maintenance_by_component", FULL)
    assert mc["events"].sum() == len(m)
    one = A.Filters(FULL.start, FULL.end, (1,), ("comp1",))        # filter columns must not be ambiguous in the join
    mc1 = A.query(conn, "maintenance_by_component", one)
    assert mc1["events"].sum() == len(m[(m.machine_id == 1) & (m.component == "comp1")])


def test_machine_timeline_is_union_not_join(conn):
    tl = A.query(conn, "machine_timeline", FULL, machine_id=1)
    assert len(tl) == 3 + 3 + 5                                     # failures + errors + maintenance of machine 1
    assert tl["event_type"].value_counts().to_dict() == {"failure": 3, "error": 3, "maintenance": 5}


# ------------------------------------------------------------------ real data
@pytest.mark.real_data
def test_real_data_totals_and_no_fan_out(real_db):
    c = db.connect(real_db, read_only=True)
    f = A.Filters(*A.data_bounds(c))                               # full range: maintenance predates telemetry
    k = A.kpis(c, f)
    assert (k["machines"], k["telemetry_records"], k["failures"], k["errors"], k["maintenance_events"]) == \
        (100, 876_100, 761, 3_919, 3_286)
    s = db.read_df(c, "SELECT * FROM v_azure_machine_summary")
    assert len(s) == 100 and s.failure_count.sum() == 761 and s.error_count.sum() == 3919 and s.maintenance_count.sum() == 3286
    assert A.query(c, "frequent_both", f)["failures"].sum() == 761
    assert A.query(c, "failure_trend", f)["failures"].sum() == 761
    c.close()
