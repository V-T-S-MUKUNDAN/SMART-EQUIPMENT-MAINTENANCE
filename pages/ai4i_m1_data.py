import pandas as pd
import streamlit as st

from equipment import config, db, readiness, ui
from equipment.ai4i import loader as L
from equipment.loading import MissingDatasetFilesError, ValidationError

ui.guard("ai4i")
ui.banner("ai4i", "Module 01 - Data Acquisition & Database Management")
st.caption("Locate and validate the AI4I 2020 csv, load it into its own SQLite table, and check data quality.")

st.subheader("1. Source file")
try:
    path = L.find_ai4i_csv()
    st.success(f"Found `{path}`")
    facts = readiness.file_facts(path)
    ui.show_table(pd.DataFrame([facts]))
except MissingDatasetFilesError as exc:
    st.error(str(exc))
    path = None

st.subheader("2. Column mapping used for ingestion")
ui.show_table(pd.DataFrame({"CSV column": list(L.COLUMN_MAP), "SQLite column": list(L.COLUMN_MAP.values())}))

if path is not None:
    st.subheader("3. Validate and load")
    c1, c2, c3 = st.columns(3)
    if c1.button("Validate file only (no database writes)"):
        try:
            vt = L.validate(path)
            st.write(f"Rows read: **{vt.rows_read:,}** | valid: **{vt.rows_valid:,}** | rejected: **{len(vt.rejected)}**")
            for w in vt.warnings:
                st.warning(w)
            if not vt.warnings:
                st.success("No data-quality warnings.")
        except (ValidationError, MissingDatasetFilesError) as exc:
            st.error(str(exc))
    for label, force, col in (("Load / update AI4I data", False, c2), ("Force reload (AI4I only)", True, c3)):
        if col.button(label):
            try:
                rep = L.load_ai4i(force=force)
                (st.success if rep.ok else st.error)(f"AI4I load: {rep.status} in {rep.seconds}s")
                for m in rep.messages:
                    st.write(m)
                for t in rep.tables:
                    st.write(f"{t.table}: read {t.rows_read:,}, loaded {t.rows_loaded:,}, rejected {t.rows_rejected}")
                    for w in t.warnings:
                        st.warning(w)
                st.cache_data.clear()
            except (ValidationError, MissingDatasetFilesError) as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Load failed and was rolled back: {exc}")
    st.caption("The load is one transaction keyed on the unique UDI. Re-running with an unchanged file does nothing. "
               "Azure tables are never touched.")

st.subheader("4. Database readiness")
try:
    conn = db.connect(read_only=True)
except db.DatabaseNotFoundError as exc:
    st.warning(str(exc))
    st.stop()
try:
    ready, problems = readiness.verdict(conn, "ai4i", readiness.AI4I_EXPECTED)
    (st.success if ready else st.error)(
        "READY: AI4I data loaded, integrity check passed." if ready else "NOT READY: " + "; ".join(problems))
    ui.show_table(readiness.table_status(conn, readiness.AI4I_EXPECTED))
    inte = readiness.integrity(conn)
    c1, c2 = st.columns(2)
    c1.metric("PRAGMA integrity_check", "ok" if inte["integrity_ok"] else "FAILED")
    c2.metric("Foreign-key violations", inte["foreign_key_violations"])

    st.subheader("5. Data-quality report")
    if db.table_count(conn, "ai4i_machine_data"):
        q = db.read_df(conn, """
            SELECT COUNT(*) AS records, COUNT(DISTINCT udi) AS unique_udi, COUNT(DISTINCT product_id) AS unique_product_id,
                   SUM(CASE WHEN label_consistency = 'failure_without_mode' THEN 1 ELSE 0 END) AS failure_without_mode_flag,
                   SUM(CASE WHEN label_consistency = 'mode_without_failure' THEN 1 ELSE 0 END) AS mode_flag_without_failure,
                   SUM(CASE WHEN mode_count > 1 THEN 1 ELSE 0 END) AS multiple_mode_records
            FROM v_ai4i_labeled""")
        ui.show_table(q)
        st.caption("Inconsistent label combinations are flagged here and in Module 02, never modified.")
    hist = readiness.load_history(conn, "ai4i")
    if not hist.empty:
        ui.show_table(hist)
    rej = readiness.rejected(conn, "ai4i")
    if rej.empty:
        st.success("No records rejected during validation.")
    else:
        st.warning("Rejected records (kept with reasons):")
        ui.show_table(rej)
    with st.expander("Indexes"):
        ui.show_table(readiness.index_list(conn, "ai4i_"))
finally:
    conn.close()
