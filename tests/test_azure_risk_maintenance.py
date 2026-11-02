"""Azure risk scoring (Module 04/05) and before/after maintenance comparison (Module 03)."""
import math

import numpy as np
import pandas as pd
import pytest

from equipment import db
from equipment.azure import analytics as A
from equipment.azure import maintenance as M
from equipment.azure import priority as P
from equipment.azure import risk as R

FULL = A.Filters("2015-01-01", "2015-12-31")


def cfg_with(**weights):
    c = R.RiskConfig.default()
    c.weights = {k: 0.0 for k in R.COMPONENTS}
    c.weights.update(weights)
    return c


# ------------------------------------------------------------------ category boundaries
@pytest.mark.parametrize("score,expected", [
    (0, "Low"), (24.99, "Low"), (25, "Medium"), (49.99, "Medium"), (50, "High"), (74.99, "High"),
    (75, "Critical"), (100, "Critical"), (24.994, "Low"), (24.995, "Medium")])
def test_category_boundaries(score, expected):
    assert R.category_for(score, {"medium": 25, "high": 50, "critical": 75}) == expected


def test_sql_categories_equal_python_mirror(conn):
    cfg = R.RiskConfig.default()
    df = R.compute_risk(conn, FULL, cfg)
    assert (df["risk_category"] == df["risk_score"].apply(lambda s: R.category_for(s, cfg.thresholds))).all()


def test_thresholds_are_configurable(conn):
    cfg = R.RiskConfig.default()
    base = R.compute_risk(conn, FULL, cfg)
    cfg2 = cfg.copy()
    cfg2.thresholds = {"medium": 1, "high": 2, "critical": 3}
    strict = R.compute_risk(conn, FULL, cfg2)
    assert (base["risk_score"] == strict["risk_score"]).all()      # thresholds never change scores
    assert (strict["risk_category"] == "Critical").sum() >= (base["risk_category"] == "Critical").sum()


def test_invalid_configs_are_rejected():
    c = R.RiskConfig.default()
    c.weights["age"] = -1
    with pytest.raises(ValueError):
        c.validate()
    c = R.RiskConfig.default()
    c.thresholds = {"medium": 50, "high": 40, "critical": 75}
    with pytest.raises(ValueError):
        c.validate()
    c = R.RiskConfig.default()
    c.weights = {k: 0 for k in R.COMPONENTS}
    with pytest.raises(ValueError):
        c.validate()


# ------------------------------------------------------------------ score calculation
def test_scores_in_range_and_components_sum_to_score(conn):
    df = R.compute_risk(conn, FULL)
    assert df["risk_score"].between(0, 100).all()
    total = df[[f"c_{c}" for c in R.COMPONENTS]].sum(axis=1)
    assert np.allclose(total, df["risk_score"], atol=0.011)        # explanation reproduces the score (2-dp rounding)
    norm = df[[f"n_{c}" for c in R.COMPONENTS if c != "tele"]]
    assert ((norm >= 0) & (norm <= 1)).all().all()


def test_frequency_scores_match_independent_pandas_minmax(conn, frames):
    f = frames["failures"].groupby("machineID").size().reindex([1, 2, 3], fill_value=0)
    e = frames["errors"].groupby("machineID").size().reindex([1, 2, 3], fill_value=0)
    mm = lambda s: (s - s.min()) / (s.max() - s.min())
    exp = 100 * (30 * mm(f) + 10 * mm(e)) / 40                     # only failure+error frequency carry weight
    df = R.compute_risk(conn, FULL, cfg_with(fail_freq=30, err_freq=10)).set_index("machine_id")
    assert df["risk_score"].round(2).sort_index().tolist() == exp.round(2).sort_index().tolist()


def test_single_component_weight_gives_that_component_only(conn):
    df = R.compute_risk(conn, FULL, cfg_with(age=7)).set_index("machine_id")
    assert df["risk_score"].round(2).to_dict() == {1: 0.0, 2: 50.0, 3: 100.0}     # ages 5, 10, 15 -> min-max 0, .5, 1
    assert (df["c_fail_freq"] == 0).all()


def test_weights_are_relative_not_absolute(conn):
    a = R.compute_risk(conn, FULL, cfg_with(fail_freq=1, err_freq=1, age=2))
    b = R.compute_risk(conn, FULL, cfg_with(fail_freq=10, err_freq=10, age=20))
    assert (a["risk_score"] == b["risk_score"]).all()


def test_recency_decay_matches_formula(conn):
    cfg = cfg_with(fail_rec=1)
    df = R.compute_risk(conn, A.Filters("2015-01-01", "2015-03-01"), cfg).set_index("machine_id")
    as_of = pd.Timestamp("2015-03-02")                              # end of range is exclusive-next-day
    expected = max(0.0, 1 - (as_of - pd.Timestamp("2015-02-10 06:00:00")).total_seconds() / 86400 / 90)
    assert df.loc[1, "n_fail_rec"] == pytest.approx(expected, abs=1e-6)
    assert df.loc[3, "n_fail_rec"] == 0                             # machine 3 never failed


def test_maintenance_burden_excludes_failure_linked_events(conn):
    df = R.compute_risk(conn, FULL).set_index("machine_id")
    assert df["planned_maintenance_count"].to_dict() == {1: 2, 2: 2, 3: 2}
    assert df["maintenance_count"].to_dict() == {1: 5, 2: 3, 3: 2}


def test_machine_filter_does_not_change_scores(conn):
    allm = R.compute_risk(conn, FULL).set_index("machine_id")
    one = R.compute_risk(conn, A.Filters("2015-01-01", "2015-12-31", machines=(2,)))
    assert len(one) == 1 and one.iloc[0]["risk_score"] == allm.loc[2, "risk_score"]
    assert one.iloc[0]["priority_rank"] == allm.loc[2, "priority_rank"]


def test_missing_telemetry_is_flagged_and_weight_renormalised(conn):
    # as-of far after the last reading: no telemetry in the window for anybody
    df = R.compute_risk(conn, A.Filters("2015-01-01", "2015-12-31"), R.RiskConfig.default())
    assert df["tele_obs"].isna().all()
    assert df["data_flags"].str.contains("no telemetry").all()
    assert (df["c_tele"] == 0).all()
    assert (df["weight_total"] == 85).all()                         # default weights total 100, minus telemetry (15)
    ok = R.compute_risk(conn, A.Filters("2015-01-01", "2015-03-10"), R.RiskConfig.default())
    assert ok["tele_obs"].notna().all() and (ok["weight_total"] == 100).all()


def test_empty_evidence_window_scores_without_error(conn):
    df = R.compute_risk(conn, A.Filters("2020-01-01", "2020-02-01"))
    assert len(df) == 3 and df["risk_score"].between(0, 100).all()
    assert df["data_flags"].str.contains("no failures").all()


def test_explanations_and_traceability(conn):
    cfg = R.RiskConfig.default()
    df = R.compute_risk(conn, FULL, cfg)
    top = df.iloc[0]
    text = R.explain(top, cfg)
    assert "pts" in text and cfg.labels["fail_freq"] in text
    ev = R.evidence(conn, 1, FULL, cfg)
    row = df.set_index("machine_id").loc[1]
    assert len(ev["failures"]) == row["failure_count"] == 3
    assert len(ev["errors"]) == row["error_count"] == 3
    assert len(ev["maintenance"]) == row["maintenance_count"] == 5
    assert set(ev["maintenance"]["kind"]) == {"planned", "failure-linked"}


def test_summary_statistics(conn):
    df = R.compute_risk(conn, FULL)
    s = R.summary(df)
    assert s["machines"] == 3 and s["avg_score"] == round(df["risk_score"].mean(), 2)
    assert sum(s["by_category"].values()) == 3
    assert R.summary(df.iloc[0:0])["machines"] == 0


# ------------------------------------------------------------------ priority table / exports
def test_priority_table_ranking_filtering_and_csv(conn):
    cfg = R.RiskConfig.default()
    t = P.build_priority_table(R.compute_risk(conn, FULL, cfg), cfg)
    assert t["Priority"].is_monotonic_increasing and t["Risk score"].is_monotonic_decreasing
    assert {"Machine ID", "Model", "Age", "Risk score", "Category", "Failures", "Errors", "Last maintenance",
            "Main reasons", "Suggested inspection (recommendation)"} <= set(t.columns)
    assert P.filter_table(t, categories=["Critical"]).empty or (P.filter_table(t, categories=["Critical"])["Category"] == "Critical").all()
    assert len(P.filter_table(t, search="model2")) == 1
    assert len(P.filter_table(t, min_score=t["Risk score"].max())) >= 1
    csv = pd.read_csv(pd.io.common.BytesIO(P.to_csv_bytes(t)))
    assert len(csv) == len(t) and list(csv.columns) == list(t.columns)
    assert "recommendation" in " ".join(t.columns).lower() and "diagnos" in P.DISCLAIMER


# ------------------------------------------------------------------ Module 03: before / after
def test_percentage_change_rules():
    assert M.pct_change(100, 110) == pytest.approx(10.0)
    assert M.pct_change(-50, -25) == pytest.approx(50.0)
    assert M.pct_change(0, 5) is None and M.pct_change(None, 5) is None and M.pct_change(5, float("nan")) is None


def test_before_after_statistics_match_pandas(conn, frames):
    res = M.compare(conn, 1, "2015-01-25 06:00:00", 7, 24)
    tel = frames["telemetry"][frames["telemetry"].machineID == 1].copy()
    tel["t"] = pd.to_datetime(tel["datetime"])
    t0 = pd.Timestamp("2015-01-25 06:00:00")
    before = tel[(tel.t >= t0 - pd.Timedelta(days=7)) & (tel.t < t0)]
    after = tel[(tel.t > t0) & (tel.t <= t0 + pd.Timedelta(days=7))]
    assert res["n_before"] == len(before) == 168 and res["n_after"] == len(after) == 168 and res["sufficient"]
    tab = res["table"].set_index("metric")
    for m in M.METRICS:
        assert tab.loc[m, "before_mean"] == pytest.approx(before[m].mean(), rel=1e-9)
        assert tab.loc[m, "after_mean"] == pytest.approx(after[m].mean(), rel=1e-9)
        assert tab.loc[m, "before_median"] == pytest.approx(before[m].median(), rel=1e-9)
        assert tab.loc[m, "after_median"] == pytest.approx(after[m].median(), rel=1e-9)
        assert tab.loc[m, "before_min"] == pytest.approx(before[m].min()) and tab.loc[m, "after_max"] == pytest.approx(after[m].max())
        assert tab.loc[m, "before_std"] == pytest.approx(before[m].std(ddof=1), rel=1e-6)
        assert tab.loc[m, "change_mean"] == pytest.approx(after[m].mean() - before[m].mean(), rel=1e-6, abs=1e-9)
        assert tab.loc[m, "pct_change_mean"] == pytest.approx(
            (after[m].mean() - before[m].mean()) / abs(before[m].mean()) * 100, rel=1e-6)


def test_median_with_even_and_odd_counts(conn, frames):
    # window of 1 day = 24 readings (even) before; 24 after
    res = M.compare(conn, 2, "2015-02-15 06:00:00", 1, 5)
    tel = frames["telemetry"][frames["telemetry"].machineID == 2].copy()
    tel["t"] = pd.to_datetime(tel["datetime"])
    t0 = pd.Timestamp("2015-02-15 06:00:00")
    before = tel[(tel.t >= t0 - pd.Timedelta(days=1)) & (tel.t < t0)]["pressure"]
    assert len(before) == 24
    assert res["table"].set_index("metric").loc["pressure", "before_median"] == pytest.approx(before.median())


def test_zero_baseline_suppresses_percentage_change(conn):
    res = M.compare(conn, 3, "2015-02-01 06:00:00", 7, 24)         # vibration is exactly 0 for the whole before-window
    vib = res["table"].set_index("metric").loc["vibration"]
    assert vib["before_mean"] == 0 and pd.isna(vib["pct_change_mean"])
    assert vib["change_mean"] == pytest.approx(vib["after_mean"])  # absolute change is still reported
    assert any("zero baseline" in m for m in res["messages"])
    assert pd.notna(res["table"].set_index("metric").loc["volt", "pct_change_mean"])


def test_insufficient_observations_are_flagged(conn):
    early = M.compare(conn, 3, "2015-01-02 12:00:00", 7, 48)       # telemetry only begins 30 h earlier
    assert early["n_before"] < 48 and not early["sufficient"]
    assert any("Insufficient" in m for m in early["messages"])
    late = M.compare(conn, 2, "2015-03-09 06:00:00", 7, 48)        # telemetry ends 23 h later
    assert late["n_after"] == 23 and not late["sufficient"]


def test_no_readings_at_all_is_handled(conn):
    res = M.compare(conn, 1, "2014-01-01 00:00:00", 3, 24)
    assert res["table"].empty and not res["sufficient"] and res["messages"]


def test_confounders_are_reported(conn):
    res = M.compare(conn, 1, "2015-02-10 06:00:00", 7, 24)         # failure/maintenance cluster around this date
    c = res["confounders"]
    assert c["events"].sum() > 0
    assert set(c["period"]) <= {"before", "at event", "after"}


def test_simultaneous_events_are_confounders_at_the_event_time(conn):
    # machine 1, 2015-02-10 06:00: comp1 and comp2 are both repaired after both fail at that instant
    res = M.compare(conn, 1, "2015-02-10 06:00:00", 1, 5, "comp1")
    at = res["confounders"].query("period == 'at event'").set_index("event_type")["events"]
    assert at["other maintenance"] == 1                            # comp2 only; the selected comp1 row is excluded
    assert at["failure"] == 2


def test_fleet_band_ignores_readings_after_the_as_of_date(azure_db):
    # A wild reading after the as-of date must not widen the band used for an earlier score.
    f = A.Filters("2015-01-01", "2015-01-31")
    cfg = R.RiskConfig.default()
    c = db.connect(azure_db, read_only=True)
    before = R.compute_risk(c, f, cfg).set_index("machine_id")["tele_exceed_rate"]
    c.close()
    w = db.connect(azure_db)
    w.execute("UPDATE azure_telemetry SET volt = 1e6 WHERE ts >= '2015-03-01'")
    w.commit()
    w.close()
    c = db.connect(azure_db, read_only=True)
    after = R.compute_risk(c, f, cfg).set_index("machine_id")["tele_exceed_rate"]
    c.close()
    assert after.to_dict() == before.to_dict()


def test_default_range_starts_at_telemetry_not_at_older_maintenance(azure_db):
    w = db.connect(azure_db)
    w.execute("INSERT INTO azure_maintenance (machine_id, ts, component) VALUES (1, '2014-06-01 06:00:00', 'comp1')")
    w.commit()
    w.close()
    c = db.connect(azure_db, read_only=True)
    assert A.data_bounds(c)[0] == "2014-06-01"                     # still selectable in the date picker
    f = A.default_filters(c)
    assert f.start == "2015-01-01"
    assert A.query(c, "failure_trend", f)["month"].iloc[0] == "2015-01"
    c.close()


def test_component_summary_does_not_fan_out(conn):
    summ = M.component_summary(conn, window_days=2, min_obs=24)
    exp = db.read_df(conn, "SELECT component, COUNT(*) n FROM azure_maintenance GROUP BY component").set_index("component")["n"]
    assert summ.set_index("component")["maintenance_events"].to_dict() == exp.to_dict()
    assert (summ["events_with_both_windows"] <= summ["maintenance_events"]).all()


def test_component_summary_matches_pandas_for_one_component(conn, frames):
    summ = M.component_summary(conn, window_days=2, min_obs=24).set_index("component")
    tel = frames["telemetry"].copy()
    tel["t"] = pd.to_datetime(tel["datetime"])
    deltas = []
    for _, r in frames["maint"][frames["maint"].comp == "comp1"].iterrows():
        t0 = pd.Timestamp(r["datetime"])
        t = tel[tel.machineID == r["machineID"]]
        b = t[(t.t >= t0 - pd.Timedelta(hours=48)) & (t.t < t0)]
        a = t[(t.t > t0) & (t.t <= t0 + pd.Timedelta(hours=48))]
        if len(b) >= 24 and len(a) >= 24:
            deltas.append(a["volt"].mean() - b["volt"].mean())
    assert summ.loc["comp1", "events_with_both_windows"] == len(deltas)
    assert summ.loc["comp1", "avg_change_volt"] == pytest.approx(round(float(np.mean(deltas)), 3), abs=1e-3)


# ------------------------------------------------------------------ real data
@pytest.mark.real_data
def test_real_risk_scores_are_consistent(real_db):
    c = db.connect(real_db, read_only=True)
    f = A.default_filters(c)
    cfg = R.RiskConfig.default()
    df = R.compute_risk(c, f, cfg)
    s = R.summary(df)
    assert s["machines"] == 100 and sum(s["by_category"].values()) == 100
    assert np.allclose(df[[f"c_{k}" for k in R.COMPONENTS]].sum(axis=1), df["risk_score"], atol=0.011)
    assert (df["risk_category"] == df["risk_score"].apply(lambda v: R.category_for(v, cfg.thresholds))).all()
    assert df["failure_count"].sum() == 761 and df["error_count"].sum() == 3919
    c.close()
