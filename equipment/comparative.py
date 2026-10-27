"""Comparative view of the two datasets - only where a comparison is methodologically defensible.

No query in this module joins Azure and AI4I data: each dataset is summarised independently and the
results are placed side by side with explicit units and a "directly comparable?" flag.
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from . import db

AZURE_TABLES = ["azure_machines", "azure_telemetry", "azure_errors", "azure_maintenance", "azure_failures"]
AI4I_TABLES = ["ai4i_machine_data"]

COLUMNS = {
    "Azure": {
        "azure_machines": "machineID, model, age",
        "azure_telemetry": "datetime, machineID, volt, rotate, pressure, vibration (hourly, 2015)",
        "azure_errors": "datetime, machineID, errorID (error1-5)",
        "azure_maintenance": "datetime, machineID, comp (comp1-4)",
        "azure_failures": "datetime, machineID, failure (comp1-4)",
    },
    "AI4I 2020": {
        "ai4i_machine_data": "UDI, Product ID, Type (L/M/H), Air temperature [K], Process temperature [K], "
                             "Rotational speed [rpm], Torque [Nm], Tool wear [min], Machine failure, TWF, HDF, PWF, OSF, RNF",
    },
}

DEFINITIONS = pd.DataFrame([
    ("Azure", "failure (comp1-comp4)", "A component of a specific machine failed at a recorded timestamp. "
              "A machine can fail repeatedly over time.", "event (timestamped)"),
    ("Azure", "error (error1-error5)", "Operational error logged by a machine at a timestamp (not necessarily a failure).",
     "event (timestamped)"),
    ("Azure", "maintenance (comp1-comp4)", "A component was serviced/replaced; may be planned or the repair of a failure.",
     "event (timestamped)"),
    ("AI4I 2020", "Machine failure", "1 if the process record failed for at least one reason (label on the record).",
     "record (one process snapshot)"),
    ("AI4I 2020", "TWF", "Tool wear failure - tool replaced/failed in a 200-240 min wear window.", "record flag"),
    ("AI4I 2020", "HDF", "Heat dissipation failure - temperature difference < 8.6 K and speed < 1380 rpm.", "record flag"),
    ("AI4I 2020", "PWF", "Power failure - power below 3500 W or above 9000 W.", "record flag"),
    ("AI4I 2020", "OSF", "Overstrain failure - tool wear x torque above 11000/12000/13000 minNm (L/M/H).", "record flag"),
    ("AI4I 2020", "RNF", "Random failure - 0.1% chance regardless of settings.", "record flag"),
], columns=["Dataset", "Label", "Definition (published dataset descriptions)", "Observation unit"])

SUPPORTED = pd.DataFrame([
    ("Failure counts and rates", "Yes (events per machine / over time)", "Yes (per record, by type and condition)"),
    ("Failure trend over time", "Yes (hourly/monthly timestamps)", "No - UDI is an identifier, not a time stamp"),
    ("Failure by component / mode", "Yes (comp1-4)", "Yes (TWF/HDF/PWF/OSF/RNF, may co-occur)"),
    ("Error analytics", "Yes (errorID, repeated errors)", "No error log"),
    ("Telemetry / operating conditions", "Yes (4 hourly sensors per machine)", "Yes (5 process measurements per record)"),
    ("Before/after maintenance analysis", "Yes (maintenance timestamps + telemetry)", "No - no maintenance history"),
    ("Machine-level risk score", "Yes (100 machines)", "No - rows are not distinct machines"),
    ("Record/group-level risk indicator", "Not applicable", "Yes (operating-condition indicator)"),
    ("Machine age / model", "Yes", "No (machine type L/M/H only)"),
], columns=["Capability", "Azure", "AI4I 2020"])

LIMITATIONS = {
    "Azure": [
        "Simulated telemetry/event data for 100 machines over about one year; not a validated real-world benchmark.",
        "Failures, errors and maintenance are linked only by machine and time; causation is not established.",
        "743 of 3,286 maintenance rows coincide with a failure of the same component (repairs), so maintenance counts "
        "overlap with failure counts unless separated.",
        "Risk scores describe historical recorded events - they are not a validated future-failure prediction.",
    ],
    "AI4I 2020": [
        "Synthetic data: labels are produced from documented rules plus a random-failure term, so strong associations "
        "reflect how the data were constructed.",
        "10,000 independent records; UDI is a row identifier and records are not repeated measurements of one machine.",
        "No timestamps, no maintenance history, no error log: trends over time and maintenance effectiveness cannot be assessed.",
        "9 records have Machine failure=1 without a mode flag and 18 have RNF=1 with Machine failure=0; they are flagged, not corrected.",
        "The risk score is an operating-condition indicator, not a validated predictive-maintenance model.",
    ],
    "Together": [
        "Different observation units (timestamped events vs independent records), different sampling and label definitions.",
        "Raw failure counts must not be compared; at most, proportions with their own denominators are shown side by side.",
        "Azure machine-level risk scores and AI4I record-level indicator scores are built differently and are NOT comparable.",
        "The datasets share no identifiers and are never joined.",
    ],
}


def record_counts(conn: sqlite3.Connection) -> pd.DataFrame:
    rows = []
    for ds, tables in (("Azure", AZURE_TABLES), ("AI4I 2020", AI4I_TABLES)):
        for t in tables:
            rows.append({"Dataset": ds, "Table": t, "Rows": db.table_count(conn, t),
                         "Columns": COLUMNS[ds].get(t, "")})
    return pd.DataFrame(rows)


def quality_summary(conn: sqlite3.Connection) -> pd.DataFrame:
    """Per-dataset load/quality facts taken from the load log and rejected_records tables."""
    if not db.table_exists(conn, "data_load_log"):
        return pd.DataFrame()
    sql = """
        SELECT l.dataset AS dataset, l.table_name, l.source_file, l.rows_read, l.rows_loaded, l.rows_rejected,
               l.loaded_at, l.message AS warnings
        FROM data_load_log l
        WHERE l.status = 'success'
          AND l.load_id = (SELECT MAX(load_id) FROM data_load_log x
                           WHERE x.table_name = l.table_name AND x.status = 'success')
        ORDER BY l.dataset, l.table_name"""
    return db.read_df(conn, sql)


def failure_proportions(conn: sqlite3.Connection) -> pd.DataFrame:
    """Well-defined proportions, each with its own denominator and an explicit comparability verdict."""
    rows = []
    if db.table_count(conn, "azure_machines"):
        az = db.read_df(conn, """
            SELECT (SELECT COUNT(*) FROM azure_failures)                                 AS failures,
                   (SELECT COUNT(*) FROM azure_telemetry)                                AS machine_hours,
                   (SELECT COUNT(DISTINCT machine_id) FROM azure_failures)               AS machines_with_failure,
                   (SELECT COUNT(*) FROM azure_machines)                                 AS machines""").iloc[0]
        rows += [
            {"Measure": "Share of observation units with a recorded failure",
             "Azure": f"{100.0 * az.failures / az.machine_hours:.3f}% of machine-hours "
                      f"({int(az.failures):,} failures / {int(az.machine_hours):,} hourly readings)",
             "AI4I 2020": None, "Directly comparable?": "No"},
            {"Measure": "Share of machines with at least one failure",
             "Azure": f"{100.0 * az.machines_with_failure / az.machines:.1f}% ({int(az.machines_with_failure)} of {int(az.machines)} machines)",
             "AI4I 2020": "Not defined - rows are not distinct machines", "Directly comparable?": "No"},
        ]
    if db.table_count(conn, "ai4i_machine_data"):
        ai = db.read_df(conn, "SELECT COUNT(*) AS n, SUM(machine_failure) AS f FROM ai4i_machine_data").iloc[0]
        txt = f"{100.0 * ai.f / ai.n:.2f}% of records ({int(ai.f):,} failed / {int(ai.n):,} records)"
        if rows:
            rows[0]["AI4I 2020"] = txt
        else:
            rows.append({"Measure": "Share of observation units with a recorded failure", "Azure": None,
                         "AI4I 2020": txt, "Directly comparable?": "No"})
    return pd.DataFrame(rows)
