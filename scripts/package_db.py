"""Compress database/equipment.db to database/equipment.db.gz for hosted deployments."""
import gzip
import shutil
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "database" / "equipment.db"

with open(DB, "rb") as src, open(DB.with_suffix(".db.gz"), "wb") as raw:
    with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9, mtime=0, filename="equipment.db") as out:
        shutil.copyfileobj(src, out)
print(f"Wrote {DB.with_suffix('.db.gz')}")
