-- Reusable analytical views for the Azure dataset.
-- Design rule: every event table is aggregated to ONE ROW PER MACHINE *before* it is joined,
-- so failures x errors x maintenance can never multiply rows (join fan-out).

DROP VIEW IF EXISTS v_azure_machine_failures;
CREATE VIEW v_azure_machine_failures AS
SELECT machine_id,
       COUNT(*)                   AS failure_count,
       COUNT(DISTINCT component)  AS components_failed,
       MIN(ts)                    AS first_failure,
       MAX(ts)                    AS last_failure
FROM azure_failures
GROUP BY machine_id;

DROP VIEW IF EXISTS v_azure_machine_errors;
CREATE VIEW v_azure_machine_errors AS
SELECT machine_id,
       COUNT(*)                  AS error_count,
       COUNT(DISTINCT error_type) AS error_types,
       MAX(ts)                   AS last_error
FROM azure_errors
GROUP BY machine_id;

-- A maintenance row that coincides with a failure of the same machine+component+timestamp is a
-- failure-triggered repair. Splitting these from planned maintenance avoids counting a failure twice.
-- azure_failures is UNIQUE(machine_id, ts, component), so this LEFT JOIN is 1:0..1 (no fan-out).
DROP VIEW IF EXISTS v_azure_machine_maintenance;
CREATE VIEW v_azure_machine_maintenance AS
SELECT x.machine_id,
       COUNT(*)                                              AS maintenance_count,
       SUM(CASE WHEN f.event_id IS NULL THEN 1 ELSE 0 END)   AS planned_maintenance_count,
       SUM(CASE WHEN f.event_id IS NULL THEN 0 ELSE 1 END)   AS failure_linked_maintenance_count,
       MAX(x.ts)                                             AS last_maintenance
FROM azure_maintenance x
LEFT JOIN azure_failures f
       ON f.machine_id = x.machine_id AND f.ts = x.ts AND f.component = x.component
GROUP BY x.machine_id;

DROP VIEW IF EXISTS v_azure_machine_summary;
CREATE VIEW v_azure_machine_summary AS
SELECT m.machine_id, m.model, m.age,
       COALESCE(f.failure_count, 0)                       AS failure_count,
       COALESCE(e.error_count, 0)                         AS error_count,
       COALESCE(mt.maintenance_count, 0)                  AS maintenance_count,
       COALESCE(mt.planned_maintenance_count, 0)          AS planned_maintenance_count,
       f.last_failure, e.last_error, mt.last_maintenance
FROM azure_machines m
LEFT JOIN v_azure_machine_failures    f  ON f.machine_id  = m.machine_id
LEFT JOIN v_azure_machine_errors      e  ON e.machine_id  = m.machine_id
LEFT JOIN v_azure_machine_maintenance mt ON mt.machine_id = m.machine_id;

-- Window functions: month-over-month change (LAG), running total and 3-month rolling average.
DROP VIEW IF EXISTS v_azure_failure_monthly;
CREATE VIEW v_azure_failure_monthly AS
WITH monthly AS (
    SELECT strftime('%Y-%m', ts) AS month, COUNT(*) AS failures
    FROM azure_failures GROUP BY 1
)
SELECT month, failures,
       LAG(failures) OVER (ORDER BY month)                                   AS prev_month_failures,
       failures - LAG(failures) OVER (ORDER BY month)                        AS mom_change,
       SUM(failures) OVER (ORDER BY month)                                   AS cumulative_failures,
       ROUND(AVG(failures) OVER (ORDER BY month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2)
                                                                             AS rolling_3m_avg
FROM monthly;

-- Repeat detection: gap since the previous failure of the same component on the same machine.
DROP VIEW IF EXISTS v_azure_failure_gaps;
CREATE VIEW v_azure_failure_gaps AS
SELECT event_id, machine_id, component, ts,
       LAG(ts) OVER w                                                AS prev_ts,
       ROUND(julianday(ts) - julianday(LAG(ts) OVER w), 2)           AS days_since_prev,
       ROW_NUMBER() OVER w                                           AS occurrence_no
FROM azure_failures
WINDOW w AS (PARTITION BY machine_id, component ORDER BY ts);

DROP VIEW IF EXISTS v_azure_error_gaps;
CREATE VIEW v_azure_error_gaps AS
SELECT event_id, machine_id, error_type, ts,
       LAG(ts) OVER w                                                AS prev_ts,
       ROUND((julianday(ts) - julianday(LAG(ts) OVER w)) * 24, 2)    AS hours_since_prev,
       ROW_NUMBER() OVER w                                           AS occurrence_no
FROM azure_errors
WINDOW w AS (PARTITION BY machine_id, error_type ORDER BY ts);
