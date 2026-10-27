import streamlit as st

from equipment import ui

ui.guard("ai4i")
ui.banner("ai4i", "Module 03 - Maintenance Effectiveness Analysis")
ui.require_ready(["ai4i_machine_data"])

st.warning("Not supported for the AI4I 2020 dataset.")
st.markdown("""
A before-and-after maintenance analysis needs, for each machine, **time-stamped telemetry and a log of maintenance events**.
AI4I 2020 contains neither:

- `UDI` is a row identifier, not a time stamp, and rows are independent records - not repeated measurements of the same
  physical machine (every `Product ID` is distinct).
- There is no maintenance, repair or tool-replacement log. `Tool wear [min]` is a per-record measurement, and the TWF label
  is a failure flag, not a maintenance event.

Inventing maintenance events or ordering records by UDI to fake a "before/after" would produce results that mean nothing, so
no chart or metric is shown here.

**What to use instead**
- **Module 02** shows how failure rates change across tool-wear bands (an association between tool wear and recorded failures).
- **Module 03 for the Azure dataset** performs a real before/after comparison, because that dataset has hourly telemetry and a
  maintenance log. Switch the dataset in the sidebar to use it.
""")
