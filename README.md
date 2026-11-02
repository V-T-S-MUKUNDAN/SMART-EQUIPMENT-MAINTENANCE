# Smart Equipment Maintenance and Failure Analytics

Advanced **SQL** analytics (not ML) over two independent predictive-maintenance datasets in one SQLite database, with a Streamlit dashboard.

| Dataset | Role | Observation unit |
|---|---|---|
| [Microsoft Azure Predictive Maintenance](https://www.kaggle.com/datasets/arnabbiswas1/microsoft-azure-predictive-maintenance) | Five CSVs: machines, hourly telemetry, errors, maintenance, failures | 100 machines over ~1 year of hourly readings |
| [AI4I 2020 Predictive Maintenance](https://archive.ics.uci.edu/dataset/601/ai4i+2020+predictive+maintenance+dataset) | One CSV of 10,000 labelled process records | Independent synthetic records (UDI is **not** a timestamp) |

They share no identifiers and are **never joined**.

## Exact commands

From the project root (the folder that contains `app.py`):

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

# Download the source CSVs first (they are not in this repo; see "Data setup"), so that you have:
#   data/azure/PdM_machines.csv
#   data/azure/PdM_telemetry.csv
#   data/azure/PdM_errors.csv
#   data/azure/PdM_maint.csv
#   data/azure/PdM_failures.csv
#   data/ai4i2020.csv

python scripts/load_data.py
streamlit run app.py
```

Load one dataset only, or force a rewrite:

```bash
python scripts/load_data.py --dataset azure
python scripts/load_data.py --dataset ai4i --force
python scripts/load_data.py --db /tmp/equipment.db
```

Tests (use small synthetic CSVs, so they need no download; the 5 tests marked `real_data` run only when `database/equipment.db` exists and are skipped otherwise):

```bash
source .venv/bin/activate
python -m pytest tests -q
```

Environment overrides: `SE_DB_PATH`, `SE_AZURE_DIR`, `SE_AI4I_DIR`, `SE_CONFIG_DIR`.

## Data setup

The CSVs are gitignored because of their size, so a fresh clone has no data. `scripts/load_data.py` stops with "cannot load" until they are in place.

1. Create the folder: `mkdir -p data/azure`
2. Download the [Azure dataset from Kaggle](https://www.kaggle.com/datasets/arnabbiswas1/microsoft-azure-predictive-maintenance) (free account needed) and put the five `PdM_*.csv` files in `data/azure/`.
3. Download the [AI4I 2020 dataset from UCI](https://archive.ics.uci.edu/dataset/601/ai4i+2020+predictive+maintenance+dataset) and put `ai4i2020.csv` in `data/`. The UTF-8 BOM on its header is handled by the loader.

## Project structure

```
app.py                      Streamlit entry: dataset radio + hidden navigation
pages/                      13 pages (6 Azure, 6 AI4I, 1 comparative)
equipment/                  Python package
  azure/                    validation, loader, analytics, risk, maintenance, priority
  ai4i/                     loader, analytics, risk
  comparative.py            side-by-side summaries, no joins
  db.py, loading.py, ui.py, navigation.py, readiness.py, charts.py
sql/azure|ai4i|common/      schema + named queries
config/azure_risk.json
config/ai4i_risk.json
scripts/load_data.py
tests/                      pytest (controlled data + real_data)
docs/                       schema, SQL techniques, risk methodology
database/equipment.db       built by the loader (gitignored)
```

## How to use the dashboard

Sidebar **Dataset**:

1. **Microsoft Azure Predictive Maintenance** — six Azure pages, Azure filters (date, machines, components).
2. **AI4I 2020 Predictive Maintenance** — six AI4I pages, AI4I filters (type, tool wear, torque, rpm, temperatures).
3. **Comparative Analysis** — one page; no Azure/AI4I filters.

An **Active dataset** banner is on every page. Switching dataset replaces the page set and jumps to that dataset's overview. Pages of the other dataset are not listed; each page also calls `ui.guard`.

SQL results are cached with `st.cache_data` keyed on `db.db_version()` (file mtime_ns + size), so a completed load is visible on the next rerun without a stale cache.

## Modules

### Azure

| Module | What it does |
|---|---|
| Overview | KPIs, risk-category bar, monthly failure trend, top-priority machines with reasons |
| M1 Data & Database | Validate CSVs, load, row counts vs documented 100 / 876,100 / 3,919 / 3,286 / 761, indexes, integrity, load log, rejected rows, `EXPLAIN QUERY PLAN` |
| M2 Failure & Error Analytics | Counts, rates, matrices, repeats (`LAG` gaps), trend (recursive month calendar), model/age bands, errors-before-failures (`EXISTS`), drill-down |
| M3 Maintenance Effectiveness | Before/after telemetry around a chosen event; confounders listed; **observed change, not causation** |
| M4 Health & Risk Scoring | Configurable weights/thresholds; score = sum of eight contributions; evidence tables |
| M5 Maintenance Priority | Ranked machines, search/filter/CSV, rule-based inspection **recommendations** (not diagnoses) |

### AI4I

| Module | What it does |
|---|---|
| Overview | Record KPIs, failure rate by type, mode counts, top records by indicator |
| M1 Data & Database | Header detection (incl. BOM), load, 10,000-row check, consistency warnings, indexes, integrity |
| M2 Failures & Conditions | Overview, independent mode counts + co-occurrence, label inconsistencies **flagged not fixed**, condition bins, window functions over type × wear band, rule agreement |
| M3 Maintenance Effectiveness | **Not supported** — no timestamps, no maintenance log, rows are not a machine history. Explanation only; no invented events |
| M4 Operating-Condition Risk | Label-independent indicator (tool wear, strain, power, heat); category/decile/threshold/AUC evaluation |
| M5 Inspection Priority | Ranks **records and groups**, not machines; search/filter/CSV; recommendations |

### Comparative

Independent summaries placed side by side: table sizes, load-log quality, label definitions, proportions **with their own denominators** and a "Directly comparable? **No**" column, capability matrix, limitations. Raw failure counts are not compared. Risk scores are not compared. No SQL join.

## Validation results

Executed against `database/equipment.db` after `python scripts/load_data.py`.

### Azure (source values preserved; SHA-256 of every azure_* table unchanged after the AI4I load)

| Table | Rows |
|---|---|
| azure_machines | 100 |
| azure_telemetry | 876,100 |
| azure_errors | 3,919 |
| azure_maintenance | 3,286 |
| azure_failures | 761 |

Integrity: `PRAGMA integrity_check = ok`, 0 foreign-key violations.

Azure risk (default config, as-of 2016-01-01, all machines): mean **46.33**, median 46.02, min 19.54, max 72.81. Categories: Low 2, Medium 59, High **39**, Critical **0**. (If another report listed a different mean or a non-zero Critical count, it used different weights, a different as-of date, or a different implementation.)

### AI4I (`data/ai4i2020.csv`)

| | |
|---|---|
| Records | 10,000 |
| Distinct UDI | 10,000 |
| Machine failure = 1 | 339 (3.39%) |
| Records with any mode flag | 348 |
| Multiple mode flags | 24 |
| failure_without_mode | 9 |
| mode_without_failure | 18 (all RNF) |
| Type L / M / H | 6,000 / 2,997 / 1,003 records; 235 / 83 / 21 failures |

Mode flags (independent; need not sum to 339): HDF 115, OSF 98, PWF 95, TWF 46, RNF 19.

AI4I indicator (default config): mean **32.3**, 3,560 Low / 5,170 Medium / 1,190 High / 80 Critical. In-sample AUC ≈ 0.867 (descriptive; see `docs/risk_methodology.md`).

## Tests actually executed

```
python -m pytest tests -q
# fresh clone, no data: 169 passed, 5 skipped
```

| File | What it covers |
|---|---|
| `tests/test_azure_pipeline.py` | CSV validation, counts, value preservation, indexes, integrity, idempotency, force reload, duplicates, rejected rows, rollback, real counts |
| `tests/test_azure_sql.py` | SQL vs pandas, join fan-out, window functions, filters, injection safety |
| `tests/test_azure_risk_maintenance.py` | Score bounds, contribution sum, filter isolation, missing telemetry, configurable weights/thresholds, before/after, priority CSV |
| `tests/test_ai4i.py` | BOM/header detection, column variants, uniqueness, idempotency, rollback, SQL vs pandas (overview, types, modes, co-occurrence, consistency, bins, windows, correlations, rule agreement), label independence, AUC, real 10,000-row checks |
| `tests/test_isolation_dashboard.py` | Azure unchanged by AI4I (and vice versa), no cross-dataset SQL objects, navigation isolation, AppTest selector/KPIs/banner/unsupported M3/CSV export/cache freshness, real DB both-dataset counts |

AppTest smoke (not part of pytest): all 13 pages rendered on the real database with 0 exceptions.

## SQL techniques (summary)

CTEs, recursive month calendar, window functions (`LAG`, `RANK`, `ROW_NUMBER`, `NTILE`, running `SUM`, rolling `AVG`, `MIN/MAX OVER ()`), correlated `EXISTS`, conditional aggregation, `UNION ALL` unpivot, Pearson correlation in SQL, AUC from average ranks, `EXPLAIN QUERY PLAN`, `WITHOUT ROWID` telemetry, parameterised filters. Details: [`docs/sql_guide.md`](docs/sql_guide.md). Schema: [`docs/schema.md`](docs/schema.md). Risk: [`docs/risk_methodology.md`](docs/risk_methodology.md).

## Limitations (do not overstate)

- **Azure** is simulated. Failures, errors and maintenance are linked only by machine and time. 743 maintenance rows are failure-linked repairs. Risk scores summarise history; they do not predict the next failure.
- **AI4I 2020** is synthetic: labels follow published rules plus a random term, so associations largely recover those rules. Rows are independent; there is no time, no maintenance log, no error log, and no distinct-machine identity. Nine failure labels have no mode flag; eighteen RNF flags have Machine failure = 0 — flagged, not corrected.
- **Together:** different units (machine-hours vs records), different sampling, different labels. Do not compare raw counts or the two risk scores. The datasets are never joined.
- Module 03 for AI4I is intentionally empty of charts. Inventing a before/after from UDI order would be meaningless.
- Suggested actions are inspection recommendations, not diagnoses, and do not guarantee failure prevention.

## Troubleshooting

| Symptom | What to do |
|---|---|
| `Database not found` | `source .venv/bin/activate && python scripts/load_data.py` |
| `AI4I 2020 csv not found` | Put `ai4i2020.csv` in `data/` or `data/ai4i/` (or set `SE_AI4I_DIR`) |
| Azure file missing | Five `PdM_*.csv` must sit together in `data/azure/` (or `SE_AZURE_DIR`) |
| Load rolled back | Previous rows are still there; the failed attempt is in `data_load_log` |
| Dashboard shows old numbers | Re-run after the load finishes; cache key is the database file version |
| `database is locked` | Wait for `load_data.py` to finish |
| Tests skip `real_data` | Build `database/equipment.db` first; there are 5 such tests |

Python 3.14, SQLite 3.51 (math functions available; `sqrt` / `pi` are also registered in `db.connect` as a fallback).
