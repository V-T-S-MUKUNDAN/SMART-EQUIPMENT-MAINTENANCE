"""Module 3 - observed telemetry change around a maintenance event (Azure)."""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from .. import db

METRICS = ["volt", "rotate", "pressure", "vibration"]
EPS = 1e-9
Q = "azure/maintenance.sql"


def events(conn: sqlite3.Connection, machine_id: int) -> pd.DataFrame:
    return db.read_df(conn, db.load_named_queries(Q)["maintenance_events"], {"machine_id": int(machine_id)})


def pct_change(before, after):
    """Percentage change, or None when the baseline is missing / zero (not mathematically meaningful)."""
    if before is None or after is None or pd.isna(before) or pd.isna(after) or abs(before) < EPS:
        return None
    return (after - before) / abs(before) * 100.0


def compare(conn: sqlite3.Connection, machine_id: int, ts: str, window_days: int = 7,
            min_obs: int = 24) -> dict:
    """Compare telemetry in [ts - W, ts) with (ts, ts + W].

    Returns a dict: ``table`` (one row per metric with before/after stats, differences, pct change),
    ``n_before`` / ``n_after``, ``sufficient`` (both windows >= min_obs), ``messages``, ``confounders``.
    """
    q = db.load_named_queries(Q)
    p = {"machine_id": int(machine_id), "ts": ts, "hours": int(window_days) * 24}
    stats = db.read_df(conn, q["before_after_stats"], p)
    conf = db.read_df(conn, q["window_confounders"], p)
    result = {"table": pd.DataFrame(), "n_before": 0, "n_after": 0, "sufficient": False,
              "messages": [], "confounders": conf, "window_days": int(window_days), "min_obs": min_obs}
    if stats.empty:
        result["messages"].append("No telemetry readings exist in either comparison window.")
        return result

    expected = int(window_days) * 24
    rows = []
    for m in METRICS:
        b = stats[(stats.metric == m) & (stats.period == "before")]
        a = stats[(stats.metric == m) & (stats.period == "after")]
        b = b.iloc[0] if len(b) else None
        a = a.iloc[0] if len(a) else None
        row = {"metric": m}
        for stat in ["n_obs", "mean", "median", "min", "max", "std"]:
            row[f"before_{stat}"] = None if b is None else b[stat]
            row[f"after_{stat}"] = None if a is None else a[stat]
        for stat in ["mean", "median", "min", "max", "std"]:
            bv, av = row[f"before_{stat}"], row[f"after_{stat}"]
            ok = bv is not None and av is not None and not (pd.isna(bv) or pd.isna(av))
            row[f"change_{stat}"] = (av - bv) if ok else None
        row["pct_change_mean"] = pct_change(row["before_mean"], row["after_mean"])
        row["pct_change_std"] = pct_change(row["before_std"], row["after_std"])
        rows.append(row)
    table = pd.DataFrame(rows)
    nb = int(table["before_n_obs"].max() or 0) if table["before_n_obs"].notna().any() else 0
    na = int(table["after_n_obs"].max() or 0) if table["after_n_obs"].notna().any() else 0
    result.update(table=table, n_before=nb, n_after=na)
    result["sufficient"] = nb >= min_obs and na >= min_obs

    if nb == 0:
        result["messages"].append("No readings before the event (maintenance may predate the telemetry record).")
    elif nb < expected:
        result["messages"].append(f"Before-window has {nb} of {expected} possible hourly readings.")
    if na == 0:
        result["messages"].append("No readings after the event (event may be at the end of the record).")
    elif na < expected:
        result["messages"].append(f"After-window has {na} of {expected} possible hourly readings.")
    if not result["sufficient"]:
        result["messages"].append(
            f"Insufficient observations: need at least {min_obs} readings in each window to interpret changes.")
    zero = table[table["before_mean"].apply(lambda v: v is not None and not pd.isna(v) and abs(v) < EPS)]
    if len(zero):
        result["messages"].append("Percentage change suppressed for metrics with a zero baseline: "
                                  + ", ".join(zero["metric"]))
    return result


def readings(conn: sqlite3.Connection, machine_id: int, ts: str, window_days: int) -> pd.DataFrame:
    return db.read_df(conn, db.load_named_queries(Q)["window_readings"],
                      {"machine_id": int(machine_id), "ts": ts, "hours": int(window_days) * 24})


def component_summary(conn: sqlite3.Connection, window_days: int = 7, min_obs: int = 24) -> pd.DataFrame:
    return db.read_df(conn, db.load_named_queries(Q)["component_effect_summary"],
                      {"hours": int(window_days) * 24, "min_obs": int(min_obs)})


LIMITATIONS = (
    "These are <b>observed changes</b> in hourly telemetry around a recorded maintenance time. They do not prove "
    "that the maintenance caused them: the readings are noisy, other events may fall inside the windows "
    "(see the confounder table), the machine may have been failing/repaired, and regression to the mean or "
    "operating-condition changes can produce similar differences."
)
