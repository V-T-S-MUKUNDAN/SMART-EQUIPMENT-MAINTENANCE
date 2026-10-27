"""Module 01 helpers: is the database / dataset ready for analysis? (works for both datasets)"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from . import db, loading

AZURE_EXPECTED = {"azure_machines": 100, "azure_telemetry": 876_100, "azure_errors": 3_919,
                  "azure_maintenance": 3_286, "azure_failures": 761}
AI4I_EXPECTED = {"ai4i_machine_data": 10_000}


def table_status(conn: sqlite3.Connection, expected: dict[str, int]) -> pd.DataFrame:
    rows = []
    for table, exp in expected.items():
        n = db.table_count(conn, table)
        status = "missing/empty" if n == 0 else ("matches documented count" if n == exp else "differs from documented count")
        rows.append({"Table": table, "Rows in database": n, "Documented count": exp, "Status": status})
    return pd.DataFrame(rows)


def index_list(conn: sqlite3.Connection, prefix: str) -> pd.DataFrame:
    sql = ("SELECT tbl_name AS \"Table\", name AS \"Index\", sql AS \"Definition\" FROM sqlite_master "
           "WHERE type = 'index' AND tbl_name LIKE ? AND sql IS NOT NULL ORDER BY tbl_name, name")
    return db.read_df(conn, sql, (f"{prefix}%",))


def integrity(conn: sqlite3.Connection) -> dict:
    return loading.integrity_report(conn)


def load_history(conn: sqlite3.Connection, dataset: str, limit: int = 25) -> pd.DataFrame:
    if not db.table_exists(conn, "data_load_log"):
        return pd.DataFrame()
    return db.read_df(conn, "SELECT load_id, table_name, source_file, rows_read, rows_loaded, rows_rejected, "
                            "status, loaded_at, message FROM data_load_log WHERE dataset = ? "
                            "ORDER BY load_id DESC LIMIT ?", (dataset, limit))


def rejected(conn: sqlite3.Connection, dataset: str, limit: int = 500) -> pd.DataFrame:
    if not db.table_exists(conn, "rejected_records"):
        return pd.DataFrame()
    return db.read_df(conn, "SELECT table_name, source_row, reason, raw_record FROM rejected_records "
                            "WHERE dataset = ? ORDER BY reject_id LIMIT ?", (dataset, limit))


def verdict(conn: sqlite3.Connection, dataset: str, expected: dict[str, int]) -> tuple[bool, list[str]]:
    """Ready = every table populated, integrity ok, no FK violations, last load successful."""
    problems = []
    st = table_status(conn, expected)
    for _, r in st.iterrows():
        if r["Rows in database"] == 0:
            problems.append(f"{r['Table']} is empty or missing")
    inte = integrity(conn)
    if not inte["integrity_ok"]:
        problems.append("PRAGMA integrity_check reported problems")
    if inte["foreign_key_violations"]:
        problems.append(f"{inte['foreign_key_violations']} foreign-key violations")
    hist = load_history(conn, dataset, 1)
    if hist.empty:
        problems.append("no load recorded in data_load_log")
    elif hist.iloc[0]["status"] == "failed":
        problems.append("the most recent load attempt failed (data from the previous successful load is intact)")
    return (not problems), problems


def query_plan(conn: sqlite3.Connection, sql: str, params: dict | None = None) -> list[str]:
    """EXPLAIN QUERY PLAN lines for a parameterised query."""
    rows = conn.execute("EXPLAIN QUERY PLAN " + sql, params or {}).fetchall()
    return [r[3] for r in rows]


def file_facts(path: Path) -> dict:
    """Cheap facts about a source file (existence, size, data-row count, sha256 prefix)."""
    path = Path(path)
    if not path.exists():
        return {"File": path.name, "Found": False, "Size (MB)": None, "Data rows": None, "SHA-256": None}
    with open(path, "rb") as fh:
        n = sum(1 for _ in fh) - 1
    return {"File": path.name, "Found": True, "Size (MB)": round(path.stat().st_size / 1e6, 2),
            "Data rows": max(n, 0), "SHA-256": loading.sha256_file(path)[:16] + "..."}
