# Database schema

Single SQLite file: `database/equipment.db` (override with `SE_DB_PATH`).

Both datasets live in the same file. They share only the bookkeeping tables (`data_load_log`, `rejected_records`). No query joins an `azure_*` object to `ai4i_machine_data`.

Timestamps (Azure) are ISO-8601 text `YYYY-MM-DD HH:MM:SS` (lexicographically sortable; usable with `strftime` / `julianday` / `datetime`).

## Shared tables

Defined in `sql/common/schema.sql`.

| Table | Purpose |
|---|---|
| `data_load_log` | One row per load attempt (`dataset`, source file, SHA-256, rows read/loaded/rejected, status, UTC time). |
| `rejected_records` | Invalid source rows kept with a reason and the original CSV line. Never silently dropped. |

`dataset` is `'azure'` or `'ai4i'`. Reloading a dataset replaces only *its* rejected rows.

## Azure tables (`sql/azure/schema.sql`)

| Table | Grain | Key | Source CSV |
|---|---|---|---|
| `azure_machines` | one machine | `machine_id` PK | `PdM_machines.csv` |
| `azure_telemetry` | one hourly reading | `(machine_id, ts)` PK, `WITHOUT ROWID` | `PdM_telemetry.csv` |
| `azure_errors` | one error event | unique `(machine_id, ts, error_type)` | `PdM_errors.csv` |
| `azure_failures` | one failure event | unique `(machine_id, ts, component)` | `PdM_failures.csv` |
| `azure_maintenance` | one maintenance event | unique `(machine_id, ts, component)` | `PdM_maint.csv` |

Documented row counts (this project, verified): 100 machines, 876,100 telemetry, 3,919 errors, 3,286 maintenance, 761 failures.

The fleet mean/std of volt, rotate, pressure and vibration are computed inside the risk query from readings before the as-of instant, so an earlier score never uses later data. Std-dev is `sqrt(E[x²] − E[x]²)`.

Foreign keys: every event/telemetry row references `azure_machines(machine_id)`. Indexes exist on timestamps and on `(component/error_type, machine_id)`.

### Views (`sql/azure/views.sql`)

Every event table is aggregated to **one row per machine before it is joined**. That is the anti-fan-out rule.

| View | Role |
|---|---|
| `v_azure_machine_failures` | count, first/last failure per machine |
| `v_azure_machine_errors` | count, last error per machine |
| `v_azure_machine_maintenance` | planned vs failure-linked (LEFT JOIN to failures on machine+ts+component; 1:0..1) |
| `v_azure_machine_summary` | machines LEFT JOIN the three aggregates above |
| `v_azure_failure_gaps` | hours between successive failures of the same machine+component (`LAG`) |
| `v_azure_failure_monthly` | monthly failure counts |

743 of 3,286 maintenance rows coincide with a failure of the same component at the same timestamp. Those are treated as repairs, not as planned maintenance, so a failure is not counted twice in the risk score.

## AI4I table (`sql/ai4i/schema.sql`)

| Table | Grain | Key | Source |
|---|---|---|---|
| `ai4i_machine_data` | one independent process record | `udi` PK | `data/ai4i2020.csv` |

Published count: 10,000 records. `UDI` is a row identifier, **not** a timestamp. `Product ID` is unique in the published file; UDI remains the primary key.

### Column mapping (published name → SQLite)

| CSV | SQLite |
|---|---|
| UDI | `udi` |
| Product ID | `product_id` |
| Type | `machine_type` (`L`/`M`/`H`) |
| Air temperature [K] | `air_temp_k` |
| Process temperature [K] | `process_temp_k` |
| Rotational speed [rpm] | `rotational_speed_rpm` |
| Torque [Nm] | `torque_nm` |
| Tool wear [min] | `tool_wear_min` |
| Machine failure | `machine_failure` |
| TWF, HDF, PWF, OSF, RNF | `twf`, `hdf`, `pwf`, `osf`, `rnf` |

The loader matches headers after stripping punctuation and case, so a UTF-8 BOM or names such as `air temperature k` still load.

### View `v_ai4i_labeled`

Derived columns only; source values are never rewritten.

- `temp_diff_k` = process − air
- `power_w` = torque × rpm × 2π / 60
- `strain_minnm` = tool wear × torque
- `mode_count` = TWF+HDF+PWF+OSF+RNF
- `label_consistency`: `consistent_failure` / `consistent_no_failure` / `failure_without_mode` / `mode_without_failure`
- `hdf_rule`, `pwf_rule`, `osf_rule`: documented failure-mode rules, used to *check* labels, never to alter them

On the published file: 9 records have Machine failure = 1 with no mode flag, 18 have RNF = 1 with Machine failure = 0. They are flagged and kept.

## Isolation

- Azure loader never reads or writes `ai4i_*`.
- AI4I loader never reads or writes `azure_*`.
- Reloading one dataset (including `--force`) leaves the other table fingerprints unchanged (row-level SHA-256).
- SQL files under `sql/azure/` contain no `ai4i` identifiers; SQL files under `sql/ai4i/` contain no `azure` table names.
