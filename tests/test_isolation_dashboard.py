"""Dataset isolation (Azure unchanged by AI4I and vice versa), comparative page rules, selector and dashboard behaviour."""
import ast
import re

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import builders
from conftest import AZURE_TABLE_NAMES, table_fingerprint
from equipment import comparative, config, db, navigation, ui
from equipment.ai4i import analytics as A
from equipment.ai4i import loader as AL
from equipment.ai4i import risk as AR
from equipment.azure import analytics as AZ
from equipment.azure import loader as ZL
from equipment.azure import priority as P
from equipment.azure import risk as ZR

APP = str(config.ROOT / "app.py")


# ------------------------------------------------------------------ isolation: data
def test_loading_ai4i_leaves_every_azure_table_identical(tmp_path, azure_dir, ai4i_dir):
    path = tmp_path / "iso.db"
    ZL.load_azure(path, azure_dir)
    before = table_fingerprint(path, AZURE_TABLE_NAMES)
    AL.load_ai4i(path, ai4i_dir / "ai4i2020.csv")
    AL.load_ai4i(path, ai4i_dir / "ai4i2020.csv", force=True)
    assert table_fingerprint(path, AZURE_TABLE_NAMES) == before


def test_loading_azure_leaves_the_ai4i_table_identical(tmp_path, azure_dir, ai4i_dir):
    path = tmp_path / "iso2.db"
    AL.load_ai4i(path, ai4i_dir / "ai4i2020.csv")
    before = table_fingerprint(path, ["ai4i_machine_data"])
    ZL.load_azure(path, azure_dir)
    ZL.load_azure(path, azure_dir, force=True)
    assert table_fingerprint(path, ["ai4i_machine_data"]) == before


def test_azure_results_are_identical_with_and_without_ai4i_present(azure_db, both_db):
    a, b = db.connect(azure_db, read_only=True), db.connect(both_db, read_only=True)
    try:
        f = AZ.default_filters(a)
        assert AZ.kpis(a, f) == AZ.kpis(b, f)
        pd.testing.assert_frame_equal(ZR.compute_risk(a, f, ZR.RiskConfig.default()),
                                      ZR.compute_risk(b, f, ZR.RiskConfig.default()))
        pd.testing.assert_frame_equal(AZ.query(a, "failures_by_component", f), AZ.query(b, "failures_by_component", f))
    finally:
        a.close()
        b.close()


def test_ai4i_results_are_identical_with_and_without_azure_present(ai4i_db, both_db):
    a, b = db.connect(ai4i_db, read_only=True), db.connect(both_db, read_only=True)
    try:
        assert A.overview(a) == A.overview(b)
        pd.testing.assert_frame_equal(AR.compute_risk(a), AR.compute_risk(b))
    finally:
        a.close()
        b.close()


def test_each_dataset_works_when_the_other_is_absent(azure_db, ai4i_db):
    az = db.connect(azure_db, read_only=True)
    ai = db.connect(ai4i_db, read_only=True)
    try:
        assert not db.table_exists(az, "ai4i_machine_data") and AZ.kpis(az, AZ.default_filters(az))["machines"] == 3
        assert not db.table_exists(ai, "azure_machines") and A.overview(ai)["total_records"] == 60
    finally:
        az.close()
        ai.close()


def test_load_log_and_rejections_are_tagged_per_dataset(both_db):
    c = db.connect(both_db, read_only=True)
    ds = {r[0] for r in c.execute("SELECT DISTINCT dataset FROM data_load_log")}
    c.close()
    assert ds == {"azure", "ai4i"}


def test_ai4i_reload_only_replaces_its_own_rejections(tmp_path, azure_dir):
    azure_dir_frames = builders.azure_frames()
    azure_dir_frames["errors"] = pd.concat([azure_dir_frames["errors"], azure_dir_frames["errors"].iloc[:1]])  # duplicate
    d = tmp_path / "az2"
    builders.write_azure_csvs(d, azure_dir_frames)
    path = tmp_path / "rej.db"
    ZL.load_azure(path, d)
    c = db.connect(path, read_only=True)
    n_az = c.execute("SELECT COUNT(*) FROM rejected_records WHERE dataset='azure'").fetchone()[0]
    c.close()
    assert n_az >= 1
    bad = builders.ai4i_frame().astype(object)
    bad.loc[0, "Type"] = "X"
    AL.load_ai4i(path, builders.write_ai4i_csv(tmp_path / "ai", df=bad))
    c = db.connect(path, read_only=True)
    assert c.execute("SELECT COUNT(*) FROM rejected_records WHERE dataset='azure'").fetchone()[0] == n_az
    assert c.execute("SELECT COUNT(*) FROM rejected_records WHERE dataset='ai4i'").fetchone()[0] == 1
    c.close()


# ------------------------------------------------------------------ isolation: code
def _sql_without_comments(path):
    return "\n".join(re.sub(r"--.*", "", ln) for ln in path.read_text(encoding="utf-8").splitlines()).lower()


def _py_references(path):
    """Imported module names, identifiers/attributes used, and non-docstring string literals of a python file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports, names, strings = set(), set(), []
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
                  and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            imports.add(("." * n.level) + (n.module or ""))
            imports.update(a.name for a in n.names)
        elif isinstance(n, ast.Import):
            imports.update(a.name for a in n.names)
        elif isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            names.add(n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings:
            strings.append(n.value.lower())
    return imports, names, strings


def test_ai4i_sql_never_references_azure_tables():
    for p in sorted((config.ROOT / "sql" / "ai4i").glob("*.sql")):
        assert "azure" not in _sql_without_comments(p), f"{p.name} references Azure objects"


def test_azure_sql_never_references_ai4i_tables():
    for p in sorted((config.ROOT / "sql" / "azure").glob("*.sql")):
        assert "ai4i" not in _sql_without_comments(p), f"{p.name} references AI4I objects"


def test_ai4i_python_never_imports_or_queries_azure():
    for p in sorted((config.ROOT / "equipment" / "ai4i").glob("*.py")):
        imports, names, strings = _py_references(p)
        assert not any("azure" in i.lower() for i in imports), p.name
        assert not any("azure" in s for s in strings), f"{p.name}: azure in a string literal"


def test_azure_python_never_imports_or_queries_ai4i():
    for p in sorted((config.ROOT / "equipment" / "azure").glob("*.py")):
        imports, names, strings = _py_references(p)
        assert not any("ai4i" in i.lower() for i in imports), p.name
        assert not any("ai4i" in s for s in strings), f"{p.name}: ai4i in a string literal"


def test_dataset_pages_only_use_their_own_modules():
    for p in sorted((config.ROOT / "pages").glob("azure_*.py")):
        imports, names, _ = _py_references(p)
        assert not any("ai4i" in i.lower() for i in imports), p.name
        assert not names & {"ai_q", "ai_risk_scores", "ai_eval", "ai4i_filters", "ai4i_cfg", "ai4i_sidebar"}, p.name
    for p in sorted((config.ROOT / "pages").glob("ai4i_*.py")):
        imports, names, _ = _py_references(p)
        assert not any("azure" in i.lower() for i in imports), p.name
        assert not names & {"az_q", "az_risk_scores", "azure_filters", "azure_cfg", "azure_sidebar", "AZURE_TABLES"}, p.name


def test_every_dataset_page_guards_its_dataset():
    for p in sorted((config.ROOT / "pages").glob("azure_*.py")):
        assert 'ui.guard("azure")' in p.read_text(), p.name
    for p in sorted((config.ROOT / "pages").glob("ai4i_*.py")):
        assert 'ui.guard("ai4i")' in p.read_text(), p.name


# ------------------------------------------------------------------ comparative page rules
def test_comparative_module_has_no_join_between_datasets():
    src = (config.ROOT / "equipment" / "comparative.py").read_text()
    sql_like = re.findall(r"(?is)(select .*?)(?:\"\"\"|\"\)|$)", src)
    for s in sql_like:
        assert not re.search(r"(?i)\bjoin\b", s) or ("azure" not in s.lower() or "ai4i" not in s.lower())
    assert not re.search(r"(?i)azure_\w+\s+\w*\s*(inner |left )?join\s+ai4i", src)
    assert not re.search(r"(?i)ai4i_\w+\s+\w*\s*(inner |left )?join\s+azure", src)
    assert not (config.ROOT / "sql" / "comparative").exists()


def test_comparative_tables_and_counts(both_db):
    c = db.connect(both_db, read_only=True)
    try:
        counts = comparative.record_counts(c).set_index("Table").Rows
        assert counts["azure_machines"] == 3 and counts["ai4i_machine_data"] == 60
        props = comparative.failure_proportions(c)
        assert (props["Directly comparable?"] == "No").all()          # never presented as like-for-like
        assert not props.empty and props["AI4I 2020"].notna().any() and props["Azure"].notna().any()
        assert not comparative.quality_summary(c).empty
    finally:
        c.close()


def test_comparative_works_with_only_one_dataset_loaded(azure_db, ai4i_db):
    for path in (azure_db, ai4i_db):
        c = db.connect(path, read_only=True)
        try:
            assert not comparative.record_counts(c).empty
            assert not comparative.failure_proportions(c).empty
        finally:
            c.close()


def test_comparative_limitations_are_documented():
    text = " ".join(str(v) for v in comparative.LIMITATIONS.values()).lower()
    for phrase in ["synthetic", "not", "machine"]:
        assert phrase in text
    assert not comparative.DEFINITIONS.empty and not comparative.SUPPORTED.empty


# ------------------------------------------------------------------ navigation
def test_navigation_page_sets_are_disjoint_and_files_exist():
    sets = {k: {f for f, _, _ in navigation.page_specs(k)} for k in ("azure", "ai4i", "both")}
    assert sets["azure"].isdisjoint(sets["ai4i"]) and sets["both"].isdisjoint(sets["azure"] | sets["ai4i"])
    for files in sets.values():
        for f in files:
            assert (config.ROOT / "pages" / f).exists(), f
    assert all(f.startswith("azure_") for f in sets["azure"]) and all(f.startswith("ai4i_") for f in sets["ai4i"])
    assert sets["both"] == {"comparative.py"}


def test_every_page_file_is_reachable_from_some_selection():
    listed = {f for k in ("azure", "ai4i", "both") for f, _, _ in navigation.page_specs(k)}
    on_disk = {p.name for p in (config.ROOT / "pages").glob("*.py")}
    assert listed == on_disk


def test_each_dataset_has_five_modules_and_an_overview():
    for k in ("azure", "ai4i"):
        titles = [t for _, t, _ in navigation.page_specs(k)]
        assert len(titles) == 6 and sum("Module 0" in t for t in titles) == 5


def test_unknown_selection_is_rejected():
    with pytest.raises(ValueError):
        navigation.page_specs("nonsense")


# ------------------------------------------------------------------ streamlit app (AppTest)
@pytest.fixture
def app_env(both_db, monkeypatch):
    import streamlit as st
    monkeypatch.setenv("SE_DB_PATH", str(both_db))
    st.cache_data.clear()
    yield both_db
    st.cache_data.clear()


def _banner(at):
    return " ".join(m.value for m in at.markdown if "ds-banner" in m.value)


def _run(dataset, page=None):
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["dataset"] = dataset
    at.run()
    if page:
        at.switch_page(f"pages/{page}")
        at.run()
    return at


def _metrics(at):
    return {m.label: m.value for m in at.metric}


def test_default_selection_is_azure(app_env):
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert at.radio(key="dataset_radio").value == "azure"
    assert "Microsoft Azure" in _banner(at)


def test_selector_offers_exactly_three_options(app_env):
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert list(at.radio(key="dataset_radio").options) == [
        "Microsoft Azure Predictive Maintenance", "AI4I 2020 Predictive Maintenance", "Comparative Analysis"]


def test_banner_and_kpis_follow_the_selection(app_env):
    az = _run("azure")
    assert not az.exception and "Active dataset: Microsoft Azure" in _banner(az)
    assert _metrics(az)["Total machines"] == "3"
    ai = _run("ai4i")
    assert not ai.exception and "Active dataset: AI4I 2020" in _banner(ai)
    assert _metrics(ai)["Records"] == "60" and _metrics(ai)["Machine failures"] == "7"
    assert "Total machines" not in _metrics(ai) and "Records" not in _metrics(az)
    both = _run("both")
    assert not both.exception and "Comparative Analysis" in _banner(both)


def _session(dataset, **state):
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["dataset"] = dataset
    for k, v in state.items():
        at.session_state[k] = v
    return at.run()


def test_azure_filter_applies_and_does_not_leak_into_ai4i(app_env):
    az = _session("azure", az_machines=[1])
    assert not az.exception and _metrics(az)["Total machines"] == "1"
    ai = _session("ai4i", az_machines=[1])                        # same stale Azure filter, other dataset
    assert not ai.exception and _metrics(ai)["Records"] == "60"


def test_ai4i_filter_applies_and_does_not_leak_into_azure(app_env):
    ai = _session("ai4i", ai_types=["L"])
    expected = int((builders.ai4i_frame()["Type"] == "L").sum())
    assert not ai.exception and _metrics(ai)["Records"] == str(expected)
    az = _session("azure", ai_types=["L"])
    assert not az.exception and _metrics(az)["Total machines"] == "3"


def test_ai4i_range_filter_reduces_records_consistently_with_sql(app_env):
    at = _session("ai4i", ai_wear=(100.0, 200.0))
    exp = int(builders.ai4i_frame()["Tool wear [min]"].between(100, 200).sum())
    assert not at.exception and _metrics(at)["Records"] == str(exp)


@pytest.mark.parametrize("dataset", ["azure", "ai4i", "both"])
def test_every_page_renders_without_exception(app_env, dataset):
    for fname, title, _ in navigation.page_specs(dataset):
        at = _run(dataset, fname)
        assert not at.exception, f"{fname}: {[e.value for e in at.exception]}"
        assert not at.error, f"{fname}: {[e.value for e in at.error]}"


def test_ai4i_module3_explains_it_is_unsupported(app_env):
    at = _run("ai4i", "ai4i_m3_maintenance.py")
    assert not at.exception
    assert any("Not supported" in w.value for w in at.warning)
    assert not at.dataframe and not at.get("plotly_chart")
    assert any("time stamp" in m.value for m in at.markdown)


def test_ai4i_module5_ranks_records_and_groups_not_machines(app_env):
    at = _run("ai4i", "ai4i_m5_priority.py")
    assert not at.exception
    text = " ".join(m.value for m in at.markdown).lower()
    assert "not distinct physical machines" in text and "recommendations, not" in text
    assert len(at.tabs) == 2 and len(at.dataframe) >= 1


def test_azure_module5_has_search_sort_filter_and_download(app_env):
    at = _run("azure", "azure_m5_priority.py")
    assert not at.exception and len(at.dataframe) >= 1
    assert at.get("download_button")


def test_ai4i_pages_offer_csv_download(app_env):
    at = _run("ai4i", "ai4i_m5_priority.py")
    assert at.get("download_button")


def test_page_of_another_dataset_is_refused_when_not_selected(app_env):
    at = AppTest.from_file(str(config.ROOT / "pages" / "ai4i_overview.py"), default_timeout=60)
    at.session_state["dataset"] = "azure"
    at.run()
    assert any("different dataset" in w.value for w in at.warning)
    assert not at.metric


def test_missing_database_shows_a_friendly_error(tmp_path, monkeypatch):
    import streamlit as st
    st.cache_data.clear()
    monkeypatch.setenv("SE_DB_PATH", str(tmp_path / "missing.db"))
    for ds in ("azure", "ai4i"):
        at = _run(ds)
        assert not at.exception
        assert at.error or at.warning
        assert any("load_data.py" in e.value for e in at.error)
    st.cache_data.clear()


def test_database_without_ai4i_tables_only_affects_ai4i_pages(azure_db, monkeypatch):
    import streamlit as st
    st.cache_data.clear()
    monkeypatch.setenv("SE_DB_PATH", str(azure_db))
    az = _run("azure", "azure_overview.py")
    assert not az.exception and not az.error and _metrics(az)["Total machines"] == "3"
    ai = _run("ai4i", "ai4i_overview.py")
    assert not ai.exception and any("ai4i_machine_data" in e.value for e in ai.error)
    st.cache_data.clear()


# ------------------------------------------------------------------ caching without staleness
def test_cache_is_refreshed_when_the_database_file_changes(tmp_path, ai4i_dir, monkeypatch):
    import streamlit as st
    st.cache_data.clear()
    path = tmp_path / "cache.db"
    monkeypatch.setenv("SE_DB_PATH", str(path))
    csv = ai4i_dir / "ai4i2020.csv"
    AL.load_ai4i(path, csv)
    first = ui.cached_call("equipment.ai4i.analytics.overview")
    again = ui.cached_call("equipment.ai4i.analytics.overview")
    assert first["total_records"] == again["total_records"] == 60
    v1 = db.db_version()
    builders.write_ai4i_csv(ai4i_dir, df=builders.ai4i_frame().iloc[:45])
    AL.load_ai4i(path, csv)
    assert db.db_version() != v1
    assert ui.cached_call("equipment.ai4i.analytics.overview")["total_records"] == 45
    st.cache_data.clear()


def test_db_version_is_none_when_database_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SE_DB_PATH", str(tmp_path / "none.db"))
    assert db.db_version() is None


# ------------------------------------------------------------------ exports
def test_azure_priority_csv_export_round_trips(azure_db):
    import io
    c = db.connect(azure_db, read_only=True)
    cfg = ZR.RiskConfig.default()
    risk = ZR.compute_risk(c, AZ.default_filters(c), cfg)
    c.close()
    table = P.build_priority_table(risk, cfg)
    back = pd.read_csv(io.BytesIO(P.to_csv_bytes(table)))
    assert len(back) == len(table) == 3
    assert list(back.columns) == list(table.columns)
    assert set(back["Machine ID"]) == {1, 2, 3}


def test_ai4i_export_content_is_utf8_csv_and_matches_the_table(ai4i_db):
    import io
    c = db.connect(ai4i_db, read_only=True)
    risk = AR.compute_risk(c)
    c.close()
    data = risk.round(3).to_csv(index=False).encode("utf-8")
    back = pd.read_csv(io.BytesIO(data))
    assert len(back) == 60 and back["udi"].is_unique
    assert back["risk_score"].between(0, 100).all()


# ------------------------------------------------------------------ the real database: Azure must be unchanged
@pytest.mark.real_data
def test_real_database_contains_both_datasets_with_expected_azure_counts(real_db):
    c = db.connect(real_db, read_only=True)
    try:
        expected = {"azure_machines": 100, "azure_telemetry": 876_100, "azure_errors": 3_919,
                    "azure_maintenance": 3_286, "azure_failures": 761, "ai4i_machine_data": 10_000}
        for t, n in expected.items():
            assert db.table_count(c, t) == n, t
    finally:
        c.close()
