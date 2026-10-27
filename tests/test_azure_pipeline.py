"""Azure ingestion: validation, schema, idempotency, duplicates, rollback, integrity."""
import shutil

import pandas as pd
import pytest

import builders
from conftest import AZURE_TABLE_NAMES, table_fingerprint
from equipment import db, loading, readiness
from equipment.azure import loader as L
from equipment.azure import validation as V


def _count(path, table):
    c = db.connect(path, read_only=True)
    try:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        c.close()


# ------------------------------------------------------------------ csv loading & schema validation
def test_validate_all_reads_every_file(azure_dir, frames):
    v = V.validate_all(azure_dir)
    assert v["machines"].rows_read == len(frames["machines"]) == 3
    assert v["telemetry"].rows_read == len(frames["telemetry"])
    assert v["errors"].rows_read == 6 and v["failures"].rows_read == 4 and v["maintenance"].rows_read == 10
    assert all(len(t.rejected) == 0 for t in v.values())


def test_missing_file_is_reported_clearly(azure_dir):
    (azure_dir / "PdM_errors.csv").unlink()
    with pytest.raises(loading.MissingDatasetFilesError, match="PdM_errors.csv"):
        V.validate_all(azure_dir)


def test_missing_column_raises(azure_dir):
    pd.DataFrame({"datetime": ["2015-01-01 06:00:00"], "machineID": [1]}).to_csv(azure_dir / "PdM_errors.csv", index=False)
    with pytest.raises(loading.ValidationError, match="errorID"):
        V.validate_all(azure_dir)


def test_empty_file_raises(azure_dir):
    (azure_dir / "PdM_failures.csv").write_text("")
    with pytest.raises(loading.ValidationError):
        V.validate_all(azure_dir)


def test_missing_file_does_not_touch_database(tmp_path, azure_dir):
    (azure_dir / "PdM_maint.csv").unlink()
    path = tmp_path / "x.db"
    with pytest.raises(loading.MissingDatasetFilesError):
        L.load_azure(path, azure_dir)
    assert not path.exists()


# ------------------------------------------------------------------ database creation and record counts
def test_counts_match_source(azure_db, frames):
    assert _count(azure_db, "azure_machines") == 3
    assert _count(azure_db, "azure_telemetry") == len(frames["telemetry"])
    assert _count(azure_db, "azure_errors") == 6
    assert _count(azure_db, "azure_failures") == 4        # includes two failures at the same timestamp
    assert _count(azure_db, "azure_maintenance") == 10


def test_source_values_are_preserved(azure_db, frames):
    c = db.connect(azure_db, read_only=True)
    got = pd.read_sql_query("SELECT machine_id, ts, volt, rotate, pressure, vibration FROM azure_telemetry "
                            "ORDER BY machine_id, ts", c)
    c.close()
    exp = frames["telemetry"].rename(columns={"machineID": "machine_id", "datetime": "ts"}).sort_values(
        ["machine_id", "ts"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(got[exp.columns], exp[exp.columns], check_dtype=False)


def test_indexes_and_integrity(azure_db):
    c = db.connect(azure_db, read_only=True)
    idx = readiness.index_list(c, "azure_")["Index"].tolist()
    assert {"idx_az_telemetry_ts", "idx_az_failures_ts", "idx_az_errors_type", "idx_az_maint_comp"} <= set(idx)
    rep = readiness.integrity(c)
    assert rep["integrity_ok"] and rep["foreign_key_violations"] == 0
    ready, problems = readiness.verdict(c, "azure", {"azure_machines": 3})
    assert ready, problems
    c.close()


def test_telemetry_query_uses_the_primary_key_index(azure_db):
    c = db.connect(azure_db, read_only=True)
    plan = readiness.query_plan(c, "SELECT AVG(volt) FROM azure_telemetry WHERE machine_id = :m AND ts >= :a AND ts < :b",
                                {"m": 1, "a": "2015-01-05", "b": "2015-01-06"})
    c.close()
    assert any("SEARCH" in line and "PRIMARY KEY" in line for line in plan), plan


# ------------------------------------------------------------------ repeatable loading / duplicates
def test_rerun_is_idempotent(tmp_path, azure_dir):
    path = tmp_path / "idem.db"
    first = L.load_azure(path, azure_dir)
    before = table_fingerprint(path, AZURE_TABLE_NAMES)
    second = L.load_azure(path, azure_dir)
    third = L.load_azure(path, azure_dir)
    assert (first.status, second.status, third.status) == ("loaded", "up_to_date", "up_to_date")
    assert table_fingerprint(path, AZURE_TABLE_NAMES) == before


def test_force_reload_does_not_duplicate(tmp_path, azure_dir):
    path = tmp_path / "force.db"
    L.load_azure(path, azure_dir)
    before = table_fingerprint(path, AZURE_TABLE_NAMES)
    assert L.load_azure(path, azure_dir, force=True).status == "loaded"
    assert table_fingerprint(path, AZURE_TABLE_NAMES) == before


def test_changed_source_replaces_data(tmp_path, azure_dir, frames):
    path = tmp_path / "chg.db"
    L.load_azure(path, azure_dir)
    extra = pd.concat([frames["failures"], pd.DataFrame([("2015-03-01 06:00:00", 3, "comp4")], columns=frames["failures"].columns)])
    extra.to_csv(azure_dir / "PdM_failures.csv", index=False)
    assert L.load_azure(path, azure_dir).status == "loaded"
    assert _count(path, "azure_failures") == 5


def test_duplicate_records_are_rejected_not_double_counted(tmp_path, azure_dir, frames):
    dup = pd.concat([frames["failures"], frames["failures"].iloc[[0]]])
    dup.to_csv(azure_dir / "PdM_failures.csv", index=False)
    path = tmp_path / "dup.db"
    rep = L.load_azure(path, azure_dir)
    t = [x for x in rep.tables if x.table == "azure_failures"][0]
    assert (t.rows_read, t.rows_loaded, t.rows_rejected) == (5, 4, 1)
    c = db.connect(path, read_only=True)
    rej = c.execute("SELECT reason, source_row FROM rejected_records WHERE table_name='azure_failures'").fetchall()
    c.close()
    assert len(rej) == 1 and "duplicate" in rej[0][0] and rej[0][1] == 5


def test_same_timestamp_different_component_is_not_a_duplicate(azure_db):
    c = db.connect(azure_db, read_only=True)
    n = c.execute("SELECT COUNT(*) FROM azure_failures WHERE machine_id=1 AND ts='2015-02-10 06:00:00'").fetchone()[0]
    c.close()
    assert n == 2


def test_invalid_records_are_kept_with_reasons(tmp_path, azure_dir, frames):
    bad_err = pd.concat([frames["errors"], pd.DataFrame([
        ("not-a-date", 1, "error1"),
        ("2015-04-01 06:00:00", 99, "error1"),            # machine 99 does not exist
        ("2015-04-02 06:00:00", 1, ""),                   # missing error type
        ("2015-04-03 06:00:00", "abc", "error2"),         # machine id not an integer
    ], columns=frames["errors"].columns)])
    bad_err.to_csv(azure_dir / "PdM_errors.csv", index=False)
    tel = frames["telemetry"].copy().astype({"volt": object})
    tel.loc[0, "volt"] = "oops"
    tel.to_csv(azure_dir / "PdM_telemetry.csv", index=False)
    path = tmp_path / "bad.db"
    rep = L.load_azure(path, azure_dir)
    err = [x for x in rep.tables if x.table == "azure_errors"][0]
    tl = [x for x in rep.tables if x.table == "azure_telemetry"][0]
    assert err.rows_rejected == 4 and err.rows_loaded == 6
    assert tl.rows_rejected == 1 and tl.rows_loaded == len(frames["telemetry"]) - 1
    c = db.connect(path, read_only=True)
    reasons = " | ".join(r[0] for r in c.execute("SELECT reason FROM rejected_records"))
    c.close()
    for needle in ("datetime", "not present in PdM_machines", "errorID missing", "not an integer", "volt is not numeric"):
        assert needle in reasons


def test_missing_sensor_value_is_stored_as_null_with_warning(tmp_path, azure_dir, frames):
    tel = frames["telemetry"].copy()
    tel.loc[5, "pressure"] = None
    tel.to_csv(azure_dir / "PdM_telemetry.csv", index=False)
    path = tmp_path / "nul.db"
    rep = L.load_azure(path, azure_dir)
    t = [x for x in rep.tables if x.table == "azure_telemetry"][0]
    assert t.rows_loaded == len(tel) and any("missing pressure" in w for w in t.warnings)
    assert _count(path, "azure_telemetry") == len(tel)
    c = db.connect(path, read_only=True)
    assert c.execute("SELECT COUNT(*) FROM azure_telemetry WHERE pressure IS NULL").fetchone()[0] == 1
    c.close()


def test_failed_load_rolls_back_and_keeps_previous_data(tmp_path, azure_dir, frames, monkeypatch):
    path = tmp_path / "rb.db"
    L.load_azure(path, azure_dir)
    before = table_fingerprint(path, AZURE_TABLE_NAMES)
    # change a source file so a reload is attempted, and sabotage the last insert statement
    pd.concat([frames["failures"], frames["failures"].iloc[:0]]).assign(failure="comp9").to_csv(
        azure_dir / "PdM_failures.csv", index=False)
    monkeypatch.setitem(L.INSERT_SQL, "failures", ("INSERT INTO no_such_table VALUES (?,?,?)", ["machine_id", "ts", "component"]))
    with pytest.raises(Exception):
        L.load_azure(path, azure_dir)
    assert table_fingerprint(path, AZURE_TABLE_NAMES) == before          # nothing half-written
    c = db.connect(path, read_only=True)
    assert c.execute("SELECT COUNT(*) FROM data_load_log WHERE status='failed'").fetchone()[0] >= 1
    c.close()


def test_read_only_connection_to_missing_database_is_a_clear_error(tmp_path):
    with pytest.raises(db.DatabaseNotFoundError, match="load_data.py"):
        db.connect(tmp_path / "nope.db", read_only=True)


def test_empty_dataset_files_load_without_crashing(tmp_path):
    d = tmp_path / "empty"
    frames = builders.azure_frames()
    empty = {k: v.iloc[0:0] for k, v in frames.items()}
    empty["machines"] = frames["machines"]
    builders.write_azure_csvs(d, empty)
    rep = L.load_azure(tmp_path / "e.db", d)
    assert rep.status == "loaded"
    assert _count(tmp_path / "e.db", "azure_telemetry") == 0
    ready, problems = readiness.verdict(db.connect(tmp_path / "e.db", read_only=True), "azure", {"azure_telemetry": 1})
    assert not ready


# ------------------------------------------------------------------ real dataset
@pytest.mark.real_data
def test_real_dataset_counts(real_db):
    c = db.connect(real_db, read_only=True)
    got = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in readiness.AZURE_EXPECTED}
    c.close()
    assert got == readiness.AZURE_EXPECTED
    c = db.connect(real_db, read_only=True)
    assert c.execute("SELECT COUNT(*) FROM rejected_records WHERE dataset='azure'").fetchone()[0] == 0
    assert readiness.integrity(c) == {"integrity_ok": True, "integrity_messages": ["ok"], "foreign_key_violations": 0}
    c.close()
