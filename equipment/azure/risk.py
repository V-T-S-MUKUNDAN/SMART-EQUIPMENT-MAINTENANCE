"""Azure machine risk scoring: configurable weights/thresholds, SQL execution, explanations."""
from __future__ import annotations

import copy
import sqlite3
from dataclasses import dataclass

import pandas as pd

from .. import config, db
from .analytics import Filters

COMPONENTS = ["fail_freq", "fail_rec", "err_freq", "err_rec", "tele", "maint_burden", "maint_rec", "age"]


@dataclass
class RiskConfig:
    weights: dict
    thresholds: dict
    parameters: dict
    labels: dict

    @classmethod
    def default(cls) -> "RiskConfig":
        raw = config.load_risk_config("azure")
        return cls(dict(raw["weights"]), dict(raw["thresholds"]), dict(raw["parameters"]), dict(raw["labels"]))

    def copy(self) -> "RiskConfig":
        return copy.deepcopy(self)

    def validate(self) -> None:
        for c in COMPONENTS:
            w = self.weights.get(c)
            if w is None or w < 0:
                raise ValueError(f"weight '{c}' must be a number >= 0")
        if sum(self.weights.values()) <= 0:
            raise ValueError("at least one weight must be greater than 0")
        t = self.thresholds
        if not (0 <= t["medium"] < t["high"] < t["critical"] <= 100):
            raise ValueError("thresholds must satisfy 0 <= medium < high < critical <= 100")
        p = self.parameters
        if p["recency_lookback_days"] <= 0 or p["telemetry_window_days"] <= 0 or p["telemetry_sigma_k"] <= 0:
            raise ValueError("lookback, telemetry window and sigma must be positive")

    def normalised_weights(self) -> dict:
        total = sum(self.weights.values())
        return {k: 100.0 * v / total for k, v in self.weights.items()}

    def params(self) -> dict:
        p = {f"w_{c}": float(self.weights[c]) for c in COMPONENTS}
        p.update(t_medium=float(self.thresholds["medium"]), t_high=float(self.thresholds["high"]),
                 t_critical=float(self.thresholds["critical"]),
                 lookback=float(self.parameters["recency_lookback_days"]),
                 tele_days=int(self.parameters["telemetry_window_days"]),
                 k=float(self.parameters["telemetry_sigma_k"]))
        return p

    def key(self) -> tuple:
        return (tuple(sorted(self.weights.items())), tuple(sorted(self.thresholds.items())),
                tuple(sorted(self.parameters.items())))


def category_for(score: float, thresholds: dict) -> str:
    """Python mirror of the SQL CASE expression (used by tests and documentation)."""
    score = round(score, 2)
    if score >= thresholds["critical"]:
        return "Critical"
    if score >= thresholds["high"]:
        return "High"
    if score >= thresholds["medium"]:
        return "Medium"
    return "Low"


def compute_risk(conn: sqlite3.Connection, f: Filters, cfg: RiskConfig | None = None) -> pd.DataFrame:
    """Risk score per machine. Scaling uses all machines; ``f.machines`` only narrows the output."""
    cfg = cfg or RiskConfig.default()
    cfg.validate()
    sql = db.load_named_queries("azure/risk.sql")["risk_scores"]
    mclause, mparams = db.in_clause("machine_id", f.machines, "mf")
    sql = sql.format(machine_filter=mclause)
    params = {"start": f.start, "end_excl": f.end_excl, **mparams, **cfg.params()}
    return db.read_df(conn, sql, params)


def contribution_frame(df: pd.DataFrame, cfg: RiskConfig) -> pd.DataFrame:
    """Long format: machine_id, component, label, contribution (points)."""
    rows = []
    for _, r in df.iterrows():
        for c in COMPONENTS:
            rows.append({"machine_id": r["machine_id"], "component": c, "label": cfg.labels[c],
                         "points": float(r[f"c_{c}"])})
    return pd.DataFrame(rows)


def explain(row: pd.Series, cfg: RiskConfig, top: int = 3) -> str:
    """Plain-language reasons for one machine (the largest score contributions)."""
    contrib = sorted(((float(row[f"c_{c}"]), c) for c in COMPONENTS), reverse=True)
    parts = []
    for pts, c in contrib[:top]:
        if pts <= 0:
            continue
        parts.append(f"{cfg.labels[c]} ({pts:.1f} pts{_detail(row, c)})")
    text = "; ".join(parts) if parts else "no component contributes to the score"
    if row.get("data_flags"):
        text += f" | data notes: {row['data_flags']}"
    return text


def _detail(row: pd.Series, c: str) -> str:
    def d(v):
        return "n/a" if pd.isna(v) else f"{v:g}"
    if c == "fail_freq":
        return f", {int(row['failure_count'])} failures"
    if c == "fail_rec":
        return f", last failure {d(row['days_since_failure'])} days before as-of"
    if c == "err_freq":
        return f", {int(row['error_count'])} errors"
    if c == "err_rec":
        return f", last error {d(row['days_since_error'])} days before as-of"
    if c == "tele":
        return f", {row['tele_exceed_rate']:.1%} of recent readings outside fleet band" \
            if pd.notna(row["tele_exceed_rate"]) else ""
    if c == "maint_burden":
        return f", {int(row['planned_maintenance_count'])} planned maintenance events"
    if c == "maint_rec":
        return f", {row['days_since_maint']:.0f} days since last maintenance"
    if c == "age":
        return f", {int(row['age'])} years"
    return ""


def evidence(conn: sqlite3.Connection, machine_id: int, f: Filters, cfg: RiskConfig | None = None) -> dict:
    """Underlying records for one machine's score, so every number can be traced to rows."""
    cfg = cfg or RiskConfig.default()
    q = db.load_named_queries("azure/risk.sql")
    base = {"machine_id": int(machine_id), "start": f.start, "end_excl": f.end_excl,
            "tele_days": int(cfg.parameters["telemetry_window_days"]),
            "k": float(cfg.parameters["telemetry_sigma_k"])}
    return {name: db.read_df(conn, q[sql], base)
            for name, sql in [("failures", "ev_failures"), ("errors", "ev_errors"),
                              ("maintenance", "ev_maintenance"), ("telemetry", "ev_telemetry")]}


def summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"machines": 0, "avg_score": None, "median": None, "max": None, "min": None, "by_category": {}}
    cats = df["risk_category"].value_counts().to_dict()
    return {"machines": len(df), "avg_score": round(float(df["risk_score"].mean()), 2),
            "median": round(float(df["risk_score"].median()), 2),
            "max": float(df["risk_score"].max()), "min": float(df["risk_score"].min()),
            "by_category": {c: int(cats.get(c, 0)) for c in config.RISK_CATEGORIES}}
