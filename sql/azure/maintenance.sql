-- Module 3: observed telemetry before vs after maintenance.
--   before window = [t - W, t)      after window = (t, t + W]      (the reading at t itself is excluded)
-- Parameters: :machine_id :ts (maintenance time) :hours (window length W in hours)

-- name: maintenance_events
SELECT x.event_id, x.ts, x.component,
       CASE WHEN f.event_id IS NULL THEN 'planned' ELSE 'failure-linked' END AS kind
FROM azure_maintenance x
LEFT JOIN azure_failures f ON f.machine_id = x.machine_id AND f.ts = x.ts AND f.component = x.component
WHERE x.machine_id = :machine_id
ORDER BY x.ts DESC;

-- Unpivot the four sensors, then compute count/mean/min/max/std in one pass and the median with ROW_NUMBER().
-- name: before_after_stats
WITH win AS (
    SELECT 'before' AS period, volt, rotate, pressure, vibration FROM azure_telemetry
    WHERE machine_id = :machine_id AND ts >= datetime(:ts, '-' || :hours || ' hours') AND ts < :ts
    UNION ALL
    SELECT 'after', volt, rotate, pressure, vibration FROM azure_telemetry
    WHERE machine_id = :machine_id AND ts > :ts AND ts <= datetime(:ts, '+' || :hours || ' hours')
),
long AS (
    SELECT period, 'volt' AS metric, volt AS val FROM win
    UNION ALL SELECT period, 'rotate',    rotate    FROM win
    UNION ALL SELECT period, 'pressure',  pressure  FROM win
    UNION ALL SELECT period, 'vibration', vibration FROM win
),
ranked AS (
    SELECT period, metric, val,
           ROW_NUMBER() OVER (PARTITION BY period, metric ORDER BY val) AS rn,
           COUNT(*)     OVER (PARTITION BY period, metric)              AS n
    FROM long WHERE val IS NOT NULL
),
med AS (
    SELECT period, metric, AVG(val) AS median
    FROM ranked WHERE rn IN ((n + 1) / 2, (n + 2) / 2)
    GROUP BY period, metric
),
agg AS (
    SELECT period, metric, COUNT(val) AS n_obs, AVG(val) AS mean, MIN(val) AS min, MAX(val) AS max,
           CASE WHEN COUNT(val) > 1
                THEN sqrt(MAX((SUM(val * val) - SUM(val) * SUM(val) / COUNT(val)) / (COUNT(val) - 1), 0)) END AS std
    FROM long GROUP BY period, metric
)
SELECT agg.period, agg.metric, agg.n_obs, agg.mean, med.median, agg.min, agg.max, agg.std
FROM agg LEFT JOIN med USING (period, metric)
ORDER BY agg.metric, agg.period DESC;

-- Other events inside the comparison windows (potential confounders of any observed change).
-- Events at exactly :ts belong to neither window, so they get their own period. Only the selected
-- machine + timestamp + component row is left out: other components serviced at :ts are confounders.
-- name: window_confounders
SELECT period, event_type, COUNT(*) AS events FROM (
    SELECT CASE WHEN ts < :ts THEN 'before' WHEN ts > :ts THEN 'after' ELSE 'at event' END AS period,
           'other maintenance' AS event_type
      FROM azure_maintenance
     WHERE machine_id = :machine_id AND NOT (ts = :ts AND component = COALESCE(:component, component))
       AND ts >= datetime(:ts, '-' || :hours || ' hours') AND ts <= datetime(:ts, '+' || :hours || ' hours')
    UNION ALL
    SELECT CASE WHEN ts < :ts THEN 'before' WHEN ts > :ts THEN 'after' ELSE 'at event' END, 'failure' FROM azure_failures
     WHERE machine_id = :machine_id
       AND ts >= datetime(:ts, '-' || :hours || ' hours') AND ts <= datetime(:ts, '+' || :hours || ' hours')
    UNION ALL
    SELECT CASE WHEN ts < :ts THEN 'before' WHEN ts > :ts THEN 'after' ELSE 'at event' END, 'error' FROM azure_errors
     WHERE machine_id = :machine_id
       AND ts >= datetime(:ts, '-' || :hours || ' hours') AND ts <= datetime(:ts, '+' || :hours || ' hours')
)
GROUP BY period, event_type
ORDER BY event_type, period;

-- name: window_readings
SELECT ts, volt, rotate, pressure, vibration,
       CASE WHEN ts < :ts THEN 'before' ELSE 'after' END AS period
FROM azure_telemetry
WHERE machine_id = :machine_id
  AND ts >= datetime(:ts, '-' || :hours || ' hours') AND ts <= datetime(:ts, '+' || :hours || ' hours')
  AND ts <> :ts
ORDER BY ts;

-- Fleet-wide view by component. Before and after windows are aggregated in SEPARATE CTEs and then
-- joined 1:1 per event; joining both windows to the event directly would multiply rows (n_before x n_after).
-- name: component_effect_summary
WITH ev AS (
    SELECT event_id, machine_id, ts, component FROM azure_maintenance
),
b AS (
    SELECT ev.event_id, COUNT(*) AS n,
           AVG(t.volt) AS volt, AVG(t.rotate) AS rotate, AVG(t.pressure) AS pressure, AVG(t.vibration) AS vibration
    FROM ev JOIN azure_telemetry t
      ON t.machine_id = ev.machine_id AND t.ts >= datetime(ev.ts, '-' || :hours || ' hours') AND t.ts < ev.ts
    GROUP BY ev.event_id
),
a AS (
    SELECT ev.event_id, COUNT(*) AS n,
           AVG(t.volt) AS volt, AVG(t.rotate) AS rotate, AVG(t.pressure) AS pressure, AVG(t.vibration) AS vibration
    FROM ev JOIN azure_telemetry t
      ON t.machine_id = ev.machine_id AND t.ts > ev.ts AND t.ts <= datetime(ev.ts, '+' || :hours || ' hours')
    GROUP BY ev.event_id
),
paired AS (
    SELECT ev.component, ev.event_id,
           a.volt - b.volt AS d_volt, a.rotate - b.rotate AS d_rotate,
           a.pressure - b.pressure AS d_pressure, a.vibration - b.vibration AS d_vibration
    FROM ev JOIN b ON b.event_id = ev.event_id JOIN a ON a.event_id = ev.event_id
    WHERE b.n >= :min_obs AND a.n >= :min_obs
)
SELECT ev_tot.component,
       ev_tot.events                                  AS maintenance_events,
       COUNT(p.event_id)                              AS events_with_both_windows,
       ROUND(AVG(p.d_volt), 3)      AS avg_change_volt,
       ROUND(AVG(p.d_rotate), 3)    AS avg_change_rotate,
       ROUND(AVG(p.d_pressure), 3)  AS avg_change_pressure,
       ROUND(AVG(p.d_vibration), 3) AS avg_change_vibration
FROM (SELECT component, COUNT(*) AS events FROM ev GROUP BY component) ev_tot
LEFT JOIN paired p ON p.component = ev_tot.component
GROUP BY ev_tot.component, ev_tot.events
ORDER BY ev_tot.component;
