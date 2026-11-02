"""Idempotent, transactional loader for the Azure PdM dataset.

* All five files are validated first (no DB writes happen if a file is missing/unreadable).
* If every source file has the same SHA-256 as the last successful load and the tables hold the
  recorded number of rows, nothing is rewritten (safe re-runs, no duplicates).
* Otherwise the five azure_* tables are replaced inside ONE transaction; any error rolls back,
  leaving the previous data untouched.
* AI4I tables are never touched by this module.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .. import config, db, loading
from ..loading import LoadReport, TableReport
from . import validation as V

LOAD_ORDER = ["machines", "telemetry", "errors", "maintenance", "failures"]
DELETE_ORDER = ["telemetry", "errors", "maintenance", "failures", "machines"]  # children first (FKs)

INSERT_SQL = {
    "machines": ("INSERT INTO azure_machines (machine_id, model, age) VALUES (?,?,?)",
                 ["machine_id", "model", "age"]),
    "telemetry": ("INSERT INTO azure_telemetry (machine_id, ts, volt, rotate, pressure, vibration) "
                  "VALUES (?,?,?,?,?,?)", ["machine_id", "ts", *V.SENSORS]),
    "errors": ("INSERT INTO azure_errors (machine_id, ts, error_type) VALUES (?,?,?)",
               ["machine_id", "ts", "error_type"]),
    "maintenance": ("INSERT INTO azure_maintenance (machine_id, ts, component) VALUES (?,?,?)",
                    ["machine_id", "ts", "component"]),
    "failures": ("INSERT INTO azure_failures (machine_id, ts, component) VALUES (?,?,?)",
                 ["machine_id", "ts", "component"]),
}

def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables, indexes and views if missing (never drops data)."""
    loading.init_common_schema(conn)
    conn.executescript(db.load_sql("azure/schema.sql"))
    conn.executescript(db.load_sql("azure/views.sql"))


def _up_to_date(conn: sqlite3.Connection, validated: dict) -> bool:
    for name, vt in validated.items():
        table = V.TABLES[name]
        last = loading.last_success(conn, "azure", table)
        if last is None or last["source_sha256"] != vt.sha256:
            return False
        if db.table_count(conn, table) != last["rows_loaded"]:
            return False
    return True


def load_azure(db_path: str | Path | None = None, data_dir: str | Path | None = None,
               force: bool = False) -> LoadReport:
    t0 = time.time()
    data_dir = Path(data_dir) if data_dir else config.azure_dir()
    report = LoadReport(dataset="azure", status="failed")

    validated = V.validate_all(data_dir)       # may raise (missing files / bad schema): DB untouched

    conn = db.connect(db_path)
    conn.isolation_level = None                # explicit transaction control
    try:
        init_schema(conn)
        if not force and _up_to_date(conn, validated):
            report.status = "up_to_date"
            report.messages.append("Source files unchanged since the last successful load; nothing rewritten.")
            for name in LOAD_ORDER:
                vt = validated[name]
                report.tables.append(TableReport(V.TABLES[name], vt.source_file, vt.rows_read,
                                                 vt.rows_valid, len(vt.rejected), "skipped", vt.warnings))
            return report

        conn.execute("BEGIN IMMEDIATE")
        try:
            for name in DELETE_ORDER:
                conn.execute(f"DELETE FROM {V.TABLES[name]}")
            conn.execute("DELETE FROM rejected_records WHERE dataset = 'azure'")
            for name in LOAD_ORDER:
                vt = validated[name]
                sql, cols = INSERT_SQL[name]
                conn.executemany(sql, loading.rows_of(vt.clean, cols))
                if vt.rejected:
                    conn.executemany(
                        "INSERT INTO rejected_records (dataset, table_name, source_row, reason, raw_record) "
                        "VALUES ('azure', ?, ?, ?, ?)",
                        [(V.TABLES[name], *rec) for rec in vt.rejected])
                loaded = conn.execute(f"SELECT COUNT(*) FROM {V.TABLES[name]}").fetchone()[0]
                if loaded != vt.rows_valid:
                    raise RuntimeError(f"{V.TABLES[name]}: expected {vt.rows_valid} rows, found {loaded}")
                loading.log_load(conn, "azure", vt, V.TABLES[name], "success", loaded,
                                 "; ".join(vt.warnings) or None)
                report.tables.append(TableReport(V.TABLES[name], vt.source_file, vt.rows_read, loaded,
                                                 len(vt.rejected), "success", vt.warnings))
            conn.execute("COMMIT")
        except Exception as exc:
            conn.execute("ROLLBACK")
            report.tables.clear()
            report.messages.append(f"Load failed and was rolled back: {exc}")
            for name in LOAD_ORDER:
                vt = validated[name]
                loading.log_load(conn, "azure", vt, V.TABLES[name], "failed", 0, str(exc))
            raise
        conn.execute("ANALYZE")
        report.status = "loaded"
        return report
    finally:
        conn.close()
        report.seconds = round(time.time() - t0, 2)
