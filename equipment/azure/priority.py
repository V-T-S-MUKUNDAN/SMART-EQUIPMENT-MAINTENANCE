"""Module 5 - rule-based maintenance prioritisation built on the verified risk score (Azure)."""
from __future__ import annotations

import pandas as pd

from .risk import COMPONENTS, RiskConfig, explain

# Rule table: dominant score component -> suggested *inspection* focus.
# These are recommendations for a human to review; they are not diagnoses and do not predict failures.
ACTION_RULES = {
    "fail_freq": "Review the failure history by component and inspect the most frequently failing component.",
    "fail_rec": "A failure was recorded recently: verify the repair and re-inspect the affected component.",
    "err_freq": "Review the error log; check the sensors/controls behind the most frequent error types.",
    "err_rec": "Errors were logged very recently: inspect for an emerging fault.",
    "tele": "Telemetry deviates from the fleet baseline: check voltage, rotation, pressure and vibration sensors and mounting.",
    "maint_burden": "Heavy planned-maintenance load: review whether the maintenance interval and scope are appropriate.",
    "maint_rec": "Maintenance is overdue relative to the fleet: schedule a routine inspection.",
    "age": "Older equipment: include in the ageing-asset inspection round.",
}
URGENCY = {
    "Critical": "Inspect as soon as possible",
    "High": "Schedule inspection in the next maintenance cycle",
    "Medium": "Monitor; include in routine inspection",
    "Low": "No action beyond routine maintenance",
}
DISCLAIMER = ("Suggested actions are rule-based recommendations for inspection derived from the risk score "
              "components. They are not automatic diagnoses and do not guarantee failure prevention.")


def dominant_component(row: pd.Series) -> str | None:
    best = max(COMPONENTS, key=lambda c: float(row[f"c_{c}"]))
    return best if float(row[f"c_{best}"]) > 0 else None


def build_priority_table(risk_df: pd.DataFrame, cfg: RiskConfig) -> pd.DataFrame:
    """One row per machine, ranked by risk score, with reasons, action and supporting timestamps."""
    if risk_df.empty:
        return pd.DataFrame()
    rows = []
    for _, r in risk_df.iterrows():
        dom = dominant_component(r)
        action = f"{URGENCY[r['risk_category']]}. " + (ACTION_RULES[dom] if dom else "")
        rows.append({
            "Priority": int(r["priority_rank"]),
            "Machine ID": int(r["machine_id"]),
            "Model": r["model"], "Age": int(r["age"]),
            "Risk score": float(r["risk_score"]), "Category": r["risk_category"],
            "Failures": int(r["failure_count"]), "Errors": int(r["error_count"]),
            "Last maintenance": r["last_maintenance"], "Last failure": r["last_failure"],
            "Last error": r["last_error"],
            "Avg volt (recent)": _round(r["avg_volt"]), "Avg rotate (recent)": _round(r["avg_rotate"]),
            "Avg pressure (recent)": _round(r["avg_pressure"]), "Avg vibration (recent)": _round(r["avg_vibration"]),
            "Telemetry outside band": None if pd.isna(r["tele_exceed_rate"]) else round(100 * r["tele_exceed_rate"], 2),
            "Main reasons": explain(r, cfg, top=3),
            "Suggested inspection (recommendation)": action,
        })
    return pd.DataFrame(rows)


def _round(v):
    return None if pd.isna(v) else round(float(v), 2)


def filter_table(df: pd.DataFrame, categories=None, models=None, search: str = "", min_score: float = 0.0) -> pd.DataFrame:
    out = df
    if categories is not None:
        out = out[out["Category"].isin(categories)]
    if models is not None:
        out = out[out["Model"].isin(models)]
    if min_score:
        out = out[out["Risk score"] >= min_score]
    if search:
        s = search.strip().lower()
        out = out[out.apply(lambda r: s in " ".join(str(v).lower() for v in r.values), axis=1)]
    return out


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")
