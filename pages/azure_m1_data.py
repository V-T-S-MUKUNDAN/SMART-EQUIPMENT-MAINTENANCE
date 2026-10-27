import pandas as pd
import streamlit as st

from equipment import config, db, readiness, ui
from equipment.azure import loader as L
from equipment.azure import validation as V
from equipment.loading import MissingDatasetFilesError, ValidationError

ui.guard("azure")
ui.banner("azure", "Module 01 - Data Acquisition & Database Management")
st.caption("Validate the five Azure PdM CSV files, build the SQLite database, and confirm it is ready for analysis.")

data_dir = config.azure_dir()

# --- source files ------------------------------------------------------------------------------------------
st.subheader("1. Source files")
st.caption(f"Folder: `{data_dir}` (override with the SE_AZURE_DIR environment variable)")
facts = pd.DataFrame([readiness.file_facts(data_dir / name) for name in V.FILES.values()])
facts["Documented rows"] = [V.EXPECTED_COUNTS[k] for k in V.FILES]
ui.show_table(facts)
if not facts["Found"].all():
    st.error("One or more source files are missing. Place the PdM_*.csv files in the folder above.")

# --- build / refresh -----------------------------------------------------------------------------------------
st.subheader("2. Build or refresh the database")
st.write("Loading is transactional and idempotent: unchanged source files are skipped, changed files replace the "
         "Azure tables atomically, and rows that fail validation are kept in `rejected_records` with a reason. "
         "AI4I tables are never touched.")
c1, c2, c3 = st.columns(3)
if c1.button("Validate files only (no database writes)"):
    try:
        with st.spinner("Validating..."):
            validated = V.validate_all(data_dir)
        rows = [{"File": vt.source_file, "Rows read": vt.rows_read, "Valid": vt.rows_valid,
                 "Rejected": len(vt.rejected), "Warnings": "; ".join(vt.warnings)} for vt in validated.values()]
        ui.show_table(pd.DataFrame(rows))
    except (MissingDatasetFilesError, ValidationError) as exc:
        st.error(str(exc))
for label, force, col in (("Load / update Azure data", False, c2), ("Force full reload (Azure only)", True, c3)):
    if col.button(label):
        try:
            with st.spinner("Loading (about 10 seconds)..."):
                rep = L.load_azure(force=force)
            (st.success if rep.ok else st.error)(f"Azure load: {rep.status} in {rep.seconds}s")
            for m in rep.messages:
                st.write(m)
            ui.show_table(rep.to_frame().drop(columns=["warnings"]))
            st.cache_data.clear()
        except (MissingDatasetFilesError, ValidationError) as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"Load failed and was rolled back: {exc}")

# --- readiness ----------------------------------------------------------------------------------------------
st.subheader("3. Database readiness")
try:
    conn = db.connect(read_only=True)
except db.DatabaseNotFoundError as exc:
    st.warning(str(exc))
    st.stop()

try:
    ready, problems = readiness.verdict(conn, "azure", readiness.AZURE_EXPECTED)
    if ready:
        st.success("READY: the Azure dataset is loaded, the integrity check passed, and there are no foreign-key violations.")
    else:
        st.error("NOT READY: " + "; ".join(problems))
    status = readiness.table_status(conn, readiness.AZURE_EXPECTED)
    ui.show_table(status)
    if (status["Status"] == "differs from documented count").any():
        st.warning("Row counts differ from the public dataset documentation. Check the load history and "
                   "rejected records below before using the results.")
    inte = readiness.integrity(conn)
    cc1, cc2 = st.columns(2)
    cc1.metric("PRAGMA integrity_check", "ok" if inte["integrity_ok"] else "FAILED")
    cc2.metric("Foreign-key violations", inte["foreign_key_violations"])

    st.subheader("4. Data-quality report")
    hist = readiness.load_history(conn, "azure")
    if hist.empty:
        ui.empty_state("No load history yet.")
    else:
        ui.show_table(hist)
    rej = readiness.rejected(conn, "azure")
    if rej.empty:
        st.success("No records were rejected during validation (no invalid timestamps, orphan machine IDs, "
                   "non-numeric sensor values or duplicate keys).")
    else:
        st.warning(f"{len(rej)} rejected record(s) shown (kept with reasons, never silently dropped).")
        ui.show_table(rej)

    st.subheader("5. Schema, indexes and query plans")
    with st.expander("Indexes"):
        ui.show_table(readiness.index_list(conn, "azure_"))
    with st.expander("EXPLAIN QUERY PLAN for key queries"):
        plans = {
            "Telemetry range scan for one machine (Module 03)":
                ("SELECT AVG(volt) FROM azure_telemetry WHERE machine_id = :m AND ts >= :a AND ts < :b",
                 {"m": 1, "a": "2015-06-01", "b": "2015-06-08"}),
            "Failures in a date range (Module 02)":
                ("SELECT component, COUNT(*) FROM azure_failures WHERE ts >= :a AND ts < :b GROUP BY component",
                 {"a": "2015-01-01", "b": "2015-07-01"}),
            "Error history for a machine": ("SELECT * FROM azure_errors WHERE machine_id = :m ORDER BY ts", {"m": 1}),
        }
        for title, (sql, p) in plans.items():
            st.markdown(f"**{title}**")
            st.code("\n".join(readiness.query_plan(conn, sql, p)), language="text")
        st.caption("`SEARCH ... USING PRIMARY KEY/INDEX` means an index is used; `SCAN` would be a full-table scan.")
finally:
    conn.close()
