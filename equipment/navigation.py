"""Which pages exist for each dataset selection. Kept free of Streamlit calls so it can be unit-tested."""
from __future__ import annotations

from . import config

# (file under pages/, title, icon)
AZURE_PAGES = [
    ("azure_overview.py", "Executive Overview", ":material/dashboard:"),
    ("azure_m1_data.py", "Module 01 - Data & Database", ":material/database:"),
    ("azure_m2_failures.py", "Module 02 - Failure & Error Analytics", ":material/query_stats:"),
    ("azure_m3_maintenance.py", "Module 03 - Maintenance Effectiveness", ":material/build:"),
    ("azure_m4_risk.py", "Module 04 - Health & Risk Scoring", ":material/health_and_safety:"),
    ("azure_m5_priority.py", "Module 05 - Maintenance Priority", ":material/fact_check:"),
]
AI4I_PAGES = [
    ("ai4i_overview.py", "AI4I Overview", ":material/dashboard:"),
    ("ai4i_m1_data.py", "Module 01 - Data & Database", ":material/database:"),
    ("ai4i_m2_failures.py", "Module 02 - Failures & Conditions", ":material/query_stats:"),
    ("ai4i_m3_maintenance.py", "Module 03 - Maintenance Effectiveness", ":material/build:"),
    ("ai4i_m4_risk.py", "Module 04 - Operating-Condition Risk", ":material/health_and_safety:"),
    ("ai4i_m5_priority.py", "Module 05 - Inspection Priority", ":material/fact_check:"),
]
COMPARATIVE_PAGES = [("comparative.py", "Comparative Analysis", ":material/compare_arrows:")]

SPECS = {
    config.DATASET_AZURE: AZURE_PAGES,
    config.DATASET_AI4I: AI4I_PAGES,
    "both": COMPARATIVE_PAGES,
}
SECTION = {
    config.DATASET_AZURE: "Azure Predictive Maintenance",
    config.DATASET_AI4I: "AI4I 2020 Predictive Maintenance",
    "both": "Comparative",
}


def page_specs(dataset: str) -> list[tuple[str, str, str]]:
    if dataset not in SPECS:
        raise ValueError(f"unknown dataset selection '{dataset}'")
    return SPECS[dataset]


def _url_path(fname: str) -> str:
    return fname.removesuffix(".py")


def build_pages(dataset: str) -> dict:
    """Every page of the app (file name -> st.Page), to be registered in ONE hidden navigation.

    Registering all pages keeps the current page valid when the dataset selection changes; only the pages of ``dataset``
    get a sidebar link (``sidebar_links``) and the first of them is the default page. Each page also calls ``ui.guard``
    so a page of another dataset can never render under the wrong selection.
    """
    import streamlit as st
    first = page_specs(dataset)[0][0]
    pages = {}
    for key in SPECS:
        for fname, title, icon in SPECS[key]:
            pages[fname] = st.Page(f"pages/{fname}", title=title, url_path=_url_path(fname),
                                   default=(fname == first))
    return pages


def sidebar_links(dataset: str, pages: dict) -> None:
    """Sidebar navigation that shows only the pages of the selected dataset."""
    import streamlit as st
    st.sidebar.caption(SECTION[dataset])
    for fname, title, icon in page_specs(dataset):
        st.sidebar.page_link(pages[fname], label=title)


def first_page(dataset: str, pages: dict):
    return pages[page_specs(dataset)[0][0]]
