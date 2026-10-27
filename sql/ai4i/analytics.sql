-- AI4I analytics. {where} expands to " AND <col> BETWEEN :lo_x AND :hi_x ..." / "AND machine_type IN (...)" (bound params).
-- IMPORTANT: UDI is a row identifier, not a time stamp. No query orders records by UDI to infer a trend;
-- every LAG()/running total below is taken over *ordered operating-condition bins*, never over adjacent UDIs.

-- name: bounds
SELECT MIN(air_temp_k) AS air_lo, MAX(air_temp_k) AS air_hi,
       MIN(process_temp_k) AS proc_lo, MAX(process_temp_k) AS proc_hi,
       MIN(rotational_speed_rpm) AS rpm_lo, MAX(rotational_speed_rpm) AS rpm_hi,
       MIN(torque_nm) AS torque_lo, MAX(torque_nm) AS torque_hi,
       MIN(tool_wear_min) AS wear_lo, MAX(tool_wear_min) AS wear_hi
FROM ai4i_machine_data;

-- name: types
SELECT DISTINCT machine_type FROM ai4i_machine_data ORDER BY machine_type;

-- A. Failure overview ----------------------------------------------------------------------------
-- name: overview
SELECT COUNT(*)                                                       AS total_records,
       COALESCE(SUM(machine_failure), 0)                              AS machine_failures,
       COALESCE(SUM(1 - machine_failure), 0)                          AS records_without_failure,
       ROUND(100.0 * SUM(machine_failure) / NULLIF(COUNT(*), 0), 3)   AS failure_rate_pct,
       COALESCE(SUM(CASE WHEN mode_count > 0 THEN 1 ELSE 0 END), 0)   AS records_with_any_mode_flag,
       COALESCE(SUM(CASE WHEN mode_count > 1 THEN 1 ELSE 0 END), 0)   AS records_with_multiple_modes
FROM v_ai4i_labeled
WHERE 1 = 1 {where};

-- name: failure_by_type
SELECT machine_type,
       COUNT(*)                                                       AS records,
       SUM(machine_failure)                                           AS failures,
       ROUND(100.0 * SUM(machine_failure) / COUNT(*), 3)              AS failure_rate_pct,
       ROUND(100.0 * SUM(machine_failure) / NULLIF(SUM(SUM(machine_failure)) OVER (), 0), 1) AS share_of_failures_pct,
       RANK() OVER (ORDER BY 1.0 * SUM(machine_failure) / COUNT(*) DESC)                     AS rate_rank
FROM v_ai4i_labeled
WHERE 1 = 1 {where}
GROUP BY machine_type
ORDER BY machine_type;

-- B. Operating-condition analysis ------------------------------------------------------------------
-- Documented, fixed-width bins:  bin_low = lo + width * floor((value - lo) / width)
--   air temp 1 K from 295 | process temp 1 K from 305 | speed 250 rpm from 1000 | torque 10 Nm from 0 | tool wear 50 min from 0
-- name: condition_bins
WITH spec(variable, var_order, lo, width) AS (
    VALUES ('Air temperature [K]', 1, 295.0, 1.0), ('Process temperature [K]', 2, 305.0, 1.0),
           ('Rotational speed [rpm]', 3, 1000.0, 250.0), ('Torque [Nm]', 4, 0.0, 10.0),
           ('Tool wear [min]', 5, 0.0, 50.0)
),
base AS (SELECT * FROM v_ai4i_labeled WHERE 1 = 1 {where}),
long AS (
    SELECT 'Air temperature [K]' AS variable, air_temp_k AS val, machine_failure FROM base
    UNION ALL SELECT 'Process temperature [K]', process_temp_k, machine_failure FROM base
    UNION ALL SELECT 'Rotational speed [rpm]', rotational_speed_rpm, machine_failure FROM base
    UNION ALL SELECT 'Torque [Nm]', torque_nm, machine_failure FROM base
    UNION ALL SELECT 'Tool wear [min]', tool_wear_min, machine_failure FROM base
),
binned AS (
    SELECT s.variable, s.var_order, s.lo + s.width * CAST((l.val - s.lo) / s.width AS INTEGER) AS bin_low,
           s.width, l.machine_failure
    FROM long l JOIN spec s ON s.variable = l.variable
),
agg AS (
    SELECT variable, var_order, bin_low, bin_low + width AS bin_high,
           COUNT(*) AS records, SUM(machine_failure) AS failures,
           1.0 * SUM(machine_failure) / COUNT(*) AS rate
    FROM binned GROUP BY variable, var_order, bin_low, width
)
SELECT variable, bin_low, bin_high, records, failures,
       ROUND(100.0 * rate, 3)                                                     AS failure_rate_pct,
       ROUND(rate / NULLIF(1.0 * SUM(failures) OVER (PARTITION BY variable)
                           / SUM(records) OVER (PARTITION BY variable), 0), 2)    AS lift_vs_overall,
       CASE WHEN records >= :min_n
            THEN RANK() OVER (PARTITION BY variable, (records >= :min_n) ORDER BY rate DESC) END AS rate_rank,
       ROUND(100.0 * (rate - LAG(rate) OVER (PARTITION BY variable ORDER BY bin_low)), 3)       AS rate_change_vs_prev_bin_pp,
       SUM(failures) OVER (PARTITION BY variable ORDER BY bin_low)                AS cumulative_failures,
       CASE WHEN records < :min_n THEN 1 ELSE 0 END                               AS low_sample
FROM agg
ORDER BY var_order, bin_low;

-- Mean of each condition among failed vs non-failed records, and the point-biserial (Pearson) correlation
-- between the condition and the failure label. Association only - not causation.
-- name: variable_summary
WITH base AS (SELECT * FROM v_ai4i_labeled WHERE 1 = 1 {where}),
long AS (
    SELECT 'Air temperature [K]' AS variable, air_temp_k AS x, machine_failure AS y FROM base
    UNION ALL SELECT 'Process temperature [K]', process_temp_k, machine_failure FROM base
    UNION ALL SELECT 'Rotational speed [rpm]', rotational_speed_rpm, machine_failure FROM base
    UNION ALL SELECT 'Torque [Nm]', torque_nm, machine_failure FROM base
    UNION ALL SELECT 'Tool wear [min]', tool_wear_min, machine_failure FROM base
)
SELECT variable,
       ROUND(AVG(CASE WHEN y = 0 THEN x END), 2) AS mean_non_failed,
       ROUND(AVG(CASE WHEN y = 1 THEN x END), 2) AS mean_failed,
       ROUND((AVG(x * y) - AVG(x) * AVG(y))
             / NULLIF(sqrt(AVG(x * x) - AVG(x) * AVG(x)) * sqrt(AVG(y * y) - AVG(y) * AVG(y)), 0), 4) AS correlation_with_failure
FROM long
GROUP BY variable
ORDER BY CASE variable WHEN 'Air temperature [K]' THEN 1 WHEN 'Process temperature [K]' THEN 2
                       WHEN 'Rotational speed [rpm]' THEN 3 WHEN 'Torque [Nm]' THEN 4 ELSE 5 END;

-- Failure rate by machine type within each tool-wear band (conditional aggregation = one column per type)
-- name: type_by_wear_band
SELECT CASE WHEN tool_wear_min < 50 THEN '0-49' WHEN tool_wear_min < 100 THEN '50-99'
            WHEN tool_wear_min < 150 THEN '100-149' WHEN tool_wear_min < 200 THEN '150-199'
            WHEN tool_wear_min < 240 THEN '200-239' ELSE '240+' END AS tool_wear_band,
       CASE WHEN tool_wear_min < 50 THEN 1 WHEN tool_wear_min < 100 THEN 2 WHEN tool_wear_min < 150 THEN 3
            WHEN tool_wear_min < 200 THEN 4 WHEN tool_wear_min < 240 THEN 5 ELSE 6 END AS band_order,
       SUM(CASE WHEN machine_type = 'L' THEN 1 ELSE 0 END)                        AS records_L,
       SUM(CASE WHEN machine_type = 'M' THEN 1 ELSE 0 END)                        AS records_M,
       SUM(CASE WHEN machine_type = 'H' THEN 1 ELSE 0 END)                        AS records_H,
       ROUND(100.0 * SUM(CASE WHEN machine_type = 'L' THEN machine_failure END)
             / NULLIF(SUM(CASE WHEN machine_type = 'L' THEN 1 END), 0), 2)        AS failure_rate_L_pct,
       ROUND(100.0 * SUM(CASE WHEN machine_type = 'M' THEN machine_failure END)
             / NULLIF(SUM(CASE WHEN machine_type = 'M' THEN 1 END), 0), 2)        AS failure_rate_M_pct,
       ROUND(100.0 * SUM(CASE WHEN machine_type = 'H' THEN machine_failure END)
             / NULLIF(SUM(CASE WHEN machine_type = 'H' THEN 1 END), 0), 2)        AS failure_rate_H_pct
FROM v_ai4i_labeled
WHERE 1 = 1 {where}
GROUP BY tool_wear_band, band_order
ORDER BY band_order;

-- C. Failure-mode analysis -------------------------------------------------------------------------
-- Each mode is counted independently; modes can co-occur, so counts need not sum to the failure total.
-- name: failure_modes
WITH base AS (SELECT * FROM v_ai4i_labeled WHERE 1 = 1 {where}),
modes AS (
    SELECT 'TWF' AS mode, 'Tool Wear Failure' AS name, twf AS flag, machine_failure FROM base
    UNION ALL SELECT 'HDF', 'Heat Dissipation Failure', hdf, machine_failure FROM base
    UNION ALL SELECT 'PWF', 'Power Failure',            pwf, machine_failure FROM base
    UNION ALL SELECT 'OSF', 'Overstrain Failure',       osf, machine_failure FROM base
    UNION ALL SELECT 'RNF', 'Random Failure',           rnf, machine_failure FROM base
)
SELECT mode, name,
       SUM(flag)                                                              AS records_with_mode,
       ROUND(100.0 * SUM(flag) / COUNT(*), 3)                                 AS rate_of_all_records_pct,
       ROUND(100.0 * SUM(flag) / NULLIF(SUM(machine_failure), 0), 2)          AS pct_of_machine_failures,
       SUM(CASE WHEN flag = 1 AND machine_failure = 1 THEN 1 ELSE 0 END)      AS with_machine_failure_1,
       SUM(CASE WHEN flag = 1 AND machine_failure = 0 THEN 1 ELSE 0 END)      AS with_machine_failure_0
FROM modes
GROUP BY mode, name
ORDER BY records_with_mode DESC;

-- Pairs of modes flagged on the same record (self-join on the primary key UDI: exactly one row per record and pair).
-- name: mode_cooccurrence
WITH base AS (SELECT * FROM v_ai4i_labeled WHERE 1 = 1 {where}),
modes AS (
    SELECT udi, 'TWF' AS mode, twf AS flag FROM base UNION ALL SELECT udi, 'HDF', hdf FROM base
    UNION ALL SELECT udi, 'PWF', pwf FROM base UNION ALL SELECT udi, 'OSF', osf FROM base
    UNION ALL SELECT udi, 'RNF', rnf FROM base
)
SELECT a.mode AS mode_a, b.mode AS mode_b, COUNT(*) AS records_with_both
FROM modes a JOIN modes b ON a.udi = b.udi AND a.mode < b.mode
WHERE a.flag = 1 AND b.flag = 1
GROUP BY a.mode, b.mode
ORDER BY records_with_both DESC, mode_a, mode_b;

-- name: modes_per_record
SELECT mode_count AS modes_flagged, COUNT(*) AS records,
       SUM(machine_failure) AS with_machine_failure_1
FROM v_ai4i_labeled WHERE 1 = 1 {where}
GROUP BY mode_count ORDER BY mode_count;

-- name: modes_by_type
SELECT machine_type, COUNT(*) AS records, SUM(machine_failure) AS machine_failures,
       SUM(twf) AS twf, SUM(hdf) AS hdf, SUM(pwf) AS pwf, SUM(osf) AS osf, SUM(rnf) AS rnf
FROM v_ai4i_labeled WHERE 1 = 1 {where}
GROUP BY machine_type ORDER BY machine_type;

-- Consistency between the overall label and the individual mode labels. Flagged, never modified.
-- name: label_consistency
SELECT label_consistency, COUNT(*) AS records FROM v_ai4i_labeled WHERE 1 = 1 {where}
GROUP BY label_consistency ORDER BY records DESC;

-- name: inconsistent_records
SELECT udi, product_id, machine_type, machine_failure, twf, hdf, pwf, osf, rnf,
       air_temp_k, process_temp_k, rotational_speed_rpm, torque_nm, tool_wear_min, label_consistency
FROM v_ai4i_labeled
WHERE label_consistency IN ('failure_without_mode', 'mode_without_failure') {where}
ORDER BY label_consistency, udi;

-- Does each mode label agree with the failure rule given in the dataset description? (descriptive check)
-- name: rule_agreement
WITH base AS (SELECT * FROM v_ai4i_labeled WHERE 1 = 1 {where}),
r AS (
    SELECT 'HDF' AS mode, 'temp difference < 8.6 K and speed < 1380 rpm' AS rule, hdf_rule AS rule_met, hdf AS label FROM base
    UNION ALL SELECT 'PWF', 'power < 3500 W or > 9000 W', pwf_rule, pwf FROM base
    UNION ALL SELECT 'OSF', 'tool wear x torque > 11000/12000/13000 minNm (L/M/H)', osf_rule, osf FROM base
)
SELECT mode, rule,
       SUM(CASE WHEN rule_met = 1 AND label = 1 THEN 1 ELSE 0 END) AS rule_met_and_labelled,
       SUM(CASE WHEN rule_met = 1 AND label = 0 THEN 1 ELSE 0 END) AS rule_met_not_labelled,
       SUM(CASE WHEN rule_met = 0 AND label = 1 THEN 1 ELSE 0 END) AS labelled_rule_not_met,
       SUM(CASE WHEN rule_met = 0 AND label = 0 THEN 1 ELSE 0 END) AS neither
FROM r GROUP BY mode, rule ORDER BY mode;

-- D. Window functions over ordered operating-condition groups --------------------------------------
-- Group = machine type x tool-wear band. ROW_NUMBER/RANK order groups by failure rate; LAG compares neighbouring
-- wear bands of the same type; SUM() OVER gives running totals and shares. (Bins are ordered by wear, not by time.)
-- name: group_window_analysis
WITH grp AS (
    SELECT machine_type,
           CASE WHEN tool_wear_min < 50 THEN 1 WHEN tool_wear_min < 100 THEN 2 WHEN tool_wear_min < 150 THEN 3
                WHEN tool_wear_min < 200 THEN 4 WHEN tool_wear_min < 240 THEN 5 ELSE 6 END AS band_order,
           CASE WHEN tool_wear_min < 50 THEN '0-49' WHEN tool_wear_min < 100 THEN '50-99'
                WHEN tool_wear_min < 150 THEN '100-149' WHEN tool_wear_min < 200 THEN '150-199'
                WHEN tool_wear_min < 240 THEN '200-239' ELSE '240+' END AS tool_wear_band,
           COUNT(*) AS records, SUM(machine_failure) AS failures,
           1.0 * SUM(machine_failure) / COUNT(*) AS rate
    FROM v_ai4i_labeled WHERE 1 = 1 {where}
    GROUP BY machine_type, band_order, tool_wear_band
)
SELECT machine_type, tool_wear_band, records, failures,
       ROUND(100.0 * rate, 2)                                                           AS failure_rate_pct,
       ROW_NUMBER() OVER (ORDER BY rate DESC, records DESC)                             AS overall_position,
       RANK() OVER (PARTITION BY machine_type ORDER BY rate DESC)                       AS rank_within_type,
       ROUND(100.0 * (rate - LAG(rate) OVER (PARTITION BY machine_type ORDER BY band_order)), 2) AS change_vs_prev_band_pp,
       SUM(failures) OVER (PARTITION BY machine_type ORDER BY band_order)               AS cumulative_failures_in_type,
       ROUND(100.0 * failures / NULLIF(SUM(failures) OVER (PARTITION BY machine_type), 0), 1)    AS share_of_type_failures_pct,
       CASE WHEN records < :min_n THEN 1 ELSE 0 END                                     AS low_sample
FROM grp
ORDER BY machine_type, band_order;
