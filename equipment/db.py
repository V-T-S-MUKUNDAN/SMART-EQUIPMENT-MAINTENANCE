"""SQLite connection helpers: read-only access, busy timeouts, parameterised queries."""
from __future__ import annotations

import math
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import pandas as pd

from . import config


class DatabaseNotFoundError(FileNotFoundError):
    """The SQLite file does not exist yet (run scripts/load_data.py)."""


def _register_functions(conn: sqlite3.Connection) -> None:
    # SQLite builds without SQLITE_ENABLE_MATH_FUNCTIONS lack SQRT/PI; provide fallbacks.
    conn.create_function("sqrt", 1, lambda x: None if x is None or x < 0 else math.sqrt(x), deterministic=True)
    conn.create_function("pi", 0, lambda: math.pi, deterministic=True)


def connect(path: str | Path | None = None, read_only: bool = False, timeout: float = 30.0) -> sqlite3.Connection:
    """Open a connection. ``read_only`` raises DatabaseNotFoundError if the file is missing."""
    p = Path(path) if path else config.db_path()
    if read_only:
        if not p.exists():
            raise DatabaseNotFoundError(
                f"Database not found at '{p}'. Build it with: python scripts/load_data.py"
            )
        conn = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True, timeout=timeout)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(p), timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
    _register_functions(conn)
    return conn


def db_version(path: str | Path | None = None) -> tuple[int, int] | None:
    """(mtime_ns, size) of the DB file. Used as a Streamlit cache key so caches never go stale."""
    p = Path(path) if path else config.db_path()
    try:
        st = p.stat()
    except FileNotFoundError:
        return None
    return (st.st_mtime_ns, st.st_size)


@lru_cache(maxsize=None)
def load_sql(relative: str) -> str:
    """Read a .sql file from the sql/ folder (cached)."""
    return (config.SQL_DIR / relative).read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def load_named_queries(relative: str) -> dict[str, str]:
    """Parse a .sql file made of ``-- name: <id>`` sections into {id: sql}."""
    out: dict[str, list[str]] = {}
    current = None
    for line in load_sql(relative).splitlines():
        if line.startswith("-- name:"):
            current = line.split(":", 1)[1].strip()
            out[current] = []
        elif current is not None:
            out[current].append(line)
    cleaned = {}
    for name, lines in out.items():
        # comments written above the *next* query end up at the tail of this section: drop them
        while lines and (not lines[-1].strip() or lines[-1].lstrip().startswith("--")):
            lines.pop()
        cleaned[name] = "\n".join(lines).strip().rstrip(";")
    return cleaned


def read_df(conn: sqlite3.Connection, sql: str, params: Mapping | Sequence | None = None) -> pd.DataFrame:
    """Run a parameterised query and return a DataFrame."""
    cur = conn.execute(sql, params or {})
    cols = [d[0] for d in cur.description]
    return pd.DataFrame([tuple(r) for r in cur.fetchall()], columns=cols)


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?", (name,)
    ).fetchone()
    return row is not None


def table_count(conn: sqlite3.Connection, name: str) -> int:
    if not table_exists(conn, name):
        return 0
    return conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]  # name is a code constant


def in_clause(column: str, values: Iterable | None, prefix: str) -> tuple[str, dict]:
    """Build ``AND column IN (:p0, :p1, ...)`` using bound parameters only.

    Returns ('', {}) when ``values`` is None (= no filter). An empty sequence yields a clause that
    matches nothing, so an empty selection shows an empty result rather than everything.
    """
    if values is None:
        return "", {}
    values = list(values)
    if not values:
        return f" AND 1 = 0 ", {}
    names = [f"{prefix}{i}" for i in range(len(values))]
    return f" AND {column} IN ({', '.join(':' + n for n in names)}) ", dict(zip(names, values))
