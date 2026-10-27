"""AI4I analytics: filters, query runner."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

import pandas as pd

from .. import db

# filter column -> (sql column, param stem)
RANGE_COLUMNS = {
    "air_temp": "air_temp_k",
    "process_temp": "process_temp_k",
    "rpm": "rotational_speed_rpm",
    "torque": "torque_nm",
    "tool_wear": "tool_wear_min",
}
MIN_GROUP_RECORDS = 30   # groups/bins with fewer records are flagged as low-sample


@dataclass(frozen=True)
class Filters:
    """Active AI4I filters. ``types`` None = all; ``ranges`` maps a key of RANGE_COLUMNS to (lo, hi)."""
    types: tuple | None = None
    ranges: tuple = field(default_factory=tuple)      # tuple of (key, lo, hi) - hashable for caching

    @classmethod
    def make(cls, types=None, **ranges) -> "Filters":
        for k in ranges:
            if k not in RANGE_COLUMNS:
                raise ValueError(f"unknown range filter '{k}'")
        return cls(tuple(types) if types is not None else None,
                   tuple((k, float(v[0]), float(v[1])) for k, v in sorted(ranges.items()) if v is not None))

    def where(self) -> tuple[str, dict]:
        parts, params = [], {}
        clause, p = db.in_clause("machine_type", self.types, "ty")
        if clause:
            parts.append(clause)
            params.update(p)
        for key, lo, hi in self.ranges:
            col = RANGE_COLUMNS[key]            # column name comes from the constant mapping above
            parts.append(f" AND {col} BETWEEN :lo_{key} AND :hi_{key} ")
            params[f"lo_{key}"], params[f"hi_{key}"] = lo, hi
        return "".join(parts), params


def query(conn: sqlite3.Connection, name: str, f: Filters | None = None, **extra) -> pd.DataFrame:
    sql = db.load_named_queries("ai4i/analytics.sql")[name]
    clause, params = (f or Filters()).where()
    return db.read_df(conn, sql.format(where=clause), {**params, **extra, "min_n": extra.get("min_n", MIN_GROUP_RECORDS)})


def bounds(conn: sqlite3.Connection) -> dict:
    return query(conn, "bounds").iloc[0].to_dict()


def overview(conn: sqlite3.Connection, f: Filters | None = None) -> dict:
    return query(conn, "overview", f).iloc[0].to_dict()
