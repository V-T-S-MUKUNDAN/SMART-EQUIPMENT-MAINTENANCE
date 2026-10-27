"""Plotly chart builders reused across pages."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from . import config


def category_bar(counts: dict, title: str = "Risk-category distribution", unit: str = "machines") -> go.Figure:
    df = pd.DataFrame({"Category": config.RISK_CATEGORIES, unit: [counts.get(c, 0) for c in config.RISK_CATEGORIES]})
    fig = px.bar(df, x="Category", y=unit, color="Category", color_discrete_map=config.RISK_COLORS, text=unit,
                 title=title)
    fig.update_layout(showlegend=False)
    return fig


def contribution_bar(contrib: pd.DataFrame, labels: dict, y: str = "machine_id", title: str = "Score composition (points)"):
    """Stacked horizontal bars. ``contrib`` has columns [y, component, points]."""
    d = contrib.copy()
    d["Component"] = d["component"].map(labels)
    d[y] = d[y].astype(str)
    fig = px.bar(d, x="points", y=y, color="Component", orientation="h", title=title,
                 labels={"points": "Score points", y: y.replace("_", " ").title()})
    fig.update_yaxes(autorange="reversed", type="category")
    return fig


def histogram(df: pd.DataFrame, col: str, title: str, thresholds: dict | None = None, nbins: int = 25) -> go.Figure:
    fig = px.histogram(df, x=col, nbins=nbins, title=title, color_discrete_sequence=["#1F4E79"])
    for name, val in (thresholds or {}).items():
        fig.add_vline(x=val, line_dash="dash", line_color="#8C3A32", annotation_text=name.title())
    fig.update_layout(bargap=0.05, yaxis_title="Count")
    return fig
