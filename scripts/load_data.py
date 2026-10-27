#!/usr/bin/env python
"""Load one or both datasets into the SQLite database.

Examples
    python scripts/load_data.py                 # both datasets (skips unchanged sources)
    python scripts/load_data.py --dataset azure
    python scripts/load_data.py --dataset ai4i --force
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from equipment import config  # noqa: E402
from equipment.azure.loader import load_azure  # noqa: E402
from equipment.loading import MissingDatasetFilesError, ValidationError  # noqa: E402


def _print(report) -> None:
    print(f"\n=== {report.dataset.upper()}: {report.status} ({report.seconds}s) ===")
    for m in report.messages:
        print("  ", m)
    for t in report.tables:
        print(f"  {t.table:<20} read={t.rows_read:>8,}  loaded={t.rows_loaded:>8,}  "
              f"rejected={t.rows_rejected:>4}  [{t.status}]")
        for w in t.warnings:
            print(f"      warning: {w}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", choices=["azure", "ai4i", "all"], default="all")
    ap.add_argument("--force", action="store_true", help="rewrite tables even if the source is unchanged")
    ap.add_argument("--db", default=None, help=f"SQLite file (default: {config.db_path()})")
    args = ap.parse_args()

    status = 0
    jobs = []
    if args.dataset in ("azure", "all"):
        jobs.append(("azure", lambda: load_azure(args.db, force=args.force)))
    if args.dataset in ("ai4i", "all"):
        from equipment.ai4i.loader import load_ai4i
        jobs.append(("ai4i", lambda: load_ai4i(args.db, force=args.force)))

    for name, job in jobs:
        try:
            _print(job())
        except (MissingDatasetFilesError, ValidationError) as exc:
            print(f"\n[{name}] cannot load: {exc}")
            status = 1
        except Exception as exc:  # transaction already rolled back by the loader
            print(f"\n[{name}] load FAILED and was rolled back: {exc}")
            status = 1
    print(f"\nDatabase: {config.db_path() if not args.db else args.db}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
