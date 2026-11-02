# SQL techniques used

Analytics run in SQLite. Python only binds parameters, formats optional `IN (...)` / `BETWEEN` fragments from an allow-list of column names, and displays results. Named queries live in `sql/*/analytics.sql`, `risk.sql`, `maintenance.sql` (`-- name:` sections parsed by `equipment.db.load_named_queries`).

Filter values are **never interpolated into SQL**. `{machine_filter}`, `{component_filter}` and `{where}` expand to bound placeholders (`:mf0`, `:lo_tool_wear`, …).

## Joins (aggregate first)

Azure event tables are aggregated per machine first (CTEs in the named queries, and the same pattern in the reusable `v_azure_*` views), then LEFT JOINed to `azure_machines`. Failures × errors × maintenance therefore cannot multiply rows. Tests assert that the joined summary has exactly one row per machine.

The maintenance/failure split is a 1:0..1 LEFT JOIN on `(machine_id, ts, component)` (those triples are unique on both sides).

AI4I mode co-occurrence is a self-join of an unpivoted mode list on `udi` with `mode_a < mode_b`, so each pair is counted once.

There is **no join between Azure and AI4I**.

## CTEs

Almost every named query uses `WITH`. Typical pattern: `base` (apply filters) → aggregate → window → `SELECT`.

The Azure failure-trend query uses a **recursive CTE** to build a gap-free month calendar from `:start` to `:end_excl`, then LEFT JOINs actual monthly counts so months with zero failures still appear.

## Window functions

| Function | Where |
|---|---|
| `LAG` | hours between successive failures/errors; month-on-month change; bin-to-bin failure-rate change |
| `ROW_NUMBER` | overall group ranking (AI4I type × wear band) |
| `RANK` | fleet-wide risk rank (computed **before** the machine filter); rate rank within type |
| `NTILE(10)` | AI4I risk-score deciles |
| `SUM() OVER (ORDER BY …)` | cumulative failures |
| `AVG() OVER (ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)` | 3-month rolling average |
| `MIN/MAX() OVER ()` | fleet min/max used to min-max scale Azure risk factors |
| `COUNT(*) OVER (PARTITION BY risk_score)` | average rank for ties (AUC) |

AI4I window functions run over **ordered operating-condition bins**, never over adjacent UDIs. UDI is not a time stamp.

## Subqueries

`errors_before_failures` uses a correlated `EXISTS` (`azure_errors` of the same machine in `[failure_ts − H, failure_ts]`). A uniform-rate reference (errors expected in a random window of length H) is shown beside it so the percentage is not over-claimed.

AI4I risk evaluation queries embed the scoring SELECT via a `{risk}` placeholder replaced in Python *after* scoring, so labels cannot leak into the score.

## Conditional aggregation

`SUM(CASE WHEN … THEN 1 END)` builds:

- Azure failure/error matrices (machine × component/type)
- planned vs failure-linked maintenance
- AI4I failure rates by type inside each tool-wear band (`records_L/M/H`, `failure_rate_L/M/H_pct`)
- AI4I rule-agreement confusion counts

## Set operations

`UNION ALL` unpivots:

- Azure machine timeline (failures / errors / maintenance as event types)
- AI4I condition variables into a long table for Pearson correlation
- AI4I failure-mode flags into a long table for independent counts

## Scaling, ranking, running totals

Azure risk factors are min-max scaled across **all** machines (`(x − MIN(x) OVER ()) / NULLIF(MAX − MIN, 0)`). Failure and error recency are a linear decay over a configurable lookback, already on a 0–1 scale and not min-max scaled. Time since maintenance is min-max scaled days. Telemetry is the share of recent readings outside fleet mean ± k·std.

AI4I factors are each clipped to `[0, 1]` against documented reference limits, then weighted.

`RANK() OVER (ORDER BY risk_score DESC)` is computed in a CTE **before** `WHERE machine_id IN (...)`, so filtering a machine does not change its fleet rank.

## Parameterised queries and `EXPLAIN QUERY PLAN`

`equipment.db.in_clause` builds `AND col IN (:p0, :p1, …)` from bound parameters. Module 01 shows `EXPLAIN QUERY PLAN` for a representative telemetry range scan; `azure_telemetry` is `WITHOUT ROWID` so `(machine_id, ts BETWEEN …)` is a clustered index range.

## Idempotent loads

Each loader:

1. SHA-256 of the source file + row count vs last successful `data_load_log` row → `up_to_date` (no rewrite).
2. Otherwise `BEGIN IMMEDIATE` → `DELETE` that dataset's table(s) → `INSERT` → `COMMIT`.
3. On error: `ROLLBACK`, then log `status='failed'` **outside** the rolled-back transaction. Previous data is unchanged.

`--force` rewrites even when the hash matches; it does not duplicate rows.
