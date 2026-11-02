"""Streamlit helpers shared by all pages: styling, cached SQL access, KPI cards, exports, filters.

Caching rule: every cached function receives ``db.db_version()`` (mtime + size of the SQLite file) as an
argument, so the moment the database is rewritten the cache key changes and stale results are never shown.
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from . import config, db
from .azure import analytics as az
from .azure import risk as az_risk
from .ai4i import analytics as ai
from .ai4i import risk as ai_risk

# Quiet signal colours — used in charts, not as decoration.
PLOT_COLORS = ["#1F4E79", "#7A4E28", "#3F5E54", "#8C3A32", "#6A7380", "#A67C00"]
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Instrument+Sans:ital,wght@0,400;0,500;0,600;0,700;1,400&family=Instrument+Serif:ital@0;1&display=swap');

:root {
  --paper: #F7F7F5;
  --ink: #161616;
  --mute: #6B6B66;
  --rule: #E4E4DF;
  --plate: #1F4E79;
  --brass: #7A4E28;
  --tag: #A67C00;
  --face: "Instrument Sans", "Segoe UI", sans-serif;
  --display: "Instrument Serif", "Georgia", serif;
}

html, body, [data-testid="stAppViewContainer"], .stApp {
  background: var(--paper);
  color: var(--ink);
  font-family: var(--face);
  font-feature-settings: "kern" 1, "liga" 1;
}
.stApp, .stApp p, .stApp li, .stApp label, .stApp span,
[data-testid="stMarkdownContainer"], [data-testid="stSidebar"] * {
  font-family: var(--face);
}
.block-container {
  padding-top: 2rem;
  padding-bottom: 4rem;
  max-width: 1240px;
}
header[data-testid="stHeader"] { background: transparent; }
[data-testid="stToolbar"],
.stAppDeployButton,
[data-testid="stDecoration"] { display: none !important; }
[data-testid="stBaseButton-headerNoPadding"],
[data-testid="stSidebarCollapseButton"] button {
  font-size: 0 !important;
  line-height: 0 !important;
  width: 28px;
  height: 28px;
  color: transparent !important;
}
[data-testid="stBaseButton-headerNoPadding"] *,
[data-testid="stSidebarCollapseButton"] [data-testid="stIconMaterial"] {
  font-size: 0 !important;
  line-height: 0 !important;
  color: transparent !important;
  overflow: hidden !important;
}
[data-testid="stBaseButton-headerNoPadding"]::before,
[data-testid="stSidebarCollapseButton"] button::before {
  content: "‹";
  font-size: 1.2rem !important;
  line-height: 1 !important;
  color: var(--mute) !important;
  font-family: var(--face) !important;
}

h1, h2, h3, h4,
[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3 {
  color: var(--ink);
  font-weight: 500;
}
h1, [data-testid="stMarkdownContainer"] h1 {
  font-family: var(--display);
  font-size: 2.35rem;
  font-weight: 400;
  letter-spacing: -0.02em;
  line-height: 1.12;
  margin: 0.2rem 0 1.35rem 0;
}
h2, [data-testid="stMarkdownContainer"] h2 {
  font-family: var(--face);
  font-size: 1.05rem;
  font-weight: 600;
  letter-spacing: -0.02em;
}
h3, [data-testid="stMarkdownContainer"] h3 {
  font-family: var(--face);
  font-size: 0.98rem;
  font-weight: 600;
  letter-spacing: -0.015em;
  color: var(--ink);
}

[data-testid="stSidebar"] {
  background: #F0F0EC;
  border-right: 1px solid var(--rule);
}
section[data-testid="stSidebar"] [data-testid="stCaption"] {
  color: var(--mute);
  font-size: 0.72rem;
  line-height: 1.45;
}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
  font-size: 0.72rem;
  font-weight: 500;
  color: var(--mute);
  letter-spacing: 0.01em;
}

.brand-plate {
  padding: 4px 0 18px 0;
  border-bottom: 1px solid var(--rule);
  margin-bottom: 18px;
}
.brand-name {
  font-family: var(--display);
  font-weight: 400;
  font-size: 1.45rem;
  letter-spacing: -0.02em;
  line-height: 1.15;
  color: var(--ink);
}
.brand-sub {
  color: var(--mute);
  font-size: 0.78rem;
  margin-top: 4px;
  font-weight: 400;
  letter-spacing: 0.01em;
}

.ds-banner {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 0 0 12px 0;
  margin: 0 0 6px 0;
  border-bottom: 1px solid var(--rule);
  background: transparent;
  font-weight: 400;
  font-size: 0.84rem;
  color: var(--mute);
  letter-spacing: 0;
}
.ds-banner::before {
  content: "";
  width: 6px;
  height: 6px;
  flex-shrink: 0;
  border-radius: 50%;
  background: var(--plate);
  align-self: center;
}
.ds-azure { color: var(--mute); }
.ds-azure::before { background: var(--plate); }
.ds-ai4i { color: var(--mute); }
.ds-ai4i::before { background: var(--brass); }
.ds-both { color: var(--mute); }
.ds-both::before { background: var(--mute); }

.note {
  background: transparent;
  color: var(--mute);
  border: none;
  border-left: 1px solid var(--tag);
  padding: 1px 0 1px 14px;
  font-size: 0.9rem;
  line-height: 1.55;
  max-width: 70ch;
  margin-bottom: 0.9rem;
}

/* Numbers as type, not cards */
div[data-testid="stMetric"] {
  background: transparent;
  border: none;
  border-radius: 0;
  padding: 2px 12px 22px 0;
  box-shadow: none;
}
div[data-testid="stMetricLabel"] p {
  font-size: 0.75rem;
  font-weight: 400;
  color: var(--mute);
  white-space: normal !important;
  overflow: visible !important;
  text-overflow: clip !important;
  line-height: 1.35;
  letter-spacing: 0.01em;
}
div[data-testid="stMetricValue"] {
  font-family: var(--face);
  font-weight: 600;
  font-size: 2.15rem;
  letter-spacing: -0.04em;
  font-variant-numeric: tabular-nums;
  font-feature-settings: "tnum" 1, "kern" 1;
  color: var(--ink);
  line-height: 1.08;
  overflow: visible !important;
}
div[data-testid="stMetricValue"] * {
  color: var(--ink);
  font-size: inherit;
  overflow: visible !important;
  text-overflow: clip !important;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
[data-testid="stMetric"] [data-testid="stTooltipIcon"] { display: none !important; }
[data-testid="stMetric"] .stMarkdownEllipsis {
  overflow: visible !important;
  text-overflow: clip !important;
  display: block !important;
  -webkit-line-clamp: unset !important;
}
[data-testid="stMetricLabel"] .stMarkdownEllipsis { white-space: normal !important; }
[data-testid="stMetricValue"] .stMarkdownEllipsis { white-space: nowrap !important; }

[data-testid="stCaption"] { color: var(--mute) !important; font-size: 0.8rem; line-height: 1.5; }

[data-testid="stDataFrame"] { border: 1px solid var(--rule); border-radius: 4px; overflow: hidden; }
[data-testid="stExpander"] {
  background: transparent;
  border: none;
  border-top: 1px solid var(--rule);
  border-radius: 0;
}

div[data-testid="stDownloadButton"] button,
.stButton button {
  border-radius: 4px;
  border: 1px solid var(--ink);
  background: transparent;
  color: var(--ink);
  font-family: var(--face);
  font-weight: 500;
  font-size: 0.84rem;
  letter-spacing: 0.01em;
}
div[data-testid="stDownloadButton"] button:hover,
.stButton button:hover {
  background: var(--ink);
  color: var(--paper);
  border-color: var(--ink);
}

.stAlert { border-radius: 0; border: none; background: transparent; }
[data-testid="stSidebar"] input,
[data-testid="stSidebar"] [data-baseweb="input"] > div,
[data-testid="stSidebar"] [data-baseweb="select"] > div,
[data-testid="stDateInput"] > div > div,
[data-baseweb="input"] > div,
[data-baseweb="select"] > div {
  border-radius: 4px !important;
  background: var(--paper) !important;
}

[data-testid="stSidebar"] a,
[data-testid="stPageLink-NavLink"] {
  border-radius: 0 !important;
  font-family: var(--face) !important;
  font-weight: 400;
  background: transparent !important;
  box-shadow: inset 2px 0 0 transparent;
}
[data-testid="stPageLink-NavLink"]:hover {
  background: transparent !important;
  box-shadow: inset 2px 0 0 var(--rule);
}
[data-testid="stPageLink-NavLink"][href=""] {
  background: transparent !important;
  box-shadow: inset 2px 0 0 var(--ink);
}
[data-testid="stPageLink-NavLink"] p {
  font-size: 0.88rem !important;
  font-weight: 400 !important;
  white-space: normal !important;
  overflow: visible !important;
  text-overflow: unset !important;
  line-height: 1.35;
}
[data-testid="stPageLink-NavLink"][href=""] p {
  font-weight: 500 !important;
  color: var(--ink);
}
[data-testid="stSidebar"] [data-baseweb="radio"] {
  gap: 2px;
}
[data-testid="stSidebar"] [data-baseweb="radio"] label {
  line-height: 1.3;
}

*:focus-visible { outline: 2px solid var(--plate); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def brand() -> None:
    """Sidebar identity: name and discipline, nothing else."""
    st.sidebar.markdown(
        '<div class="brand-plate"><div class="brand-name">Smart Equipment</div>'
        '<div class="brand-sub">Failure analytics</div></div>',
        unsafe_allow_html=True,
    )


def banner(dataset: str, page_title: str) -> None:
    """Identify the active dataset at the top of every page."""
    label = {"azure": "Microsoft Azure Predictive Maintenance", "ai4i": "AI4I 2020 Predictive Maintenance",
             "both": "Comparative Analysis (Azure + AI4I 2020)"}[dataset]
    cls = {"azure": "ds-azure", "ai4i": "ds-ai4i", "both": "ds-both"}[dataset]
    st.markdown(f'<div class="ds-banner {cls}">Active dataset: {label}</div>', unsafe_allow_html=True)
    st.title(page_title)


def note(text: str) -> None:
    st.markdown(f'<div class="note">{text}</div>', unsafe_allow_html=True)


def empty_state(message: str = "No records match the current filters.") -> None:
    st.info(message)


# ----------------------------------------------------------------------------- connections / readiness
def db_ready(tables: list[str]) -> tuple[bool, str]:
    """(ready, message): database file exists and every table has rows."""
    try:
        conn = db.connect(read_only=True)
    except db.DatabaseNotFoundError as exc:
        return False, str(exc)
    except Exception as exc:  # locked / corrupt
        return False, f"Cannot open the database: {exc}"
    try:
        empty = [t for t in tables if db.table_count(conn, t) == 0]
    finally:
        conn.close()
    if empty:
        return False, f"Table(s) empty or missing: {', '.join(empty)}. Run: python scripts/load_data.py"
    return True, "ready"


def require_ready(tables: list[str]) -> None:
    ok, msg = db_ready(tables)
    if not ok:
        st.error(msg)
        st.caption("Open **Module 01 - Data Acquisition and Database Management** to build or repair the database.")
        st.stop()


class DatabaseBusyError(RuntimeError):
    """The SQLite file is locked by another writer."""


def _run(fn, *args, **kwargs):
    """Open a short-lived read-only connection; translate low-level errors into friendly ones."""
    conn = db.connect(read_only=True)      # raises DatabaseNotFoundError with instructions
    try:
        return fn(conn, *args, **kwargs)
    except Exception as exc:
        if "locked" in str(exc).lower():
            raise DatabaseBusyError(
                "The database is locked by another process (for example a running data load). Retry in a moment.") from exc
        raise
    finally:
        conn.close()


# ----------------------------------------------------------------------------- cached SQL access
@st.cache_data(show_spinner=False)
def _az_query(version, name, filters, extra):
    return _run(lambda c: az.query(c, name, filters, **dict(extra)))


@st.cache_data(show_spinner=False)
def _az_risk(version, filters, cfg):
    return _run(lambda c: az_risk.compute_risk(c, filters, cfg))


@st.cache_data(show_spinner=False)
def _ai_query(version, name, filters, extra):
    return _run(lambda c: ai.query(c, name, filters, **dict(extra)))


@st.cache_data(show_spinner=False)
def _ai_risk(version, filters, cfg):
    return _run(lambda c: ai_risk.compute_risk(c, filters, cfg))


@st.cache_data(show_spinner=False)
def _ai_eval(version, fn_name, filters, cfg):
    return _run(lambda c: getattr(ai_risk, fn_name)(c, filters, cfg))


def az_q(name: str, f: az.Filters | None = None, **extra) -> pd.DataFrame:
    return _az_query(db.db_version(), name, f, tuple(sorted(extra.items())))


def az_risk_scores(f: az.Filters, cfg: az_risk.RiskConfig) -> pd.DataFrame:
    return _az_risk(db.db_version(), f, cfg)


def ai_q(name: str, f: ai.Filters | None = None, **extra) -> pd.DataFrame:
    return _ai_query(db.db_version(), name, f, tuple(sorted(extra.items())))


def ai_risk_scores(f: ai.Filters, cfg: ai_risk.RiskConfig) -> pd.DataFrame:
    return _ai_risk(db.db_version(), f, cfg)


def ai_eval(fn_name: str, f: ai.Filters, cfg: ai_risk.RiskConfig) -> pd.DataFrame:
    return _ai_eval(db.db_version(), fn_name, f, cfg)


@st.cache_data(show_spinner=False)
def _direct(version, fn_path, args):
    import importlib
    mod, fn = fn_path.rsplit(".", 1)
    func = getattr(importlib.import_module(mod), fn)
    return _run(lambda c: func(c, *args))


def cached_call(fn_path: str, *args):
    """Cache any ``module.function(conn, *args)`` keyed by database version (args must be hashable)."""
    return _direct(db.db_version(), fn_path, args)


# ----------------------------------------------------------------------------- session-backed state
def dataset() -> str:
    return st.session_state.get("dataset", config.DATASET_AZURE)


def guard(expected: str) -> None:
    """Refuse to render a page of one dataset while another is selected (dataset isolation)."""
    if dataset() != expected:
        st.warning("This page belongs to a different dataset than the one selected in the sidebar.")
        st.stop()


AZURE_TABLES = ["azure_machines", "azure_telemetry", "azure_errors", "azure_maintenance", "azure_failures"]


def azure_filters() -> az.Filters:
    return st.session_state["az_filters"]


def azure_cfg() -> az_risk.RiskConfig:
    if "az_cfg" not in st.session_state:
        st.session_state["az_cfg"] = az_risk.RiskConfig.default()
    return st.session_state["az_cfg"]


def ai4i_filters() -> ai.Filters:
    return st.session_state["ai_filters"]


def ai4i_cfg() -> ai_risk.RiskConfig:
    if "ai_cfg" not in st.session_state:
        st.session_state["ai_cfg"] = ai_risk.RiskConfig.default()
    return st.session_state["ai_cfg"]


# ----------------------------------------------------------------------------- sidebar filters
def azure_sidebar() -> None:
    ok, _ = db_ready(["azure_machines", "azure_failures"])
    if not ok:
        st.sidebar.warning("Azure data not loaded yet - see Module 01.")
        st.session_state["az_filters"] = None
        return
    lo, hi = _run(az.data_bounds)
    first = _run(az.default_start)
    machines = az_q("machine_list")
    comps = az_q("component_list")["component"].tolist()
    st.sidebar.markdown("**Filters**")
    rng = st.sidebar.date_input("Date range", value=(date.fromisoformat(first), date.fromisoformat(hi)),
                                min_value=date.fromisoformat(lo), max_value=date.fromisoformat(hi), key="az_dates")
    if isinstance(rng, (tuple, list)) and len(rng) == 2:
        start, end = rng
    else:
        start = end = rng[0] if isinstance(rng, (tuple, list)) and rng else rng
    sel_m = st.sidebar.multiselect("Machines", machines["machine_id"].tolist(), key="az_machines",
                                   placeholder="All machines")
    sel_c = st.sidebar.multiselect("Components", comps, key="az_components", placeholder="All components",
                                   help="Applies to failures and maintenance events (errors use error types).")
    st.session_state["az_filters"] = az.Filters(start, end, tuple(sel_m) if sel_m else None,
                                                tuple(sel_c) if sel_c else None)


def ai4i_sidebar() -> None:
    ok, _ = db_ready(["ai4i_machine_data"])
    if not ok:
        st.sidebar.warning("AI4I data not loaded yet - see Module 01.")
        st.session_state["ai_filters"] = None
        return
    b = _run(ai.bounds)
    types = ai_q("types")["machine_type"].tolist()
    st.sidebar.markdown("**Filters**")
    sel_t = st.sidebar.multiselect("Machine type", types, key="ai_types", placeholder="All types",
                                   help="L = low, M = medium, H = high quality variant")

    def rng(label, lo, hi, key, step):
        lo, hi = float(lo), float(hi)
        v = st.sidebar.slider(label, lo, hi, (lo, hi), step=step, key=key)
        return None if (v[0] <= lo and v[1] >= hi) else v

    r = {
        "tool_wear": rng("Tool wear [min]", b["wear_lo"], b["wear_hi"], "ai_wear", 1.0),
        "torque": rng("Torque [Nm]", b["torque_lo"], b["torque_hi"], "ai_torque", 0.1),
        "rpm": rng("Rotational speed [rpm]", b["rpm_lo"], b["rpm_hi"], "ai_rpm", 1.0),
        "air_temp": rng("Air temperature [K]", b["air_lo"], b["air_hi"], "ai_air", 0.1),
        "process_temp": rng("Process temperature [K]", b["proc_lo"], b["proc_hi"], "ai_proc", 0.1),
    }
    st.session_state["ai_filters"] = ai.Filters.make(tuple(sel_t) if sel_t else None, **r)


def sidebar_footer() -> None:
    st.sidebar.divider()
    p = config.db_path()
    v = db.db_version()
    if v is None:
        st.sidebar.caption("Database: not found")
        return
    size_mb = v[1] / 1e6
    mtime = datetime.fromtimestamp(v[0] / 1e9).strftime("%Y-%m-%d %H:%M")
    st.sidebar.caption(f"Database: `{p.name}` ({size_mb:,.1f} MB)\n\nLast updated: {mtime}")
    try:
        conn = db.connect(read_only=True)
        row = conn.execute(
            "SELECT dataset, MAX(loaded_at) FROM data_load_log WHERE status='success' GROUP BY dataset").fetchall()
        conn.close()
        for ds, ts in row:
            st.sidebar.caption(f"{config.DATASET_LABELS.get(ds, ds)}: loaded {ts} UTC")
    except Exception:
        pass


# ----------------------------------------------------------------------------- widgets / charts
def kpi_row(items: list[tuple[str, str, str | None]], per_row: int = 3) -> None:
    """items: (label, value_text, help). Renders metric cards, at most ``per_row`` per row so values never truncate."""
    for start in range(0, len(items), per_row):
        chunk = items[start:start + per_row]
        cols = st.columns(per_row)
        for col, (label, value, help_) in zip(cols, chunk):
            col.metric(label, value, help=help_)


def fmt_int(v) -> str:
    return "n/a" if v is None or pd.isna(v) else f"{int(v):,}"


def style_fig(fig: go.Figure, height: int = 360) -> go.Figure:
    face = "Instrument Sans, Segoe UI, sans-serif"
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=52, b=8),
        template="plotly_white", colorway=PLOT_COLORS,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", y=-0.22, font=dict(size=11), bgcolor="rgba(0,0,0,0)"),
        font=dict(family=face, size=12, color="#161616"),
        title=dict(font=dict(family=face, size=14, color="#161616", weight=600), x=0, xanchor="left"),
        hoverlabel=dict(font=dict(family=face, size=12), bgcolor="#F7F7F5", bordercolor="#E4E4DF"),
        xaxis=dict(gridcolor="#EEEEEA", zeroline=False, linecolor="#E4E4DF", ticks="",
                   title_font=dict(size=11, color="#6B6B66")),
        yaxis=dict(gridcolor="#EEEEEA", zeroline=False, linecolor="#E4E4DF", ticks="",
                   title_font=dict(size=11, color="#6B6B66")),
    )
    fig.update_traces(marker_line_width=0, selector=dict(type="bar"))
    return fig


def show_fig(fig: go.Figure, height: int = 360, key: str | None = None) -> None:
    st.plotly_chart(
        style_fig(fig, height), width="stretch", key=key,
        config={"displaylogo": False, "displayModeBar": "hover",
                "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"]},
    )


def show_table(df: pd.DataFrame, hide_index: bool = True, height: int | str = "auto", **kw) -> None:
    st.dataframe(df, width="stretch", hide_index=hide_index, height=height, **kw)


def download(df: pd.DataFrame, filename: str, label: str = "Download CSV", key: str | None = None) -> None:
    st.download_button(label, df.to_csv(index=False).encode("utf-8"), file_name=filename,
                       mime="text/csv", key=key or f"dl_{filename}", width="content")
