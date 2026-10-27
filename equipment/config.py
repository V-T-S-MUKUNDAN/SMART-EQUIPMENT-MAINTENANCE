"""Central configuration. Every path can be overridden with an environment variable.

SE_DB_PATH     - SQLite database file            (default: <project>/database/equipment.db)
SE_AZURE_DIR   - folder with the five PdM_*.csv  (default: <project>/data/azure)
SE_AI4I_DIR    - folder searched for the AI4I csv (default: <project>/data/ai4i, then <project>/data)
SE_CONFIG_DIR  - folder with the risk-model JSON  (default: <project>/config)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql"


def _path(env: str, default: Path) -> Path:
    return Path(os.environ.get(env, str(default))).expanduser()


def db_path() -> Path:
    return _path("SE_DB_PATH", ROOT / "database" / "equipment.db")


def azure_dir() -> Path:
    return _path("SE_AZURE_DIR", ROOT / "data" / "azure")


def ai4i_search_dirs() -> list[Path]:
    """Folders searched (non-recursively) for the AI4I csv."""
    if "SE_AI4I_DIR" in os.environ:
        return [_path("SE_AI4I_DIR", ROOT / "data" / "ai4i")]
    return [ROOT / "data" / "ai4i", ROOT / "data"]


def config_dir() -> Path:
    return _path("SE_CONFIG_DIR", ROOT / "config")


def load_risk_config(dataset: str) -> dict:
    """Load config/<dataset>_risk.json (weights, thresholds, parameters)."""
    path = config_dir() / f"{dataset}_risk.json"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# Category labels are shared by both risk models.
RISK_CATEGORIES = ["Low", "Medium", "High", "Critical"]
# Physical signal colours (enamel green / tag amber / iron oxide) — used by charts, not as decoration.
RISK_COLORS = {"Low": "#3F5E54", "Medium": "#A07A3A", "High": "#B0573A", "Critical": "#8A3A36"}

DATASET_AZURE = "azure"
DATASET_AI4I = "ai4i"
DATASET_LABELS = {
    DATASET_AZURE: "Microsoft Azure Predictive Maintenance",
    DATASET_AI4I: "AI4I 2020 Predictive Maintenance",
}
