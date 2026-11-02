-- Explainable machine risk score (Azure). See docs/risk_methodology.md.
--
-- Bound parameters
--   :start, :end_excl                  evidence window [start, end_excl); end_excl is also the "as-of" instant
--   :lookback                          days over which failure/error recency decays linearly to 0
--   :tele_days, :k                     telemetry window (days before as-of) and sigma multiplier
--   :w_<component>                     eight relative weights
--   :t_medium, :t_high, :t_critical    category thresholds (score >= threshold)
--
-- Pipeline (each step is a CTE):
--   f, e, mt, t   per-machine aggregates, ONE row per machine (no join fan-out)
--   raw           machines LEFT JOIN aggregates, missing events -> 0, missing telemetry -> NULL
--   norm          every component scaled to 0..1 (min-max across ALL machines via MIN/MAX() OVER ())
--   scored        weights renormalised over components that have data; point contributions
--   final         risk score = sum of contributions (so the components explain the score exactly)

-- name: risk_scores
WITH
f AS (
    SELECT machine_id, COUNT(*) AS failure_count, MAX(ts) AS last_failure
    FROM azure_failures WHERE ts >= :start AND ts < :end_excl GROUP BY machine_id
),
e AS (
    SELECT machine_id, COUNT(*) AS error_count, MAX(ts) AS last_error
    FROM azure_errors WHERE ts >= :start AND ts < :end_excl GROUP BY machine_id
),
-- planned maintenance = maintenance that is NOT the repair of a recorded failure (avoids double counting failures)
mt AS (
    SELECT x.machine_id,
           COUNT(*) AS maintenance_count,
           SUM(CASE WHEN fl.event_id IS NULL THEN 1 ELSE 0 END) AS planned_maintenance_count,
           MAX(x.ts) AS last_maintenance
    FROM azure_maintenance x
    LEFT JOIN azure_failures fl
           ON fl.machine_id = x.machine_id AND fl.ts = x.ts AND fl.component = x.component
    WHERE x.ts >= :start AND x.ts < :end_excl
    GROUP BY x.machine_id
),
-- fleet baseline from readings before the as-of instant only, so an earlier score never sees later data
b AS (
    SELECT AVG(volt) AS volt_mean,           sqrt(MAX(AVG(volt*volt) - AVG(volt)*AVG(volt), 0))                     AS volt_std,
           AVG(rotate) AS rotate_mean,       sqrt(MAX(AVG(rotate*rotate) - AVG(rotate)*AVG(rotate), 0))             AS rotate_std,
           AVG(pressure) AS pressure_mean,   sqrt(MAX(AVG(pressure*pressure) - AVG(pressure)*AVG(pressure), 0))     AS pressure_std,
           AVG(vibration) AS vibration_mean, sqrt(MAX(AVG(vibration*vibration) - AVG(vibration)*AVG(vibration), 0)) AS vibration_std
    FROM azure_telemetry
    WHERE ts < :end_excl
),
-- share of recent readings outside the fleet baseline band (mean +/- k*std), per sensor
t AS (
    SELECT tl.machine_id, COUNT(*) AS tele_obs,
           AVG(tl.volt) AS avg_volt, AVG(tl.rotate) AS avg_rotate,
           AVG(tl.pressure) AS avg_pressure, AVG(tl.vibration) AS avg_vibration,
           AVG(CASE WHEN tl.volt IS NULL THEN NULL
                    WHEN ABS(tl.volt - b.volt_mean) > :k * b.volt_std THEN 1.0 ELSE 0.0 END) AS volt_exceed,
           AVG(CASE WHEN tl.rotate IS NULL THEN NULL
                    WHEN ABS(tl.rotate - b.rotate_mean) > :k * b.rotate_std THEN 1.0 ELSE 0.0 END) AS rotate_exceed,
           AVG(CASE WHEN tl.pressure IS NULL THEN NULL
                    WHEN ABS(tl.pressure - b.pressure_mean) > :k * b.pressure_std THEN 1.0 ELSE 0.0 END) AS pressure_exceed,
           AVG(CASE WHEN tl.vibration IS NULL THEN NULL
                    WHEN ABS(tl.vibration - b.vibration_mean) > :k * b.vibration_std THEN 1.0 ELSE 0.0 END) AS vibration_exceed
    FROM azure_telemetry tl CROSS JOIN b
    WHERE tl.ts >= MAX(:start, datetime(:end_excl, '-' || :tele_days || ' days')) AND tl.ts < :end_excl
    GROUP BY tl.machine_id
),
raw AS (
    SELECT m.machine_id, m.model, m.age,
           COALESCE(f.failure_count, 0)             AS failure_count,  f.last_failure,
           COALESCE(e.error_count, 0)               AS error_count,    e.last_error,
           COALESCE(mt.maintenance_count, 0)        AS maintenance_count,
           COALESCE(mt.planned_maintenance_count, 0) AS planned_maintenance_count,
           mt.last_maintenance,
           t.tele_obs, t.avg_volt, t.avg_rotate, t.avg_pressure, t.avg_vibration,
           CASE WHEN t.tele_obs IS NULL THEN NULL
                WHEN (t.volt_exceed IS NULL AND t.rotate_exceed IS NULL
                      AND t.pressure_exceed IS NULL AND t.vibration_exceed IS NULL) THEN NULL
                ELSE (COALESCE(t.volt_exceed, 0) + COALESCE(t.rotate_exceed, 0)
                      + COALESCE(t.pressure_exceed, 0) + COALESCE(t.vibration_exceed, 0))
                     / ((t.volt_exceed IS NOT NULL) + (t.rotate_exceed IS NOT NULL)
                        + (t.pressure_exceed IS NOT NULL) + (t.vibration_exceed IS NOT NULL))
           END AS tele_exceed_rate,
           CASE WHEN f.last_failure IS NULL THEN 0.0
                ELSE MAX(0.0, 1.0 - (julianday(:end_excl) - julianday(f.last_failure)) / :lookback) END AS fail_rec_raw,
           CASE WHEN e.last_error IS NULL THEN 0.0
                ELSE MAX(0.0, 1.0 - (julianday(:end_excl) - julianday(e.last_error)) / :lookback) END   AS err_rec_raw,
           julianday(:end_excl) - julianday(COALESCE(mt.last_maintenance, :start)) AS days_since_maint,
           ROUND(julianday(:end_excl) - julianday(f.last_failure), 1)  AS days_since_failure,
           ROUND(julianday(:end_excl) - julianday(e.last_error), 1)    AS days_since_error
    FROM azure_machines m
    LEFT JOIN f  ON f.machine_id  = m.machine_id
    LEFT JOIN e  ON e.machine_id  = m.machine_id
    LEFT JOIN mt ON mt.machine_id = m.machine_id
    LEFT JOIN t  ON t.machine_id  = m.machine_id
),
norm AS (
    SELECT raw.*,
      COALESCE(1.0 * (failure_count - MIN(failure_count) OVER ())
               / NULLIF(MAX(failure_count) OVER () - MIN(failure_count) OVER (), 0), 0.0)          AS n_fail_freq,
      fail_rec_raw                                                                                  AS n_fail_rec,
      COALESCE(1.0 * (error_count - MIN(error_count) OVER ())
               / NULLIF(MAX(error_count) OVER () - MIN(error_count) OVER (), 0), 0.0)              AS n_err_freq,
      err_rec_raw                                                                                   AS n_err_rec,
      CASE WHEN tele_exceed_rate IS NULL THEN NULL
           ELSE COALESCE((tele_exceed_rate - MIN(tele_exceed_rate) OVER ())
                         / NULLIF(MAX(tele_exceed_rate) OVER () - MIN(tele_exceed_rate) OVER (), 0), 0.0)
      END                                                                                           AS n_tele,
      COALESCE(1.0 * (planned_maintenance_count - MIN(planned_maintenance_count) OVER ())
               / NULLIF(MAX(planned_maintenance_count) OVER () - MIN(planned_maintenance_count) OVER (), 0), 0.0)
                                                                                                    AS n_maint_burden,
      COALESCE((days_since_maint - MIN(days_since_maint) OVER ())
               / NULLIF(MAX(days_since_maint) OVER () - MIN(days_since_maint) OVER (), 0), 0.0)    AS n_maint_rec,
      COALESCE(1.0 * (age - MIN(age) OVER ()) / NULLIF(MAX(age) OVER () - MIN(age) OVER (), 0), 0.0) AS n_age
    FROM raw
),
scored AS (
    SELECT norm.*,
      -- weights of components that actually have data for this machine (only telemetry can be missing)
      (:w_fail_freq + :w_fail_rec + :w_err_freq + :w_err_rec + :w_maint_burden + :w_maint_rec + :w_age
        + CASE WHEN n_tele IS NULL THEN 0 ELSE :w_tele END) AS weight_total
    FROM norm
),
final AS (
    SELECT scored.*,
      100.0 * :w_fail_freq    * n_fail_freq    / NULLIF(weight_total, 0) AS c_fail_freq,
      100.0 * :w_fail_rec     * n_fail_rec     / NULLIF(weight_total, 0) AS c_fail_rec,
      100.0 * :w_err_freq     * n_err_freq     / NULLIF(weight_total, 0) AS c_err_freq,
      100.0 * :w_err_rec      * n_err_rec      / NULLIF(weight_total, 0) AS c_err_rec,
      COALESCE(100.0 * :w_tele * n_tele        / NULLIF(weight_total, 0), 0.0) AS c_tele,
      100.0 * :w_maint_burden * n_maint_burden / NULLIF(weight_total, 0) AS c_maint_burden,
      100.0 * :w_maint_rec    * n_maint_rec    / NULLIF(weight_total, 0) AS c_maint_rec,
      100.0 * :w_age          * n_age          / NULLIF(weight_total, 0) AS c_age
    FROM scored
),
ranked AS (
    SELECT final.*,
      ROUND(c_fail_freq + c_fail_rec + c_err_freq + c_err_rec + c_tele + c_maint_burden + c_maint_rec + c_age, 2)
        AS risk_score
    FROM final
),
-- category and fleet-wide rank are computed BEFORE the machine filter, so filtering never changes them
categorised AS (
    SELECT ranked.*,
       CASE WHEN risk_score >= :t_critical THEN 'Critical'
            WHEN risk_score >= :t_high     THEN 'High'
            WHEN risk_score >= :t_medium   THEN 'Medium'
            ELSE 'Low' END                                            AS risk_category,
       RANK() OVER (ORDER BY risk_score DESC)                         AS priority_rank,
       TRIM(CASE WHEN failure_count = 0 THEN 'no failures in range; ' ELSE '' END
         || CASE WHEN error_count = 0 THEN 'no errors in range; ' ELSE '' END
         || CASE WHEN tele_obs IS NULL THEN 'no telemetry in window (telemetry factor excluded, weights renormalised); ' ELSE '' END
         || CASE WHEN last_maintenance IS NULL THEN 'no maintenance in range (staleness = full range); ' ELSE '' END)
                                                                       AS data_flags
    FROM ranked
)
SELECT * FROM categorised
WHERE 1 = 1 {machine_filter}
ORDER BY risk_score DESC, machine_id;

-- Evidence queries: the exact records behind a machine's score.
-- name: ev_failures
SELECT ts, component FROM azure_failures
WHERE machine_id = :machine_id AND ts >= :start AND ts < :end_excl ORDER BY ts DESC;

-- name: ev_errors
SELECT ts, error_type FROM azure_errors
WHERE machine_id = :machine_id AND ts >= :start AND ts < :end_excl ORDER BY ts DESC;

-- name: ev_maintenance
SELECT x.ts, x.component,
       CASE WHEN f.event_id IS NULL THEN 'planned' ELSE 'failure-linked' END AS kind
FROM azure_maintenance x
LEFT JOIN azure_failures f ON f.machine_id = x.machine_id AND f.ts = x.ts AND f.component = x.component
WHERE x.machine_id = :machine_id AND x.ts >= :start AND x.ts < :end_excl
ORDER BY x.ts DESC;

-- Readings in the telemetry window outside the fleet band, largest deviation first.
-- name: ev_telemetry
WITH b AS (
    SELECT AVG(volt) AS volt_mean,           sqrt(MAX(AVG(volt*volt) - AVG(volt)*AVG(volt), 0))                     AS volt_std,
           AVG(rotate) AS rotate_mean,       sqrt(MAX(AVG(rotate*rotate) - AVG(rotate)*AVG(rotate), 0))             AS rotate_std,
           AVG(pressure) AS pressure_mean,   sqrt(MAX(AVG(pressure*pressure) - AVG(pressure)*AVG(pressure), 0))     AS pressure_std,
           AVG(vibration) AS vibration_mean, sqrt(MAX(AVG(vibration*vibration) - AVG(vibration)*AVG(vibration), 0)) AS vibration_std
    FROM azure_telemetry
    WHERE ts < :end_excl
)
SELECT t.ts, t.volt, t.rotate, t.pressure, t.vibration,
       ROUND(ABS(t.volt - b.volt_mean) / b.volt_std, 2)                   AS volt_sigma,
       ROUND(ABS(t.rotate - b.rotate_mean) / b.rotate_std, 2)             AS rotate_sigma,
       ROUND(ABS(t.pressure - b.pressure_mean) / b.pressure_std, 2)       AS pressure_sigma,
       ROUND(ABS(t.vibration - b.vibration_mean) / b.vibration_std, 2)    AS vibration_sigma
FROM azure_telemetry t CROSS JOIN b
WHERE t.machine_id = :machine_id
  AND t.ts >= MAX(:start, datetime(:end_excl, '-' || :tele_days || ' days')) AND t.ts < :end_excl
  AND (ABS(t.volt - b.volt_mean) > :k * b.volt_std OR ABS(t.rotate - b.rotate_mean) > :k * b.rotate_std
       OR ABS(t.pressure - b.pressure_mean) > :k * b.pressure_std
       OR ABS(t.vibration - b.vibration_mean) > :k * b.vibration_std)
ORDER BY MAX(ABS(t.volt - b.volt_mean) / b.volt_std, ABS(t.rotate - b.rotate_mean) / b.rotate_std,
             ABS(t.pressure - b.pressure_mean) / b.pressure_std,
             ABS(t.vibration - b.vibration_mean) / b.vibration_std) DESC
LIMIT 200;
