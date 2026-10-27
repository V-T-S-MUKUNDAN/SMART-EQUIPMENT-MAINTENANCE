"""AI4I 2020: file detection, validation, ingestion, idempotency, SQL-vs-pandas analytics, risk indicator."""
import math
import re

import numpy as np
import pandas as pd
import pytest

import builders
from equipment import db, loading, readiness
from equipment.ai4i import analytics as A
from equipment.ai4i import loader as L
from equipment.ai4i import risk as R

TABLE_COLS = list(L.COLUMN_MAP.values())


def _frame():
    return builders.ai4i_frame()


def _db_frame(path):
    c = db.connect(path, read_only=True)
    try:
        return pd.read_sql_query("SELECT * FROM ai4i_machine_data ORDER BY udi", c)
    finally:
        c.close()


# ------------------------------------------------------------------ csv detection
def test_find_csv_by_header_even_with_bom_and_other_name(tmp_path):
    builders.write_ai4i_csv(tmp_path, name="whatever_name.csv")
    pd.DataFrame({"a": [1]}).to_csv(tmp_path / "unrelated.csv", index=False)
    assert L.find_ai4i_csv([tmp_path]).name == "whatever_name.csv"


def test_find_csv_missing_gives_clear_instruction(tmp_path):
    with pytest.raises(loading.MissingDatasetFilesError, match="Place the downloaded file"):
        L.find_ai4i_csv([tmp_path])


def test_explicit_missing_file_is_reported(tmp_path):
    with pytest.raises(loading.MissingDatasetFilesError, match="does not exist"):
        L.find_ai4i_csv(explicit=tmp_path / "nope.csv")


def test_missing_csv_does_not_create_database(tmp_path, monkeypatch):
    monkeypatch.setenv("SE_AI4I_DIR", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    path = tmp_path / "x.db"
    with pytest.raises(loading.MissingDatasetFilesError):
        L.load_ai4i(path)
    assert not path.exists()


def test_search_dir_env_override_is_used(tmp_path, monkeypatch):
    d = tmp_path / "elsewhere"
    builders.write_ai4i_csv(d, name="ai4i2020.csv")
    monkeypatch.setenv("SE_AI4I_DIR", str(d))
    assert L.find_ai4i_csv().parent == d


# ------------------------------------------------------------------ validation
def test_validate_clean_frame(ai4i_dir):
    v = L.validate(ai4i_dir / "ai4i2020.csv")
    assert v.rows_read == 60 and v.rows_valid == 60 and not v.rejected
    assert list(v.clean.columns) == TABLE_COLS


def test_bom_does_not_corrupt_first_column(ai4i_dir):
    v = L.validate(ai4i_dir / "ai4i2020.csv")
    assert v.clean["udi"].tolist() == list(range(1, 61))


def test_column_name_variants_are_mapped(tmp_path):
    df = _frame().rename(columns={"Air temperature [K]": "air temperature k", "Tool wear [min]": "TOOL_WEAR_MIN",
                                  "Product ID": "product_id", "Machine failure": "machine failure"})
    p = builders.write_ai4i_csv(tmp_path, df=df, bom=False)
    v = L.validate(p)
    assert v.rows_valid == 60 and list(v.clean.columns) == TABLE_COLS


def test_missing_required_column_raises_naming_it(tmp_path):
    p = builders.write_ai4i_csv(tmp_path, df=_frame().drop(columns=["Torque [Nm]"]))
    with pytest.raises(loading.ValidationError, match=r"Torque \[Nm\]"):
        L.validate(p)


def test_extra_column_is_ignored_with_warning(tmp_path):
    p = builders.write_ai4i_csv(tmp_path, df=_frame().assign(Comment="x"))
    v = L.validate(p)
    assert any("extra column 'Comment'" in w for w in v.warnings) and v.rows_valid == 60


def test_invalid_rows_are_rejected_with_reasons_and_not_dropped_silently(tmp_path):
    df = _frame().astype(object)
    df.loc[0, "Type"] = "X"                    # invalid type
    df.loc[1, "Torque [Nm]"] = "abc"           # non numeric
    df.loc[2, "Tool wear [min]"] = -5          # negative
    df.loc[3, "TWF"] = 2                       # not a flag
    df.loc[4, "UDI"] = df.loc[5, "UDI"]        # duplicate UDI -> the later occurrence is rejected
    df.loc[6, "Product ID"] = ""               # missing product id
    p = builders.write_ai4i_csv(tmp_path, df=df)
    v = L.validate(p)
    assert v.rows_read == 60 and v.rows_valid == 54 and len(v.rejected) == 6
    assert v.rows_valid + len(v.rejected) == v.rows_read
    reasons = " | ".join(r[1] for r in v.rejected)
    for text in ["Type must be", "not numeric", "negative", "must be 0 or 1", "duplicate UDI", "Product ID missing"]:
        assert text in reasons


def test_inconsistent_labels_are_flagged_but_kept(ai4i_dir):
    v = L.validate(ai4i_dir / "ai4i2020.csv")
    assert any("Machine failure=1 but no failure-mode flag" in w for w in v.warnings)
    assert any("failure-mode flag but Machine failure=0" in w for w in v.warnings)
    assert any("more than one failure-mode flag" in w for w in v.warnings)
    assert v.rows_valid == 60                                     # nothing removed or "fixed"


def test_row_count_warning_when_not_10000(ai4i_dir):
    v = L.validate(ai4i_dir / "ai4i2020.csv")
    assert any("60 rows read" in w for w in v.warnings)


# ------------------------------------------------------------------ ingestion
def test_load_preserves_every_source_value(ai4i_db):
    got = _db_frame(ai4i_db)
    exp = _frame().rename(columns=L.COLUMN_MAP).sort_values("udi").reset_index(drop=True)
    pd.testing.assert_frame_equal(got[TABLE_COLS].astype(exp.dtypes.to_dict()), exp[TABLE_COLS], check_dtype=False)


def test_schema_has_primary_key_indexes_and_integrity(ai4i_db):
    c = db.connect(ai4i_db, read_only=True)
    cols = {r[1]: r[5] for r in c.execute("PRAGMA table_info(ai4i_machine_data)")}
    assert cols["udi"] == 1                                       # primary key
    assert len(readiness.index_list(c, "ai4i_")) >= 4
    assert readiness.integrity(c) == {"integrity_ok": True, "integrity_messages": ["ok"], "foreign_key_violations": 0}
    c.close()


def test_load_is_logged_with_hash(ai4i_db):
    c = db.connect(ai4i_db, read_only=True)
    row = c.execute("SELECT dataset, table_name, status, rows_loaded, length(source_sha256) FROM data_load_log").fetchone()
    c.close()
    assert tuple(row) == ("ai4i", "ai4i_machine_data", "success", 60, 64)


def test_rerun_is_idempotent(tmp_path, ai4i_dir):
    path, csv = tmp_path / "i.db", ai4i_dir / "ai4i2020.csv"
    first = L.load_ai4i(path, csv)
    before = _db_frame(path)
    second, third = L.load_ai4i(path, csv), L.load_ai4i(path, csv)
    assert (first.status, second.status, third.status) == ("loaded", "up_to_date", "up_to_date")
    pd.testing.assert_frame_equal(_db_frame(path), before)


def test_force_reload_does_not_duplicate(tmp_path, ai4i_dir):
    path, csv = tmp_path / "f.db", ai4i_dir / "ai4i2020.csv"
    L.load_ai4i(path, csv)
    before = _db_frame(path)
    assert L.load_ai4i(path, csv, force=True).status == "loaded"
    pd.testing.assert_frame_equal(_db_frame(path), before)
    assert len(before) == 60


def test_changed_source_replaces_rows(tmp_path, ai4i_dir):
    path, csv = tmp_path / "c.db", ai4i_dir / "ai4i2020.csv"
    L.load_ai4i(path, csv)
    builders.write_ai4i_csv(ai4i_dir, df=_frame().iloc[:50])
    assert L.load_ai4i(path, csv).status == "loaded"
    assert len(_db_frame(path)) == 50


def test_rejected_rows_are_stored_and_replaced_on_reload(tmp_path):
    df = _frame().astype(object)
    df.loc[0, "Type"] = "X"
    csv = builders.write_ai4i_csv(tmp_path, df=df)
    path = tmp_path / "r.db"
    L.load_ai4i(path, csv)
    L.load_ai4i(path, csv, force=True)
    c = db.connect(path, read_only=True)
    rows = c.execute("SELECT dataset, reason FROM rejected_records").fetchall()
    c.close()
    assert len(rows) == 1 and rows[0][0] == "ai4i" and "Type must be" in rows[0][1]


def test_failed_load_rolls_back_and_keeps_previous_data(tmp_path, ai4i_dir, monkeypatch):
    path, csv = tmp_path / "rb.db", ai4i_dir / "ai4i2020.csv"
    L.load_ai4i(path, csv)
    before = _db_frame(path)
    builders.write_ai4i_csv(ai4i_dir, df=_frame().iloc[:40])      # source changed -> a reload is attempted
    # validation succeeds, then the INSERT fails *after* the old rows were deleted inside the transaction
    monkeypatch.setattr(loading, "rows_of", lambda df, cols: [(1,)])
    with pytest.raises(Exception):
        L.load_ai4i(path, csv)
    monkeypatch.undo()
    pd.testing.assert_frame_equal(_db_frame(path), before)      # nothing half-written
    c = db.connect(path, read_only=True)
    assert c.execute("SELECT COUNT(*) FROM data_load_log WHERE status='failed'").fetchone()[0] >= 1
    c.close()


# ------------------------------------------------------------------ analytics: SQL vs pandas
@pytest.fixture
def pdf():
    f = _frame().rename(columns=L.COLUMN_MAP)
    f["modes"] = f[["twf", "hdf", "pwf", "osf", "rnf"]].sum(axis=1)
    return f


def test_overview_matches_pandas(aconn, pdf):
    o = A.overview(aconn)
    assert o["total_records"] == 60
    assert o["machine_failures"] == int(pdf.machine_failure.sum()) == 7
    assert o["records_without_failure"] == 53
    assert o["failure_rate_pct"] == pytest.approx(100 * 7 / 60, abs=1e-3)
    assert o["records_with_any_mode_flag"] == int((pdf.modes > 0).sum())
    assert o["records_with_multiple_modes"] == int((pdf.modes > 1).sum()) == 1


def test_failure_by_type_matches_pandas(aconn, pdf):
    got = A.query(aconn, "failure_by_type").set_index("machine_type")
    exp = pdf.groupby("machine_type").machine_failure.agg(["count", "sum"])
    for t in "LMH":
        assert got.loc[t, "records"] == exp.loc[t, "count"]
        assert got.loc[t, "failures"] == exp.loc[t, "sum"]
        assert got.loc[t, "failure_rate_pct"] == pytest.approx(100 * exp.loc[t, "sum"] / exp.loc[t, "count"], abs=1e-3)
    assert got["share_of_failures_pct"].sum() == pytest.approx(100, abs=0.2)


def test_failure_modes_are_counted_independently(aconn, pdf):
    got = A.query(aconn, "failure_modes").set_index("mode")
    for m in ["twf", "hdf", "pwf", "osf", "rnf"]:
        assert got.loc[m.upper(), "records_with_mode"] == int(pdf[m].sum())
    # co-occurring modes mean the mode counts exceed the number of records with any mode
    assert got["records_with_mode"].sum() == int(pdf.modes.sum()) > int((pdf.modes > 0).sum())
    # an RNF flag on a record with Machine failure = 0 is reported, not repaired
    assert got.loc["RNF", "with_machine_failure_0"] == 1 and got.loc["RNF", "with_machine_failure_1"] == 1


def test_mode_cooccurrence_has_one_row_per_pair(aconn, pdf):
    got = A.query(aconn, "mode_cooccurrence")
    assert got.records_with_both.sum() == 1
    r = got.iloc[0]
    assert (r.mode_a, r.mode_b) == ("OSF", "PWF")


def test_modes_per_record_sums_to_records(aconn):
    got = A.query(aconn, "modes_per_record")
    assert got.records.sum() == 60


def test_label_consistency_classification(aconn):
    got = A.query(aconn, "label_consistency").set_index("label_consistency").records
    assert got["failure_without_mode"] == 1 and got["mode_without_failure"] == 1
    assert got["consistent_failure"] == 6 and got["consistent_no_failure"] == 52
    assert got.sum() == 60
    bad = A.query(aconn, "inconsistent_records")
    assert sorted(bad.udi) == [54, 55]
    # the source labels are unchanged in the table
    c = aconn
    assert tuple(c.execute("SELECT machine_failure, rnf FROM ai4i_machine_data WHERE udi = 55").fetchone()) == (0, 1)
    assert tuple(c.execute("SELECT machine_failure, twf+hdf+pwf+osf+rnf FROM ai4i_machine_data WHERE udi = 54").fetchone()) == (1, 0)


def test_rule_agreement_counts_sum_to_records(aconn):
    got = A.query(aconn, "rule_agreement").set_index("mode")
    for m in ["HDF", "PWF", "OSF"]:
        assert got.loc[m, ["rule_met_and_labelled", "rule_met_not_labelled", "labelled_rule_not_met", "neither"]].sum() == 60
    # the hand-built HDF record (udi 49) meets the rule and carries the label
    assert got.loc["HDF", "rule_met_and_labelled"] >= 1


def test_derived_columns_match_pandas(aconn, pdf):
    got = pd.read_sql_query("SELECT udi, temp_diff_k, power_w, strain_minnm FROM v_ai4i_labeled ORDER BY udi", aconn)
    assert np.allclose(got.temp_diff_k, pdf.process_temp_k - pdf.air_temp_k)
    assert np.allclose(got.power_w, pdf.torque_nm * pdf.rotational_speed_rpm * 2 * math.pi / 60)
    assert np.allclose(got.strain_minnm, pdf.tool_wear_min * pdf.torque_nm)


def test_condition_bins_match_pandas(aconn, pdf):
    got = A.query(aconn, "condition_bins")
    w = got[got.variable == "Tool wear [min]"].set_index("bin_low")
    bins = (pdf.tool_wear_min // 50 * 50)
    exp = pdf.groupby(bins).machine_failure.agg(["count", "sum"])
    assert w.records.sum() == 60
    for lo, row in exp.iterrows():
        assert w.loc[float(lo), "records"] == row["count"] and w.loc[float(lo), "failures"] == row["sum"]
    # every variable accounts for every record (nothing falls outside the documented bins)
    assert (got.groupby("variable").records.sum() == 60).all()
    # LAG-based change is NULL for the first bin of each variable
    assert got.groupby("variable").rate_change_vs_prev_bin_pp.apply(lambda s: pd.isna(s.iloc[0])).all()


def test_group_window_analysis_matches_pandas(aconn, pdf):
    got = A.query(aconn, "group_window_analysis")
    band = pd.cut(pdf.tool_wear_min, [-1, 49, 99, 149, 199, 239, 10**6])
    exp = pdf.groupby(["machine_type", band], observed=True).machine_failure.agg(["count", "sum"])
    assert got.records.sum() == 60 and len(got) == len(exp)
    assert got.failures.sum() == exp["sum"].sum()
    # cumulative failures never decrease inside a type and end at the type total
    for t, g in got.groupby("machine_type"):
        assert g.cumulative_failures_in_type.is_monotonic_increasing
        assert g.cumulative_failures_in_type.iloc[-1] == g.failures.sum()
    assert sorted(got.overall_position) == list(range(1, len(got) + 1))
    assert (got[got.records < 30].low_sample == 1).all()


def test_variable_summary_correlation_matches_numpy(aconn, pdf):
    got = A.query(aconn, "variable_summary").set_index("variable")
    exp = np.corrcoef(pdf.tool_wear_min, pdf.machine_failure)[0, 1]
    assert got.loc["Tool wear [min]", "correlation_with_failure"] == pytest.approx(exp, abs=1e-3)
    assert got.loc["Torque [Nm]", "mean_failed"] == pytest.approx(pdf[pdf.machine_failure == 1].torque_nm.mean(), abs=0.01)


def test_type_by_wear_band_conditional_aggregation(aconn):
    got = A.query(aconn, "type_by_wear_band")
    assert int(got[["records_L", "records_M", "records_H"]].sum().sum()) == 60


# ------------------------------------------------------------------ filters
def test_type_and_range_filters_match_pandas(aconn, pdf):
    f = A.Filters.make(types=["L", "H"], tool_wear=(100, 200), torque=(30, 60))
    exp = pdf[pdf.machine_type.isin(["L", "H"]) & pdf.tool_wear_min.between(100, 200) & pdf.torque_nm.between(30, 60)]
    o = A.overview(aconn, f)
    assert o["total_records"] == len(exp) and o["machine_failures"] == int(exp.machine_failure.sum())
    assert len(R.compute_risk(aconn, f)) == len(exp)


def test_filter_that_matches_nothing_returns_empty_results(aconn):
    f = A.Filters.make(types=["L"], tool_wear=(10_000, 20_000))
    assert A.overview(aconn, f)["total_records"] == 0
    assert R.compute_risk(aconn, f).empty
    assert A.query(aconn, "failure_by_type", f).empty


def test_unknown_filter_key_is_rejected():
    with pytest.raises(ValueError):
        A.Filters.make(bogus=(1, 2))


def test_filter_values_are_bound_not_interpolated(aconn):
    evil = "L'); DROP TABLE ai4i_machine_data; --"
    assert A.overview(aconn, A.Filters.make(types=[evil]))["total_records"] == 0
    assert aconn.execute("SELECT COUNT(*) FROM ai4i_machine_data").fetchone()[0] == 60
    sql, _ = A.Filters.make(types=[evil]).where()
    assert "DROP" not in sql


# ------------------------------------------------------------------ risk indicator
def test_scores_in_range_and_one_row_per_record(aconn):
    df = R.compute_risk(aconn)
    assert len(df) == 60 and df.udi.is_unique
    assert df.risk_score.between(0, 100).all()


def test_contributions_sum_to_score_and_factors_in_unit_interval(aconn):
    df = R.compute_risk(aconn)
    total = df[[f"c_{c}" for c in R.COMPONENTS]].sum(axis=1)
    assert np.allclose(total, df.risk_score, atol=0.011)
    for c in R.COMPONENTS:
        assert df[f"f_{c}"].between(0, 1).all()


def test_score_matches_independent_python_implementation(aconn, pdf):
    cfg = R.RiskConfig.default()
    df = R.compute_risk(aconn, cfg=cfg).set_index("udi")
    p, w = cfg.parameters, cfg.weights
    avg_diff = (pdf.process_temp_k - pdf.air_temp_k).mean()
    avg_rpm = pdf.rotational_speed_rpm.mean()
    limit = {"L": 11000, "M": 12000, "H": 13000}
    for _, r in pdf.iterrows():
        power = r.torque_nm * r.rotational_speed_rpm * 2 * math.pi / 60
        diff = r.process_temp_k - r.air_temp_k
        f = {
            "tool_wear": min(1, r.tool_wear_min / p["tool_wear_reference_min"]),
            "strain": min(1, r.tool_wear_min * r.torque_nm / limit[r.machine_type]),
            "power": min(1, abs(power - 6250) / 2750),
            "heat": max(0, min(1, (avg_diff - diff) / (avg_diff - 8.6))) * max(0, min(1, (avg_rpm - r.rotational_speed_rpm) / (avg_rpm - 1380))),
        }
        expected = 100 * sum(w[k] * f[k] for k in f) / sum(w.values())
        assert df.loc[r.udi, "risk_score"] == pytest.approx(expected, abs=0.011), r.udi


def test_score_is_independent_of_failure_labels(tmp_path):
    base = _frame()
    flipped = base.copy()
    for c in ["Machine failure", "TWF", "HDF", "PWF", "OSF", "RNF"]:
        flipped[c] = 1 - flipped[c]
    from equipment.ai4i.loader import load_ai4i
    scores = []
    for i, df in enumerate([base, flipped]):
        csv = builders.write_ai4i_csv(tmp_path / f"d{i}", df=df)
        path = tmp_path / f"s{i}.db"
        load_ai4i(path, csv)
        c = db.connect(path, read_only=True)
        scores.append(R.compute_risk(c).set_index("udi")[["risk_score", "risk_category"] + [f"c_{k}" for k in R.COMPONENTS]])
        c.close()
    pd.testing.assert_frame_equal(scores[0], scores[1])


def test_weights_change_scores_and_zero_weight_removes_a_factor(aconn):
    cfg = R.RiskConfig.default()
    only_wear = cfg.copy()
    only_wear.weights = {"tool_wear": 1, "strain": 0, "power": 0, "heat": 0}
    df = R.compute_risk(aconn, cfg=only_wear)
    assert np.allclose(df.risk_score, 100 * df.f_tool_wear, atol=0.011)
    assert (df[["c_strain", "c_power", "c_heat"]] == 0).all().all()


def test_thresholds_are_configurable_and_boundaries_are_inclusive(aconn):
    cfg = R.RiskConfig.default()
    df = R.compute_risk(aconn, cfg=cfg)
    for _, r in df.iterrows():
        assert r.risk_category == R.category_for(r.risk_score, cfg.thresholds)
    assert R.category_for(24.99, cfg.thresholds) == "Low" and R.category_for(25, cfg.thresholds) == "Medium"
    assert R.category_for(49.99, cfg.thresholds) == "Medium" and R.category_for(50, cfg.thresholds) == "High"
    assert R.category_for(74.99, cfg.thresholds) == "High" and R.category_for(75, cfg.thresholds) == "Critical"
    strict = cfg.copy()
    strict.thresholds = {"medium": 1, "high": 2, "critical": 3}
    assert (R.compute_risk(aconn, cfg=strict).risk_category.isin(["Medium", "High", "Critical"])).sum() >= (df.risk_category != "Low").sum()


@pytest.mark.parametrize("weights,thresholds", [
    ({"tool_wear": -1, "strain": 1, "power": 1, "heat": 1}, None),
    ({"tool_wear": 0, "strain": 0, "power": 0, "heat": 0}, None),
    (None, {"medium": 60, "high": 50, "critical": 75}),
    (None, {"medium": 25, "high": 50, "critical": 101}),
])
def test_invalid_configuration_is_rejected(aconn, weights, thresholds):
    cfg = R.RiskConfig.default()
    if weights:
        cfg.weights = weights
    if thresholds:
        cfg.thresholds = thresholds
    with pytest.raises(ValueError):
        R.compute_risk(aconn, cfg=cfg)


def test_hand_built_records_score_as_designed(aconn):
    df = R.compute_risk(aconn).set_index("udi")
    assert df.loc[51, "f_strain"] == 1.0                      # 200 min x 70 Nm = 14000 > 13000 for type H
    assert df.loc[50, "f_power"] > 0.9                        # ~15.7 kW is far outside the 3.5-9 kW band
    assert df.loc[49, "f_heat"] > 0                           # small temperature difference at low speed
    assert df.loc[59, "f_tool_wear"] < 0.05                   # 10 min of wear
    assert df.loc[57, "c_tool_wear"] > df.loc[59, "c_tool_wear"]


def test_explanations_name_the_top_factors_and_sum_is_traceable(aconn):
    cfg = R.RiskConfig.default()
    row = R.compute_risk(aconn, cfg=cfg).set_index("udi").loc[51].reset_index().rename(columns={51: "x"})
    r = R.compute_risk(aconn, cfg=cfg).set_index("udi").loc[51]
    r["tool_wear_min"], text = r["tool_wear_min"], R.explain(r, cfg)
    assert "pts" in text and "tool wear" in text.lower()
    assert R.suggested_action(r) in R.ACTIONS.values()
    zero = r.copy()
    for c in R.COMPONENTS:
        zero[f"c_{c}"] = 0.0
    assert R.explain(zero, cfg) == "no factor contributes to the score"
    assert R.suggested_action(zero) == "No specific inspection focus."


def test_evaluation_queries_are_consistent(aconn):
    df = R.compute_risk(aconn)
    cat = R.category_validation(aconn)
    assert cat.records.sum() == 60 and cat.failures.sum() == df.machine_failure.sum()
    dec = R.decile_validation(aconn)
    assert dec.records.sum() == 60 and dec.failures.sum() == df.machine_failure.sum()
    sweep = R.threshold_sweep(aconn).set_index("score_at_least")
    assert sweep.records_flagged.is_monotonic_decreasing
    for cutoff, r in sweep.iterrows():
        assert r.records_flagged == int((df.risk_score >= cutoff).sum())
        assert r.failures_flagged == int(df[df.risk_score >= cutoff].machine_failure.sum())


def test_auc_matches_pandas_rank_computation(aconn):
    df = R.compute_risk(aconn)
    ranks = df.risk_score.rank(method="average")
    pos = df.machine_failure == 1
    n1, n0 = int(pos.sum()), int((~pos).sum())
    exp = (ranks[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)
    got = R.rank_separation(aconn).iloc[0]
    assert got.failed_records == n1 and got.non_failed_records == n0
    assert got.auc == pytest.approx(exp, abs=1e-4)


def test_group_ranking_for_module5_covers_every_record(aconn):
    g = R.group_ranking(aconn)
    assert g.records.sum() == 60
    assert g.priority_rank.min() == 1
    assert g.avg_risk_score.is_monotonic_decreasing
    assert (g[g.records < 30].low_sample == 1).all()


def test_summary_handles_empty_and_normal(aconn):
    assert R.summary(pd.DataFrame())["records"] == 0
    s = R.summary(R.compute_risk(aconn))
    assert sum(s["by_category"].values()) == 60 and set(s["by_category"]) == {"Low", "Medium", "High", "Critical"}


def test_scoring_sql_does_not_read_label_columns_before_final_select():
    sql = db.load_named_queries("ai4i/risk.sql")["risk_scores"]
    scoring, final_select = sql.split("SELECT f.*", 1)
    for label in ["machine_failure", "twf", "hdf", "pwf", "osf", "rnf"]:
        assert not re.search(rf"\b{label}\b", scoring), f"{label} referenced in the scoring CTEs"
    assert "machine_failure" in final_select                      # attached afterwards, for evaluation only


# ------------------------------------------------------------------ real data (skipped if the db is not built)
@pytest.mark.real_data
def test_real_ai4i_counts_and_integrity(real_db):
    c = db.connect(real_db, read_only=True)
    try:
        assert c.execute("SELECT COUNT(*) FROM ai4i_machine_data").fetchone()[0] == 10_000
        o = A.overview(c)
        assert o["total_records"] == 10_000 and o["machine_failures"] == 339
        assert c.execute("SELECT COUNT(DISTINCT udi) FROM ai4i_machine_data").fetchone()[0] == 10_000
        assert readiness.integrity(c)["integrity_ok"]
        types = A.query(c, "failure_by_type").set_index("machine_type")
        assert int(types.records.sum()) == 10_000 and int(types.failures.sum()) == 339
        scores = R.compute_risk(c)
        assert len(scores) == 10_000 and scores.risk_score.between(0, 100).all()
        assert np.allclose(scores[[f"c_{k}" for k in R.COMPONENTS]].sum(axis=1), scores.risk_score, atol=0.011)
        auc = R.rank_separation(c).iloc[0].auc
        assert 0.5 < auc < 1.0                      # descriptive, in-sample: not a performance claim
    finally:
        c.close()
