"""Smart Equipment Maintenance and Failure Analytics - Streamlit entry point.

Run:  streamlit run app.py
"""
import streamlit as st

from equipment import config, navigation, ui

st.set_page_config(page_title="Smart Equipment", page_icon=":material/precision_manufacturing:",
                   layout="wide", initial_sidebar_state="expanded")
ui.inject_css()

ui.brand()
OPTIONS = [config.DATASET_AZURE, config.DATASET_AI4I, "both"]
LABELS = {config.DATASET_AZURE: "Microsoft Azure Predictive Maintenance",
          config.DATASET_AI4I: "AI4I 2020 Predictive Maintenance",
          "both": "Comparative Analysis"}
# The selection lives in a plain session-state key ("dataset"): st.switch_page reruns the script without widget state,
# which would otherwise reset the radio to its default and silently undo the user's choice.
st.session_state.setdefault("dataset", config.DATASET_AZURE)


def _remember_choice() -> None:
    st.session_state["dataset"] = st.session_state["dataset_radio"]


st.sidebar.radio("Dataset", OPTIONS, index=OPTIONS.index(st.session_state["dataset"]), format_func=LABELS.get,
                 key="dataset_radio", on_change=_remember_choice)
choice = st.session_state["dataset"]

pages = navigation.build_pages(choice)
if st.session_state.get("_active_dataset", choice) != choice:
    st.session_state["_active_dataset"] = choice
    st.switch_page(navigation.first_page(choice, pages))     # leave a page of the previous dataset
st.session_state["_active_dataset"] = choice

navigation.sidebar_links(choice, pages)
if choice == config.DATASET_AZURE:
    st.sidebar.divider()
    ui.azure_sidebar()
elif choice == config.DATASET_AI4I:
    st.sidebar.divider()
    ui.ai4i_sidebar()
ui.sidebar_footer()

nav = st.navigation(list(pages.values()), position="hidden")
try:
    nav.run()
except (ui.db.DatabaseNotFoundError, ui.DatabaseBusyError) as exc:
    st.error(str(exc))
    st.caption("Open Module 01 (Data & Database) to build or repair the database.")
