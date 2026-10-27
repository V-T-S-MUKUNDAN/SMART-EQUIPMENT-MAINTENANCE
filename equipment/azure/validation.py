"""Validation of the five Azure PdM CSV files (no database access).

Rows that fail validation are *collected with a reason* and later stored in ``rejected_records``;
nothing is silently dropped and no value is altered or imputed.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .. import loading
from ..loading import ValidatedTable, ValidationError

TS_FORMAT = "%Y-%m-%d %H:%M:%S"

FILES = {
    "machines": "PdM_machines.csv",
    "telemetry": "PdM_telemetry.csv",
    "errors": "PdM_errors.csv",
    "maintenance": "PdM_maint.csv",
    "failures": "PdM_failures.csv",
}
TABLES = {
    "machines": "azure_machines",
    "telemetry": "azure_telemetry",
    "errors": "azure_errors",
    "maintenance": "azure_maintenance",
    "failures": "azure_failures",
}
# Row counts documented for the public dataset - used only as a *comparison*, never forced.
EXPECTED_COUNTS = {"machines": 100, "telemetry": 876_100, "failures": 761, "errors": 3_919, "maintenance": 3_286}

SENSORS = ["volt", "rotate", "pressure", "vibration"]


def find_files(data_dir: Path) -> dict[str, Path]:
    paths = {k: Path(data_dir) / v for k, v in FILES.items()}
    missing = [p.name for p in paths.values() if not p.exists()]
    if missing:
        raise loading.MissingDatasetFilesError(
            f"Azure dataset file(s) not found in '{data_dir}': {', '.join(missing)}. "
            "Place the five PdM_*.csv files in that folder (or set SE_AZURE_DIR)."
        )
    return paths


class _Reasons:
    """Row-level rejection reasons; the first reason that applies to a row wins."""

    def __init__(self, index: pd.Index):
        self.s = pd.Series("", index=index, dtype=object)

    def flag(self, mask: pd.Series, text: str) -> None:
        self.s[mask & (self.s == "")] = text


def _parse_ts(col: pd.Series) -> pd.Series:
    return pd.to_datetime(col, format=TS_FORMAT, errors="coerce")


def _int(col: pd.Series) -> pd.Series:
    return pd.to_numeric(col, errors="coerce")


def _finish(name: str, path: Path, sha: str, raw: pd.DataFrame, reasons: _Reasons,
            clean: pd.DataFrame, warnings: list[str]) -> ValidatedTable:
    rej = loading.rejected_tuples(raw, reasons.s)
    return ValidatedTable(name, path.name, sha, len(raw), clean, rej, warnings)


def validate_machines(path: Path) -> ValidatedTable:
    raw = loading.read_csv_text(path)
    loading.require_columns(raw, ["machineID", "model", "age"], path.name)
    raw.index += 1
    raw = raw.apply(lambda c: c.str.strip())
    r = _Reasons(raw.index)
    mid, age = _int(raw["machineID"]), _int(raw["age"])
    r.flag(mid.isna() | (mid != mid.round()), "machineID missing or not an integer")
    r.flag(raw["model"] == "", "model missing")
    r.flag(age.isna() | (age != age.round()), "age missing or not an integer")
    r.flag(age < 0, "age is negative")
    ok = r.s == ""
    r.flag(ok & mid.duplicated(keep="first"), "duplicate machineID (first occurrence kept)")
    ok = r.s == ""
    clean = pd.DataFrame({"machine_id": mid[ok].astype(int), "model": raw.loc[ok, "model"],
                          "age": age[ok].astype(int)}).reset_index(drop=True)
    return _finish("machines", path, loading.sha256_file(path), raw, r, clean, [])


def _event_table(name: str, path: Path, label_col: str, out_label: str, known: set[int]) -> ValidatedTable:
    raw = loading.read_csv_text(path)
    loading.require_columns(raw, ["datetime", "machineID", label_col], path.name)
    raw.index += 1
    raw = raw.apply(lambda c: c.str.strip())
    r = _Reasons(raw.index)
    ts, mid = _parse_ts(raw["datetime"]), _int(raw["machineID"])
    r.flag(ts.isna(), f"datetime missing or not in format {TS_FORMAT}")
    r.flag(mid.isna() | (mid != mid.round()), "machineID missing or not an integer")
    r.flag(raw[label_col] == "", f"{label_col} missing")
    r.flag(~mid.isin(known), "machineID not present in PdM_machines.csv")
    ok = r.s == ""
    key = pd.DataFrame({"m": mid, "t": ts, "l": raw[label_col]})
    r.flag(ok & key.duplicated(keep="first"), "duplicate (machineID, datetime, label) - first occurrence kept")
    ok = r.s == ""
    clean = pd.DataFrame({
        "machine_id": mid[ok].astype(int),
        "ts": ts[ok].dt.strftime(TS_FORMAT),
        out_label: raw.loc[ok, label_col],
    }).sort_values(["ts", "machine_id", out_label]).reset_index(drop=True)
    return _finish(name, path, loading.sha256_file(path), raw, r, clean, [])


def validate_telemetry(path: Path, known: set[int]) -> ValidatedTable:
    raw = loading.read_csv_text(path)
    loading.require_columns(raw, ["datetime", "machineID", *SENSORS], path.name)
    raw.index += 1
    raw = raw.apply(lambda c: c.str.strip())
    r = _Reasons(raw.index)
    ts, mid = _parse_ts(raw["datetime"]), _int(raw["machineID"])
    r.flag(ts.isna(), f"datetime missing or not in format {TS_FORMAT}")
    r.flag(mid.isna() | (mid != mid.round()), "machineID missing or not an integer")
    r.flag(~mid.isin(known), "machineID not present in PdM_machines.csv")
    values = {}
    warnings: list[str] = []
    for s in SENSORS:
        v = _int(raw[s])
        # empty cell = genuine missing reading (kept as NULL); non-empty but non-numeric = reject
        r.flag(v.isna() & (raw[s] != ""), f"{s} is not numeric")
        values[s] = v
    ok = r.s == ""
    key = pd.DataFrame({"m": mid, "t": ts})
    r.flag(ok & key.duplicated(keep="first"), "duplicate (machineID, datetime) - first occurrence kept")
    ok = r.s == ""
    clean = pd.DataFrame({"machine_id": mid[ok].astype(int), "ts": ts[ok].dt.strftime(TS_FORMAT),
                          **{s: values[s][ok] for s in SENSORS}})
    clean = clean.sort_values(["machine_id", "ts"]).reset_index(drop=True)
    for s in SENSORS:
        n_null = int(clean[s].isna().sum())
        if n_null:
            warnings.append(f"{n_null} missing {s} readings kept as NULL")
        n_neg = int((clean[s] < 0).sum())
        if n_neg:
            warnings.append(f"{n_neg} negative {s} readings (kept as recorded)")
    return _finish("telemetry", path, loading.sha256_file(path), raw, r, clean, warnings)


def validate_all(data_dir: Path) -> dict[str, ValidatedTable]:
    """Validate all five files. Raises MissingDatasetFilesError / ValidationError for fatal problems."""
    paths = find_files(data_dir)
    machines = validate_machines(paths["machines"])
    known = set(machines.clean["machine_id"].tolist())
    out = {
        "machines": machines,
        "telemetry": validate_telemetry(paths["telemetry"], known),
        "errors": _event_table("errors", paths["errors"], "errorID", "error_type", known),
        "maintenance": _event_table("maintenance", paths["maintenance"], "comp", "component", known),
        "failures": _event_table("failures", paths["failures"], "failure", "component", known),
    }
    for name, vt in out.items():
        exp = EXPECTED_COUNTS.get(name)
        if exp is not None and vt.rows_read != exp:
            vt.warnings.append(f"{vt.rows_read:,} rows read but the public dataset is documented to have {exp:,}")
    return out
