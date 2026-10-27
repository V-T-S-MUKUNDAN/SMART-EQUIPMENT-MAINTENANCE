"""Small, hand-controlled datasets used by the tests (NOT used by the application).

The Azure set is crafted so that:
  * machine 1 has two failures at the SAME timestamp (different components)
  * most failures have a coincident maintenance row (failure-linked) and some maintenance is planned
  * a naive machines x failures x errors x maintenance join inflates counts dramatically
  * machine 3 has no failures; one event has a truncated before-window, one a truncated after-window,
    and one has an all-zero vibration baseline
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

TS = "%Y-%m-%d %H:%M:%S"


def azure_frames() -> dict[str, pd.DataFrame]:
    machines = pd.DataFrame({"machineID": [1, 2, 3], "model": ["model1", "model2", "model1"], "age": [5, 10, 15]})
    failures = pd.DataFrame([
        ("2015-01-10 06:00:00", 1, "comp1"), ("2015-02-10 06:00:00", 1, "comp1"), ("2015-02-10 06:00:00", 1, "comp2"),
        ("2015-01-20 06:00:00", 2, "comp3"),
    ], columns=["datetime", "machineID", "failure"])
    errors = pd.DataFrame([
        ("2015-01-09 06:00:00", 1, "error1"), ("2015-01-10 02:00:00", 1, "error2"), ("2015-02-09 12:00:00", 1, "error1"),
        ("2015-01-19 06:00:00", 2, "error1"), ("2015-03-01 06:00:00", 2, "error4"), ("2015-01-05 06:00:00", 3, "error5"),
    ], columns=["datetime", "machineID", "errorID"])
    maint = pd.DataFrame([
        ("2015-01-10 06:00:00", 1, "comp1"),   # failure-linked
        ("2015-01-25 06:00:00", 1, "comp2"),   # planned
        ("2015-02-10 06:00:00", 1, "comp1"),   # failure-linked
        ("2015-02-10 06:00:00", 1, "comp2"),   # failure-linked
        ("2015-03-05 06:00:00", 1, "comp3"),   # planned
        ("2015-01-20 06:00:00", 2, "comp3"),   # failure-linked
        ("2015-02-15 06:00:00", 2, "comp1"),   # planned
        ("2015-03-09 06:00:00", 2, "comp2"),   # planned, after-window truncated by end of telemetry
        ("2015-02-01 06:00:00", 3, "comp4"),   # planned, vibration baseline all zero
        ("2015-01-02 12:00:00", 3, "comp1"),   # planned, before-window truncated (telemetry starts 01-01 06:00)
    ], columns=["datetime", "machineID", "comp"])
    hours = pd.date_range("2015-01-01 06:00:00", "2015-03-10 05:00:00", freq="h")
    rng = np.random.RandomState(7)
    parts = []
    for m in (1, 2, 3):
        df = pd.DataFrame({
            "datetime": hours.strftime(TS), "machineID": m,
            "volt": rng.normal(170, 15, len(hours)).round(6), "rotate": rng.normal(446, 50, len(hours)).round(6),
            "pressure": rng.normal(100, 10, len(hours)).round(6), "vibration": rng.normal(40, 5, len(hours)).round(6)})
        if m == 3:
            zero = (hours >= "2015-01-25 06:00:00") & (hours < "2015-02-01 06:00:00")
            df.loc[zero, "vibration"] = 0.0
        parts.append(df)
    telemetry = pd.concat(parts, ignore_index=True)
    return {"machines": machines, "telemetry": telemetry, "errors": errors, "maint": maint, "failures": failures}


FILENAMES = {"machines": "PdM_machines.csv", "telemetry": "PdM_telemetry.csv", "errors": "PdM_errors.csv",
             "maint": "PdM_maint.csv", "failures": "PdM_failures.csv"}


def write_azure_csvs(folder: Path, frames: dict | None = None) -> dict:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    frames = frames or azure_frames()
    for key, df in frames.items():
        df.to_csv(folder / FILENAMES[key], index=False)
    return frames


AI4I_COLUMNS = ["UDI", "Product ID", "Type", "Air temperature [K]", "Process temperature [K]",
                "Rotational speed [rpm]", "Torque [Nm]", "Tool wear [min]", "Machine failure", "TWF", "HDF", "PWF", "OSF", "RNF"]


def ai4i_frame() -> pd.DataFrame:
    """60 records, hand-built: typical rows + one record per failure situation under test."""
    rows = []
    rng = np.random.RandomState(3)
    types = ["L", "M", "H"]
    for i in range(1, 49):
        t = types[i % 3]
        rows.append([i, f"{t}{47000 + i}", t, round(298 + rng.rand() * 4, 1), round(308 + rng.rand() * 4, 1),
                     int(rng.randint(1400, 1800)), round(30 + rng.rand() * 15, 1), int(rng.randint(0, 190)), 0, 0, 0, 0, 0, 0])
    specials = [
        # UDI, type, air, proc, rpm, torque, wear, MF, TWF, HDF, PWF, OSF, RNF
        (49, "L", 300.0, 308.5, 1300, 40.0, 100, 1, 0, 1, 0, 0, 0),   # HDF rule: diff 8.5 < 8.6 and rpm < 1380
        (50, "M", 300.0, 310.0, 2500, 60.0, 20, 1, 0, 0, 1, 0, 0),    # PWF: power ~15.7 kW
        (51, "H", 300.0, 310.0, 1500, 70.0, 200, 1, 0, 0, 0, 1, 0),   # OSF: 14000 > 13000
        (52, "L", 300.0, 310.0, 1500, 45.0, 230, 1, 1, 0, 0, 0, 0),   # TWF
        (53, "L", 300.0, 310.0, 1500, 70.0, 210, 1, 0, 0, 1, 1, 0),   # multi-mode: PWF + OSF
        (54, "M", 300.0, 310.0, 1500, 40.0, 50, 1, 0, 0, 0, 0, 0),    # failure WITHOUT any mode flag
        (55, "L", 300.0, 310.0, 1500, 40.0, 50, 0, 0, 0, 0, 0, 1),    # RNF flag but Machine failure = 0
        (56, "L", 300.0, 310.0, 1500, 40.0, 60, 1, 0, 0, 0, 0, 1),    # genuine random failure
        (57, "H", 301.0, 311.0, 1450, 38.0, 245, 0, 0, 0, 0, 0, 0),   # high wear, no failure
        (58, "M", 299.0, 309.0, 1600, 35.0, 90, 0, 0, 0, 0, 0, 0),
        (59, "H", 299.5, 309.5, 1700, 33.0, 10, 0, 0, 0, 0, 0, 0),
        (60, "L", 302.0, 312.0, 1550, 41.0, 150, 0, 0, 0, 0, 0, 0),
    ]
    for u, t, a, p, r, tq, w, mf, twf, hdf, pwf, osf, rnf in specials:
        rows.append([u, f"{t}{47000 + u}", t, a, p, r, tq, w, mf, twf, hdf, pwf, osf, rnf])
    return pd.DataFrame(rows, columns=AI4I_COLUMNS)


def write_ai4i_csv(folder: Path, name: str = "ai4i2020.csv", df: pd.DataFrame | None = None, bom: bool = True) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    (df if df is not None else ai4i_frame()).to_csv(path, index=False, encoding="utf-8-sig" if bom else "utf-8")
    return path
