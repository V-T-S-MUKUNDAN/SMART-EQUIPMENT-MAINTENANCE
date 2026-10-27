"""AI4I operating-condition risk indicator (configurable weights, explanations, evaluation)."""
from __future__ import annotations

import copy
import sqlite3
from dataclasses import dataclass

import pandas as pd

from .. import config, db
from .analytics import Filters, MIN_GROUP_RECORDS

COMPONENTS = ["tool_wear", "strain", "power", "heat"]
ACTIONS = {
    "tool_wear": "Check tool condition and consider tool replacement.",
    "strain": "Review load: tool wear combined with high torque is near the overstrain limit for this type.",
    "power": "Check torque/speed settings: power is far from the middle of the safe operating band.",
    "heat": "Check cooling: small process-air temperature difference at low speed limits heat dissipation.",
}
DISCLAIMER = ("This is an operating-condition risk indicator computed from sensor values. It is not a validated "
              "predictive-maintenance model and does not predict failures of a physical machine; AI4I rows are "
              "independent synthetic records, not a maintenance history.")


@dataclass
class RiskConfig:
    weights: dict
    thresholds: dict
    parameters: dict
    labels: dict

    @classmethod
    def default(cls) -> "RiskConfig":
        raw = config.load_risk_config("ai4i")
        return cls(dict(raw["weights"]), dict(raw["thresholds"]), copy.deepcopy(raw["parameters"]), dict(raw["labels"]))

    def copy(self) -> "RiskConfig":
        return copy.deepcopy(self)

    def validate(self) -> None:
        for c in COMPONENTS:
            if self.weights.get(c) is None or self.weights[c] < 0:
                raise ValueError(f"weight '{c}' must be a number >= 0")
        if sum(self.weights.values()) <= 0:
            raise ValueError("at least one weight must be greater than 0")
        t = self.thresholds
        if not (0 <= t["medium"] < t["high"] < t["critical"] <= 100):
            raise ValueError("thresholds must satisfy 0 <= medium < high < critical <= 100")

    def params(self) -> dict:
        p, osf = self.parameters, self.parameters["osf_threshold_minnm"]
        out = {f"w_{c}": float(self.weights[c]) for c in COMPONENTS}
        out.update(t_medium=float(self.thresholds["medium"]), t_high=float(self.thresholds["high"]),
                   t_critical=float(self.thresholds["critical"]),
                   wear_ref=float(p["tool_wear_reference_min"]),
                   osf_l=float(osf["L"]), osf_m=float(osf["M"]), osf_h=float(osf["H"]),
                   p_low=float(p["power_safe_low_w"]), p_high=float(p["power_safe_high_w"]),
                   hdf_diff=float(p["hdf_temp_diff_limit_k"]), hdf_rpm=float(p["hdf_speed_limit_rpm"]))
        return out

    def key(self) -> str:
        import json
        return json.dumps([self.weights, self.thresholds, self.parameters], sort_keys=True)


def risk_sql(f: Filters) -> tuple[str, dict]:
    clause, params = f.where()
    return db.load_named_queries("ai4i/risk.sql")["risk_scores"].format(where=clause), params


def compute_risk(conn: sqlite3.Connection, f: Filters | None = None, cfg: RiskConfig | None = None) -> pd.DataFrame:
    cfg = cfg or RiskConfig.default()
    cfg.validate()
    sql, params = risk_sql(f or Filters())
    return db.read_df(conn, sql, {**params, **cfg.params()})


def _eval(conn: sqlite3.Connection, name: str, f: Filters | None, cfg: RiskConfig | None, **extra) -> pd.DataFrame:
    cfg = cfg or RiskConfig.default()
    cfg.validate()
    inner, params = risk_sql(f or Filters())
    sql = db.load_named_queries("ai4i/risk.sql")[name].replace("{risk}", inner)
    return db.read_df(conn, sql, {**params, **cfg.params(), "min_n": MIN_GROUP_RECORDS, **extra})


def category_validation(conn, f=None, cfg=None):
    return _eval(conn, "category_validation", f, cfg)


def decile_validation(conn, f=None, cfg=None):
    return _eval(conn, "decile_validation", f, cfg)


def threshold_sweep(conn, f=None, cfg=None):
    return _eval(conn, "threshold_sweep", f, cfg)


def rank_separation(conn, f=None, cfg=None):
    return _eval(conn, "rank_separation", f, cfg)


def group_ranking(conn, f=None, cfg=None):
    return _eval(conn, "group_ranking", f, cfg)


def category_for(score: float, thresholds: dict) -> str:
    score = round(score, 2)
    if score >= thresholds["critical"]:
        return "Critical"
    if score >= thresholds["high"]:
        return "High"
    if score >= thresholds["medium"]:
        return "Medium"
    return "Low"


def explain(row: pd.Series, cfg: RiskConfig, top: int = 2) -> str:
    """Why a record scored as it did: factor values and point contributions."""
    detail = {
        "tool_wear": f"tool wear {row['tool_wear_min']:g} min",
        "strain": f"wear x torque = {row['tool_wear_min'] * row['torque_nm']:,.0f} minNm (type {row['machine_type']})",
        "power": f"power {row['power_w']:,.0f} W",
        "heat": f"temp. difference {row['temp_diff_k']:.1f} K at {row['rotational_speed_rpm']:g} rpm",
    }
    contrib = sorted(((float(row[f"c_{c}"]), c) for c in COMPONENTS), reverse=True)
    parts = [f"{cfg.labels[c]}: {pts:.1f} pts ({detail[c]})" for pts, c in contrib[:top] if pts > 0]
    return "; ".join(parts) if parts else "no factor contributes to the score"


def suggested_action(row: pd.Series) -> str:
    best = max(COMPONENTS, key=lambda c: float(row[f"c_{c}"]))
    return ACTIONS[best] if float(row[f"c_{best}"]) > 0 else "No specific inspection focus."


def summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"records": 0, "avg_score": None, "by_category": {}}
    cats = df["risk_category"].value_counts().to_dict()
    return {"records": len(df), "avg_score": round(float(df["risk_score"].mean()), 2),
            "median": round(float(df["risk_score"].median()), 2), "max": float(df["risk_score"].max()),
            "by_category": {c: int(cats.get(c, 0)) for c in config.RISK_CATEGORIES}}
