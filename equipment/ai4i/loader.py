"""Detect, validate and idempotently load the AI4I 2020 csv into ``ai4i_machine_data``.

Only AI4I objects are touched; the azure_* tables are never read or written here.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import pandas as pd

from .. import config, db, loading
from ..loading import LoadReport, MissingDatasetFilesError, TableReport, ValidatedTable, ValidationError

TABLE = "ai4i_machine_data"
EXPECTED_ROWS = 10_000

# Canonical (published) column name -> SQLite column name
COLUMN_MAP = {
    "UDI": "udi",
    "Product ID": "product_id",
    "Type": "machine_type",
    "Air temperature [K]": "air_temp_k",
    "Process temperature [K]": "process_temp_k",
    "Rotational speed [rpm]": "rotational_speed_rpm",
    "Torque [Nm]": "torque_nm",
    "Tool wear [min]": "tool_wear_min",
    "Machine failure": "machine_failure",
    "TWF": "twf", "HDF": "hdf", "PWF": "pwf", "OSF": "osf", "RNF": "rnf",
}
DB_COLUMNS = list(COLUMN_MAP.values())
FLAGS = ["machine_failure", "twf", "hdf", "pwf", "osf", "rnf"]


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


_NORM_TO_DB = {_norm(k): v for k, v in COLUMN_MAP.items()}


def _header_ok(path: Path) -> bool:
    try:
        with open(path, encoding="utf-8-sig") as fh:
            header = fh.readline()
    except (OSError, UnicodeDecodeError):
        return False
    cols = {_norm(c) for c in header.strip().split(",")}
    return {"udi", "machinefailure"} <= cols and "tool" in header.lower()


def find_ai4i_csv(search_dirs: list[Path] | None = None, explicit: str | Path | None = None) -> Path:
    """Locate the AI4I csv by inspecting headers of csv files in the project data folders."""
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise MissingDatasetFilesError(f"AI4I file '{p}' does not exist.")
        return p
    dirs = search_dirs if search_dirs is not None else config.ai4i_search_dirs()
    candidates: list[Path] = []
    for d in dirs:
        if Path(d).is_dir():
            candidates += sorted(Path(d).glob("*.csv"))
    matches = [c for c in candidates if _header_ok(c)]
    if not matches:
        raise MissingDatasetFilesError(
            "AI4I 2020 csv not found. Place the downloaded file (e.g. ai4i2020.csv) in "
            f"{' or '.join(str(d) for d in dirs)} (or set SE_AI4I_DIR)."
        )
    matches.sort(key=lambda p: ("ai4i" not in p.name.lower(), p.name))
    return matches[0]


def validate(path: Path) -> ValidatedTable:
    raw = loading.read_csv_text(path)
    raw.index += 1
    rename, unknown = {}, []
    for col in raw.columns:
        target = _NORM_TO_DB.get(_norm(col))
        if target:
            rename[col] = target
        else:
            unknown.append(col)
    missing = [k for k, v in COLUMN_MAP.items() if v not in rename.values()]
    if missing:
        raise ValidationError(f"{path.name}: missing required column(s) {missing}. Found: {list(raw.columns)}")
    warnings = [f"extra column '{c}' ignored" for c in unknown]
    df = raw.rename(columns=rename)[DB_COLUMNS].apply(lambda c: c.str.strip())

    reasons = pd.Series("", index=df.index, dtype=object)

    def flag(mask, text):
        reasons[mask & (reasons == "")] = text

    udi = pd.to_numeric(df["udi"], errors="coerce")
    flag(udi.isna() | (udi != udi.round()), "UDI missing or not an integer")
    flag(df["product_id"] == "", "Product ID missing")
    flag(~df["machine_type"].isin(["L", "M", "H"]), "Type must be L, M or H")
    num = {}
    for c in ["air_temp_k", "process_temp_k", "rotational_speed_rpm", "torque_nm", "tool_wear_min"]:
        num[c] = pd.to_numeric(df[c], errors="coerce")
        flag(num[c].isna(), f"{c} missing or not numeric")
    flag((num["tool_wear_min"] < 0), "tool wear is negative")
    for c in FLAGS:
        v = pd.to_numeric(df[c], errors="coerce")
        num[c] = v
        flag(~v.isin([0, 1]), f"{c} must be 0 or 1")
    ok = reasons == ""
    flag(ok & udi.duplicated(keep="first"), "duplicate UDI (first occurrence kept)")
    ok = reasons == ""

    clean = pd.DataFrame({"udi": udi[ok].astype(int), "product_id": df.loc[ok, "product_id"],
                          "machine_type": df.loc[ok, "machine_type"]})
    for c in ["air_temp_k", "process_temp_k", "torque_nm"]:
        clean[c] = num[c][ok].astype(float)
    for c in ["rotational_speed_rpm", "tool_wear_min", *FLAGS]:
        clean[c] = num[c][ok].round().astype(int)
    clean = clean[DB_COLUMNS].sort_values("udi").reset_index(drop=True)

    # data-quality warnings (informational: records are kept exactly as recorded)
    if len(raw) != EXPECTED_ROWS:
        warnings.append(f"{len(raw):,} rows read; the published AI4I 2020 dataset has {EXPECTED_ROWS:,}")
    dup_prod = int(clean["product_id"].duplicated().sum())
    if dup_prod:
        warnings.append(f"{dup_prod} duplicate Product ID values (UDI remains the unique key)")
    mism = int((clean["product_id"].str[:1] != clean["machine_type"]).sum())
    if mism:
        warnings.append(f"{mism} rows where the Product ID prefix differs from Type")
    modes = clean[["twf", "hdf", "pwf", "osf", "rnf"]].sum(axis=1)
    n_fail_nomode = int(((clean["machine_failure"] == 1) & (modes == 0)).sum())
    n_mode_nofail = int(((clean["machine_failure"] == 0) & (modes > 0)).sum())
    if n_fail_nomode:
        warnings.append(f"{n_fail_nomode} records have Machine failure=1 but no failure-mode flag (kept as recorded)")
    if n_mode_nofail:
        warnings.append(f"{n_mode_nofail} records have a failure-mode flag but Machine failure=0 (kept as recorded)")
    if int((modes > 1).sum()):
        warnings.append(f"{int((modes > 1).sum())} records carry more than one failure-mode flag")
    if (clean["torque_nm"] <= 0).any():
        warnings.append(f"{int((clean['torque_nm'] <= 0).sum())} records with non-positive torque")
    return ValidatedTable("ai4i", path.name, loading.sha256_file(path), len(raw), clean,
                          loading.rejected_tuples(df, reasons), warnings)


def init_schema(conn) -> None:
    loading.init_common_schema(conn)
    conn.executescript(db.load_sql("ai4i/schema.sql"))


def load_ai4i(db_path: str | Path | None = None, csv_path: str | Path | None = None,
              force: bool = False) -> LoadReport:
    t0 = time.time()
    path = find_ai4i_csv(explicit=csv_path)
    vt = validate(path)
    report = LoadReport("ai4i", "failed")
    conn = db.connect(db_path)
    conn.isolation_level = None
    try:
        init_schema(conn)
        last = loading.last_success(conn, "ai4i", TABLE)
        if (not force and last is not None and last["source_sha256"] == vt.sha256
                and db.table_count(conn, TABLE) == last["rows_loaded"]):
            report.status = "up_to_date"
            report.messages.append("Source file unchanged since the last successful load; nothing rewritten.")
            report.tables.append(TableReport(TABLE, path.name, vt.rows_read, vt.rows_valid,
                                             len(vt.rejected), "skipped", vt.warnings))
            return report
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(f"DELETE FROM {TABLE}")
            conn.execute("DELETE FROM rejected_records WHERE dataset = 'ai4i'")
            placeholders = ",".join("?" * len(DB_COLUMNS))
            conn.executemany(f"INSERT INTO {TABLE} ({','.join(DB_COLUMNS)}) VALUES ({placeholders})",
                             loading.rows_of(vt.clean, DB_COLUMNS))
            if vt.rejected:
                conn.executemany("INSERT INTO rejected_records (dataset, table_name, source_row, reason, raw_record) "
                                 "VALUES ('ai4i', ?, ?, ?, ?)", [(TABLE, *r) for r in vt.rejected])
            loaded = conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
            if loaded != vt.rows_valid:
                raise RuntimeError(f"expected {vt.rows_valid} rows, found {loaded}")
            loading.log_load(conn, "ai4i", vt, TABLE, "success", loaded, "; ".join(vt.warnings) or None)
            conn.execute("COMMIT")
        except Exception as exc:
            conn.execute("ROLLBACK")
            loading.log_load(conn, "ai4i", vt, TABLE, "failed", 0, str(exc))
            raise
        conn.execute("ANALYZE")
        report.status = "loaded"
        report.tables.append(TableReport(TABLE, path.name, vt.rows_read, loaded, len(vt.rejected),
                                         "success", vt.warnings))
        return report
    finally:
        conn.close()
        report.seconds = round(time.time() - t0, 2)
