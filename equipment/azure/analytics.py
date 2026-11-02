"""Azure analytics: thin Python wrappers that bind parameters and run the named SQL queries."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from .. import db


def _day(value) -> str:
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


@dataclass(frozen=True)
class Filters:
    """Active dashboard filters. ``None`` for machines/components means 'no restriction'.

    * start/end are inclusive calendar days.
    * components restrict failure and maintenance events (errors are typed differently and are not filtered by it).
    """
    start: str
    end: str
    machines: tuple | None = None
    components: tuple | None = None

    def __post_init__(self):
        # normalise so date objects, strings and Streamlit values all behave (and hash) identically
        object.__setattr__(self, "start", _day(self.start))
        object.__setattr__(self, "end", _day(self.end))
        if self.machines is not None:
            object.__setattr__(self, "machines", tuple(int(m) for m in self.machines))
        if self.components is not None:
            object.__setattr__(self, "components", tuple(self.components))

    @property
    def end_excl(self) -> str:
        return (datetime.strptime(_day(self.end), "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

    def render(self, sql: str) -> tuple[str, dict]:
        mclause, mparams = db.in_clause("machine_id", self.machines, "mf")
        cclause, cparams = db.in_clause("component", self.components, "cf")
        params = {"start": _day(self.start), "end_excl": self.end_excl, **mparams, **cparams}
        return sql.format(machine_filter=mclause, component_filter=cclause), params

    def key(self) -> tuple:
        return (_day(self.start), _day(self.end), self.machines, self.components)


def query(conn: sqlite3.Connection, name: str, filters: Filters | None = None, **extra) -> pd.DataFrame:
    """Run ``-- name: <name>`` from sql/azure/analytics.sql with bound parameters."""
    sql = db.load_named_queries("azure/analytics.sql")[name]
    if filters is None:
        params: dict = {}
    else:
        sql, params = filters.render(sql)
    return db.read_df(conn, sql, {**params, **extra})


def data_bounds(conn: sqlite3.Connection) -> tuple[str, str]:
    row = query(conn, "data_bounds").iloc[0]
    return str(row["min_ts"])[:10], str(row["max_ts"])[:10]


def default_start(conn: sqlite3.Connection) -> str:
    """First telemetry day. Maintenance history starts earlier, so data_bounds alone would add empty months."""
    first = query(conn, "first_telemetry").iloc[0]["min_ts"]
    return data_bounds(conn)[0] if first is None else str(first)[:10]


def default_filters(conn: sqlite3.Connection) -> Filters:
    return Filters(default_start(conn), data_bounds(conn)[1])


def kpis(conn: sqlite3.Connection, f: Filters) -> dict:
    return query(conn, "kpis", f).iloc[0].to_dict()
