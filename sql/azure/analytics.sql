-- Azure analytics queries. Parsed by equipment.db.load_named_queries().
-- Conventions
--   :start / :end_excl   date range, [start, end_excl) on the text timestamp
--   {machine_filter}     expands to "AND machine_id IN (:mf0, :mf1, ...)" (bound parameters, never values)
--   {component_filter}   expands to "AND component IN (:cf0, ...)"
-- Event tables are always aggregated to the target grain BEFORE they are joined (no fan-out).

-- name: data_bounds
SELECT MIN(ts) AS min_ts, MAX(ts) AS max_ts FROM (
    SELECT MIN(ts) AS ts FROM azure_telemetry UNION ALL SELECT MAX(ts) FROM azure_telemetry
    UNION ALL SELECT MIN(ts) FROM azure_failures    UNION ALL SELECT MAX(ts) FROM azure_failures
    UNION ALL SELECT MIN(ts) FROM azure_errors      UNION ALL SELECT MAX(ts) FROM azure_errors
    UNION ALL SELECT MIN(ts) FROM azure_maintenance UNION ALL SELECT MAX(ts) FROM azure_maintenance
);

-- name: machine_list
SELECT machine_id, model, age FROM azure_machines ORDER BY machine_id;

-- name: component_list
SELECT component FROM (SELECT component FROM azure_failures UNION SELECT component FROM azure_maintenance)
ORDER BY component;

-- name: kpis
SELECT
  (SELECT COUNT(*) FROM azure_machines WHERE 1 = 1 {machine_filter})                         AS machines,
  (SELECT COUNT(*) FROM azure_telemetry
     WHERE ts >= :start AND ts < :end_excl {machine_filter})                                 AS telemetry_records,
  (SELECT COUNT(*) FROM azure_failures
     WHERE ts >= :start AND ts < :end_excl {machine_filter} {component_filter})              AS failures,
  (SELECT COUNT(*) FROM azure_errors
     WHERE ts >= :start AND ts < :end_excl {machine_filter})                                 AS errors,
  (SELECT COUNT(*) FROM azure_maintenance
     WHERE ts >= :start AND ts < :end_excl {machine_filter} {component_filter})              AS maintenance_events;

-- name: failures_by_machine
WITH f AS (
    SELECT machine_id, COUNT(*) AS failures, COUNT(DISTINCT component) AS components_failed,
           MIN(ts) AS first_failure, MAX(ts) AS last_failure
    FROM azure_failures
    WHERE ts >= :start AND ts < :end_excl {component_filter}
    GROUP BY machine_id
)
SELECT m.machine_id, m.model, m.age,
       COALESCE(f.failures, 0)          AS failures,
       COALESCE(f.components_failed, 0) AS components_failed,
       f.first_failure, f.last_failure,
       RANK() OVER (ORDER BY COALESCE(f.failures, 0) DESC) AS failure_rank
FROM azure_machines m
LEFT JOIN f USING (machine_id)
WHERE 1 = 1 {machine_filter}
ORDER BY failures DESC, machine_id;

-- name: failures_by_component
SELECT component,
       COUNT(*)                                        AS failures,
       COUNT(DISTINCT machine_id)                      AS machines_affected,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS share_pct
FROM azure_failures
WHERE ts >= :start AND ts < :end_excl {machine_filter} {component_filter}
GROUP BY component
ORDER BY failures DESC;

-- Conditional aggregation: one pass, one column per component.
-- name: failure_matrix
SELECT machine_id,
       SUM(CASE WHEN component = 'comp1' THEN 1 ELSE 0 END) AS comp1,
       SUM(CASE WHEN component = 'comp2' THEN 1 ELSE 0 END) AS comp2,
       SUM(CASE WHEN component = 'comp3' THEN 1 ELSE 0 END) AS comp3,
       SUM(CASE WHEN component = 'comp4' THEN 1 ELSE 0 END) AS comp4,
       COUNT(*) AS total
FROM azure_failures
WHERE ts >= :start AND ts < :end_excl {machine_filter} {component_filter}
GROUP BY machine_id
ORDER BY total DESC, machine_id;

-- name: errors_by_type
SELECT error_type,
       COUNT(*)                   AS errors,
       COUNT(DISTINCT machine_id) AS machines_affected,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS share_pct
FROM azure_errors
WHERE ts >= :start AND ts < :end_excl {machine_filter}
GROUP BY error_type
ORDER BY errors DESC;

-- name: errors_by_machine
SELECT machine_id,
       COUNT(*)                    AS errors,
       COUNT(DISTINCT error_type)  AS error_types,
       MAX(ts)                     AS last_error,
       RANK() OVER (ORDER BY COUNT(*) DESC) AS error_rank
FROM azure_errors
WHERE ts >= :start AND ts < :end_excl {machine_filter}
GROUP BY machine_id
ORDER BY errors DESC, machine_id;

-- name: error_matrix
SELECT machine_id,
       SUM(CASE WHEN error_type = 'error1' THEN 1 ELSE 0 END) AS error1,
       SUM(CASE WHEN error_type = 'error2' THEN 1 ELSE 0 END) AS error2,
       SUM(CASE WHEN error_type = 'error3' THEN 1 ELSE 0 END) AS error3,
       SUM(CASE WHEN error_type = 'error4' THEN 1 ELSE 0 END) AS error4,
       SUM(CASE WHEN error_type = 'error5' THEN 1 ELSE 0 END) AS error5,
       COUNT(*) AS total
FROM azure_errors
WHERE ts >= :start AND ts < :end_excl {machine_filter}
GROUP BY machine_id
ORDER BY total DESC, machine_id;

-- Repeated failures: LAG() gives the gap to the previous failure of the same component on the same machine.
-- name: repeated_failures
WITH g AS (
    SELECT machine_id, component, ts,
           julianday(ts) - julianday(LAG(ts) OVER w) AS days_since_prev
    FROM azure_failures
    WHERE ts >= :start AND ts < :end_excl {machine_filter} {component_filter}
    WINDOW w AS (PARTITION BY machine_id, component ORDER BY ts)
)
SELECT machine_id, component,
       COUNT(*)                          AS failures,
       ROUND(MIN(days_since_prev), 1)    AS min_gap_days,
       ROUND(AVG(days_since_prev), 1)    AS avg_gap_days,
       MAX(ts)                           AS last_failure
FROM g
GROUP BY machine_id, component
HAVING COUNT(*) >= 2
ORDER BY failures DESC, avg_gap_days, machine_id;

-- Repeated errors: same error type on the same machine again within :repeat_hours.
-- name: repeated_errors
WITH g AS (
    SELECT machine_id, error_type, ts,
           (julianday(ts) - julianday(LAG(ts) OVER w)) * 24 AS hours_since_prev
    FROM azure_errors
    WHERE ts >= :start AND ts < :end_excl {machine_filter}
    WINDOW w AS (PARTITION BY machine_id, error_type ORDER BY ts)
)
SELECT machine_id, error_type,
       COUNT(*)                                                              AS errors,
       SUM(CASE WHEN hours_since_prev <= :repeat_hours THEN 1 ELSE 0 END)    AS repeats_within_window,
       ROUND(MIN(hours_since_prev), 1)                                       AS shortest_gap_hours
FROM g
GROUP BY machine_id, error_type
HAVING SUM(CASE WHEN hours_since_prev <= :repeat_hours THEN 1 ELSE 0 END) > 0
ORDER BY repeats_within_window DESC, machine_id;

-- Recursive CTE builds a gap-free month calendar; window functions add change, running total, rolling mean.
-- name: failure_trend
WITH RECURSIVE months(month_start) AS (
    SELECT date(:start, 'start of month')
    UNION ALL
    SELECT date(month_start, '+1 month') FROM months
    WHERE month_start < date(:end_excl, '-1 day', 'start of month')
),
monthly AS (
    SELECT strftime('%Y-%m', ts) AS month, COUNT(*) AS failures
    FROM azure_failures
    WHERE ts >= :start AND ts < :end_excl {machine_filter} {component_filter}
    GROUP BY 1
)
SELECT strftime('%Y-%m', mo.month_start)                                    AS month,
       COALESCE(mn.failures, 0)                                             AS failures,
       COALESCE(mn.failures, 0) - LAG(COALESCE(mn.failures, 0)) OVER w_all  AS mom_change,
       SUM(COALESCE(mn.failures, 0)) OVER w_all_cum                         AS cumulative_failures,
       ROUND(AVG(COALESCE(mn.failures, 0)) OVER w_roll, 2)                  AS rolling_3m_avg
FROM months mo
LEFT JOIN monthly mn ON mn.month = strftime('%Y-%m', mo.month_start)
WINDOW w_all     AS (ORDER BY mo.month_start),
       w_all_cum AS (ORDER BY mo.month_start ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),
       w_roll    AS (ORDER BY mo.month_start ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)
ORDER BY mo.month_start;

-- name: failure_trend_by_component
SELECT strftime('%Y-%m', ts) AS month, component, COUNT(*) AS failures
FROM azure_failures
WHERE ts >= :start AND ts < :end_excl {machine_filter} {component_filter}
GROUP BY month, component
ORDER BY month, component;

-- Machines that are above the fleet average for BOTH failures and errors.
-- Thresholds are computed over ALL machines (CTE `thr`), the machine filter only narrows the output.
-- name: frequent_both
WITH f AS (
    SELECT machine_id, COUNT(*) AS failures FROM azure_failures
    WHERE ts >= :start AND ts < :end_excl {component_filter} GROUP BY machine_id
),
e AS (
    SELECT machine_id, COUNT(*) AS errors FROM azure_errors
    WHERE ts >= :start AND ts < :end_excl GROUP BY machine_id
),
base AS (
    SELECT m.machine_id, m.model, m.age,
           COALESCE(f.failures, 0) AS failures, COALESCE(e.errors, 0) AS errors
    FROM azure_machines m
    LEFT JOIN f USING (machine_id)
    LEFT JOIN e USING (machine_id)
),
thr AS (SELECT AVG(failures) AS avg_failures, AVG(errors) AS avg_errors FROM base)
SELECT b.machine_id, b.model, b.age, b.failures, b.errors,
       ROUND(thr.avg_failures, 2) AS fleet_avg_failures,
       ROUND(thr.avg_errors, 2)   AS fleet_avg_errors,
       CASE WHEN b.failures > thr.avg_failures AND b.errors > thr.avg_errors THEN 1 ELSE 0 END AS both_above_average,
       RANK() OVER (ORDER BY b.failures DESC) + RANK() OVER (ORDER BY b.errors DESC)           AS rank_sum
FROM base b CROSS JOIN thr
WHERE 1 = 1 {machine_filter}
ORDER BY both_above_average DESC, rank_sum, machine_id;

-- name: model_comparison
WITH f AS (
    SELECT machine_id, COUNT(*) AS n FROM azure_failures
    WHERE ts >= :start AND ts < :end_excl {component_filter} GROUP BY machine_id
),
e AS (
    SELECT machine_id, COUNT(*) AS n FROM azure_errors
    WHERE ts >= :start AND ts < :end_excl GROUP BY machine_id
),
base AS (
    SELECT m.machine_id, m.model, m.age, COALESCE(f.n, 0) AS failures, COALESCE(e.n, 0) AS errors
    FROM azure_machines m LEFT JOIN f USING (machine_id) LEFT JOIN e USING (machine_id)
    WHERE 1 = 1 {machine_filter}
)
SELECT model,
       COUNT(*)                                   AS machines,
       ROUND(AVG(age), 1)                         AS avg_age,
       SUM(failures)                              AS failures,
       ROUND(1.0 * SUM(failures) / COUNT(*), 2)   AS failures_per_machine,
       SUM(errors)                                AS errors,
       ROUND(1.0 * SUM(errors) / COUNT(*), 2)     AS errors_per_machine
FROM base
GROUP BY model
ORDER BY model;

-- name: age_band_comparison
WITH f AS (
    SELECT machine_id, COUNT(*) AS n FROM azure_failures
    WHERE ts >= :start AND ts < :end_excl {component_filter} GROUP BY machine_id
),
e AS (
    SELECT machine_id, COUNT(*) AS n FROM azure_errors
    WHERE ts >= :start AND ts < :end_excl GROUP BY machine_id
),
base AS (
    SELECT m.machine_id, m.age, COALESCE(f.n, 0) AS failures, COALESCE(e.n, 0) AS errors,
           CASE WHEN m.age < 5 THEN '0-4 yrs' WHEN m.age < 10 THEN '5-9 yrs'
                WHEN m.age < 15 THEN '10-14 yrs' ELSE '15+ yrs' END AS age_band,
           CASE WHEN m.age < 5 THEN 1 WHEN m.age < 10 THEN 2 WHEN m.age < 15 THEN 3 ELSE 4 END AS band_order
    FROM azure_machines m LEFT JOIN f USING (machine_id) LEFT JOIN e USING (machine_id)
    WHERE 1 = 1 {machine_filter}
)
SELECT age_band, COUNT(*) AS machines,
       SUM(failures) AS failures, ROUND(1.0 * SUM(failures) / COUNT(*), 2) AS failures_per_machine,
       SUM(errors)   AS errors,   ROUND(1.0 * SUM(errors) / COUNT(*), 2)   AS errors_per_machine
FROM base
GROUP BY age_band, band_order
ORDER BY band_order;

-- Pearson correlation computed in SQL: cov(x,y) / (sd(x) * sd(y)).
-- name: age_correlation
WITH f AS (
    SELECT machine_id, COUNT(*) AS n FROM azure_failures
    WHERE ts >= :start AND ts < :end_excl {component_filter} GROUP BY machine_id
),
e AS (
    SELECT machine_id, COUNT(*) AS n FROM azure_errors
    WHERE ts >= :start AND ts < :end_excl GROUP BY machine_id
),
base AS (
    SELECT m.age AS x, COALESCE(f.n, 0) AS yf, COALESCE(e.n, 0) AS ye
    FROM azure_machines m LEFT JOIN f USING (machine_id) LEFT JOIN e USING (machine_id)
    WHERE 1 = 1 {machine_filter}
)
SELECT COUNT(*) AS machines,
       (AVG(x * yf) - AVG(x) * AVG(yf))
         / NULLIF(sqrt(AVG(x * x) - AVG(x) * AVG(x)) * sqrt(AVG(yf * yf) - AVG(yf) * AVG(yf)), 0) AS corr_age_failures,
       (AVG(x * ye) - AVG(x) * AVG(ye))
         / NULLIF(sqrt(AVG(x * x) - AVG(x) * AVG(x)) * sqrt(AVG(ye * ye) - AVG(ye) * AVG(ye)), 0) AS corr_age_errors
FROM base;

-- Correlated subquery: was the same machine logging an error shortly before each failure?
-- (descriptive only - no baseline for "errors in any random window" is implied)
-- name: errors_before_failures
SELECT f.component,
       COUNT(*) AS failures,
       SUM(CASE WHEN EXISTS (
               SELECT 1 FROM azure_errors e
               WHERE e.machine_id = f.machine_id
                 AND e.ts <= f.ts
                 AND e.ts >= datetime(f.ts, '-' || :hours || ' hours')
           ) THEN 1 ELSE 0 END) AS preceded_by_error,
       ROUND(100.0 * SUM(CASE WHEN EXISTS (
               SELECT 1 FROM azure_errors e
               WHERE e.machine_id = f.machine_id
                 AND e.ts <= f.ts
                 AND e.ts >= datetime(f.ts, '-' || :hours || ' hours')
           ) THEN 1 ELSE 0 END) / COUNT(*), 1) AS preceded_pct,
       -- reference point: errors expected inside one window if each machine's errors were spread uniformly in time
       ROUND(:hours * (SELECT COUNT(*) FROM azure_errors WHERE ts >= :start AND ts < :end_excl)
             / ((julianday(:end_excl) - julianday(:start)) * 24.0
                * (SELECT COUNT(*) FROM azure_machines)), 2) AS uniform_expected_errors_per_window
FROM azure_failures f
WHERE f.ts >= :start AND f.ts < :end_excl {machine_filter} {component_filter}
GROUP BY f.component
ORDER BY f.component;

-- name: maintenance_by_component
SELECT x.component,
       COUNT(*)                                             AS events,
       SUM(CASE WHEN f.event_id IS NULL THEN 1 ELSE 0 END)  AS planned,
       SUM(CASE WHEN f.event_id IS NULL THEN 0 ELSE 1 END)  AS failure_linked,
       COUNT(DISTINCT x.machine_id)                         AS machines
FROM azure_maintenance x
LEFT JOIN azure_failures f ON f.machine_id = x.machine_id AND f.ts = x.ts AND f.component = x.component
WHERE x.ts >= :start AND x.ts < :end_excl {machine_filter} {component_filter}
GROUP BY x.component
ORDER BY x.component;

-- name: machine_timeline
SELECT ts, 'failure' AS event_type, component AS detail FROM azure_failures
 WHERE machine_id = :machine_id AND ts >= :start AND ts < :end_excl
UNION ALL
SELECT ts, 'error', error_type FROM azure_errors
 WHERE machine_id = :machine_id AND ts >= :start AND ts < :end_excl
UNION ALL
SELECT ts, 'maintenance', component FROM azure_maintenance
 WHERE machine_id = :machine_id AND ts >= :start AND ts < :end_excl
ORDER BY ts, event_type;

-- name: machine_telemetry_daily
SELECT date(ts) AS day, COUNT(*) AS readings,
       ROUND(AVG(volt), 2) AS volt, ROUND(AVG(rotate), 2) AS rotate,
       ROUND(AVG(pressure), 2) AS pressure, ROUND(AVG(vibration), 2) AS vibration
FROM azure_telemetry
WHERE machine_id = :machine_id AND ts >= :start AND ts < :end_excl
GROUP BY date(ts)
ORDER BY day;

-- name: table_counts
SELECT 'azure_machines' AS table_name, COUNT(*) AS row_count FROM azure_machines
UNION ALL SELECT 'azure_telemetry',   COUNT(*) FROM azure_telemetry
UNION ALL SELECT 'azure_errors',      COUNT(*) FROM azure_errors
UNION ALL SELECT 'azure_maintenance', COUNT(*) FROM azure_maintenance
UNION ALL SELECT 'azure_failures',    COUNT(*) FROM azure_failures;
