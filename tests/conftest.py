import hashlib
import shutil
import sqlite3
from pathlib import Path

import pytest

import builders
from equipment import config, db
from equipment.ai4i import loader as ai4i_loader
from equipment.azure import loader as azure_loader


@pytest.fixture
def azure_dir(tmp_path):
    d = tmp_path / "azure"
    builders.write_azure_csvs(d)
    return d


@pytest.fixture
def ai4i_dir(tmp_path):
    d = tmp_path / "ai4i"
    builders.write_ai4i_csv(d)
    return d


@pytest.fixture
def frames():
    return builders.azure_frames()


@pytest.fixture
def azure_db(tmp_path, azure_dir):
    """Controlled Azure data loaded into a temporary database."""
    path = tmp_path / "test.db"
    rep = azure_loader.load_azure(path, azure_dir)
    assert rep.status == "loaded"
    return path


@pytest.fixture
def both_db(tmp_path, azure_dir, ai4i_dir):
    path = tmp_path / "both.db"
    azure_loader.load_azure(path, azure_dir)
    ai4i_loader.load_ai4i(path, ai4i_dir / "ai4i2020.csv")
    return path


@pytest.fixture
def ai4i_db(tmp_path, ai4i_dir):
    path = tmp_path / "ai4i.db"
    assert ai4i_loader.load_ai4i(path, ai4i_dir / "ai4i2020.csv").status == "loaded"
    return path


@pytest.fixture
def conn(azure_db):
    c = db.connect(azure_db, read_only=True)
    yield c
    c.close()


@pytest.fixture
def aconn(ai4i_db):
    c = db.connect(ai4i_db, read_only=True)
    yield c
    c.close()


def table_fingerprint(path, tables):
    """Row-level SHA-256 per table (order independent) - used to prove tables were not changed."""
    c = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    out = {}
    for t in tables:
        h = hashlib.sha256()
        n = 0
        for row in c.execute(f"SELECT * FROM {t} ORDER BY 1,2,3"):
            h.update(repr(tuple(row)).encode())
            n += 1
        out[t] = (n, h.hexdigest())
    c.close()
    return out


AZURE_TABLE_NAMES = ["azure_machines", "azure_telemetry", "azure_errors", "azure_maintenance", "azure_failures"]


@pytest.fixture(scope="session")
def real_db():
    """The real database built from the downloaded data (skip when it has not been built)."""
    path = config.ROOT / "database" / "equipment.db"
    if not path.exists():
        pytest.skip("database/equipment.db has not been built")
    return path
