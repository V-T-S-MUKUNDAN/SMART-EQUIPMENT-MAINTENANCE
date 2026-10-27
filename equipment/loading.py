"""Utilities shared by the Azure and AI4I ingestion pipelines."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import db


class ValidationError(Exception):
    """The source file cannot be loaded at all (missing columns, unreadable, ...)."""


class MissingDatasetFilesError(FileNotFoundError):
    """One or more required dataset files were not found."""


@dataclass
class ValidatedTable:
    """Result of validating one CSV file (nothing has been written to the DB yet)."""
    name: str
    source_file: str
    sha256: str
    rows_read: int
    clean: pd.DataFrame
    rejected: list[tuple[int, str, str]] = field(default_factory=list)  # (source_row, reason, raw_json)
    warnings: list[str] = field(default_factory=list)

    @property
    def rows_valid(self) -> int:
        return len(self.clean)


@dataclass
class TableReport:
    table: str
    source_file: str
    rows_read: int
    rows_loaded: int
    rows_rejected: int
    status: str            # success | skipped | failed
    warnings: list[str] = field(default_factory=list)


@dataclass
class LoadReport:
    dataset: str
    status: str            # loaded | up_to_date | failed
    tables: list[TableReport] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status in ("loaded", "up_to_date")

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([t.__dict__ for t in self.tables])


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def read_csv_text(path: Path) -> pd.DataFrame:
    """Read every cell as text (no type guessing, no NA coercion); handles a UTF-8 BOM."""
    try:
        return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except pd.errors.EmptyDataError as exc:
        raise ValidationError(f"{path.name} is empty") from exc
    except (pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise ValidationError(f"{path.name} could not be parsed as CSV: {exc}") from exc


def require_columns(df: pd.DataFrame, required: list[str], filename: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValidationError(
            f"{filename}: missing required column(s) {missing}. Found columns: {list(df.columns)}"
        )


def rejected_tuples(raw: pd.DataFrame, reasons: pd.Series) -> list[tuple[int, str, str]]:
    """Collect (row_number, reason, raw_json) for rows with a non-empty reason."""
    bad = reasons[reasons != ""]
    out = []
    for idx, reason in bad.items():
        out.append((int(idx), str(reason), json.dumps(raw.loc[idx].to_dict(), ensure_ascii=False)))
    return out


def rows_of(df: pd.DataFrame, cols: list[str]) -> list[tuple]:
    """DataFrame -> list of tuples of native Python values (sqlite3 cannot bind numpy scalars)."""
    return list(zip(*(df[c].tolist() for c in cols)))


def last_success(conn: sqlite3.Connection, dataset: str, table: str):
    return conn.execute(
        "SELECT source_sha256, rows_loaded FROM data_load_log "
        "WHERE dataset = ? AND table_name = ? AND status = 'success' ORDER BY load_id DESC LIMIT 1",
        (dataset, table),
    ).fetchone()


def log_load(conn: sqlite3.Connection, dataset: str, vt: ValidatedTable, table: str,
             status: str, rows_loaded: int, message: str | None = None) -> None:
    conn.execute(
        "INSERT INTO data_load_log (dataset, table_name, source_file, source_sha256, rows_read, "
        "rows_loaded, rows_rejected, status, message) VALUES (?,?,?,?,?,?,?,?,?)",
        (dataset, table, vt.source_file, vt.sha256, vt.rows_read, rows_loaded,
         len(vt.rejected), status, message),
    )


def init_common_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(db.load_sql("common/schema.sql"))


def integrity_report(conn: sqlite3.Connection) -> dict:
    """PRAGMA integrity_check + foreign_key_check summary."""
    integ = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    return {
        "integrity_ok": integ == ["ok"],
        "integrity_messages": integ,
        "foreign_key_violations": len(fk),
    }
