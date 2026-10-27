-- AI4I operating-condition risk indicator (row level). See docs/risk_methodology.md.
-- The scoring CTEs read ONLY operating-condition columns. The failure labels are attached afterwards
-- (final SELECT) purely so that results can be evaluated; changing a label cannot change a score.
--
-- Factors (each scaled to 0..1 against a documented reference, then weighted):
--   tool_wear  = MIN(1, tool_wear / :wear_ref)
--   strain     = MIN(1, tool_wear * torque / type_limit)             (type_limit: L/M/H = :osf_l/:osf_m/:osf_h)
--   power      = MIN(1, |power - mid| / half_width), power = torque * rpm * 2*pi/60, band [:p_low, :p_high]
--   heat       = r_diff * r_speed, both are 0 at the dataset-average value and 1 at the documented limit
--                (AND-logic: heat dissipation needs low temperature difference AND low speed)
-- score = 100 * sum(w_i * f_i) / sum(w_i)

-- name: risk_scores
WITH ref AS (   -- label-independent dataset-wide reference values for the heat factor
    SELECT AVG(process_temp_k - air_temp_k) AS avg_diff, AVG(rotational_speed_rpm) AS avg_rpm
    FROM ai4i_machine_data
),
base AS (
    SELECT udi, product_id, machine_type, air_temp_k, process_temp_k, rotational_speed_rpm, torque_nm, tool_wear_min,
           process_temp_k - air_temp_k AS temp_diff_k,
           torque_nm * rotational_speed_rpm * 2.0 * pi() / 60.0 AS power_w
    FROM ai4i_machine_data
    WHERE 1 = 1 {where}
),
factors AS (
    SELECT b.*,
        MIN(1.0, 1.0 * b.tool_wear_min / :wear_ref)                                              AS f_tool_wear,
        MIN(1.0, 1.0 * b.tool_wear_min * b.torque_nm
                 / CASE b.machine_type WHEN 'L' THEN :osf_l WHEN 'M' THEN :osf_m ELSE :osf_h END)  AS f_strain,
        MIN(1.0, ABS(b.power_w - (:p_low + :p_high) / 2.0) / ((:p_high - :p_low) / 2.0))          AS f_power,
        MAX(0.0, MIN(1.0, (ref.avg_diff - b.temp_diff_k) / NULLIF(ref.avg_diff - :hdf_diff, 0)))
          * MAX(0.0, MIN(1.0, (ref.avg_rpm - b.rotational_speed_rpm) / NULLIF(ref.avg_rpm - :hdf_rpm, 0))) AS f_heat
    FROM base b CROSS JOIN ref
),
scored AS (
    SELECT factors.*,
        100.0 * :w_tool_wear * f_tool_wear / (:w_tool_wear + :w_strain + :w_power + :w_heat) AS c_tool_wear,
        100.0 * :w_strain    * f_strain    / (:w_tool_wear + :w_strain + :w_power + :w_heat) AS c_strain,
        100.0 * :w_power     * f_power     / (:w_tool_wear + :w_strain + :w_power + :w_heat) AS c_power,
        100.0 * :w_heat      * f_heat      / (:w_tool_wear + :w_strain + :w_power + :w_heat) AS c_heat
    FROM factors
),
final AS (
    SELECT scored.*, ROUND(c_tool_wear + c_strain + c_power + c_heat, 2) AS risk_score FROM scored
)
SELECT f.*,
       CASE WHEN risk_score >= :t_critical THEN 'Critical' WHEN risk_score >= :t_high THEN 'High'
            WHEN risk_score >= :t_medium THEN 'Medium' ELSE 'Low' END AS risk_category,
       -- recorded labels, attached for *evaluation only*
       d.machine_failure, d.twf, d.hdf, d.pwf, d.osf, d.rnf
FROM final f JOIN ai4i_machine_data d ON d.udi = f.udi
ORDER BY risk_score DESC, f.udi;

-- Evaluation queries below embed the scored rows through a placeholder replaced in Python. Labels are used
-- here, after scoring, to describe how the indicator relates to recorded failures on THIS dataset
-- (in-sample, descriptive; not a predictive validation).

-- name: category_validation
WITH scores AS ({risk})
SELECT risk_category, COUNT(*) AS records, SUM(machine_failure) AS failures,
       ROUND(100.0 * SUM(machine_failure) / COUNT(*), 2) AS observed_failure_rate_pct,
       ROUND(MIN(risk_score), 2) AS min_score, ROUND(MAX(risk_score), 2) AS max_score
FROM scores
GROUP BY risk_category
ORDER BY CASE risk_category WHEN 'Low' THEN 1 WHEN 'Medium' THEN 2 WHEN 'High' THEN 3 ELSE 4 END;

-- name: decile_validation
WITH scores AS ({risk}),
dec AS (SELECT risk_score, machine_failure, NTILE(10) OVER (ORDER BY risk_score) AS decile FROM scores)
SELECT decile, COUNT(*) AS records, ROUND(MIN(risk_score), 2) AS min_score, ROUND(MAX(risk_score), 2) AS max_score,
       SUM(machine_failure) AS failures, ROUND(100.0 * SUM(machine_failure) / COUNT(*), 2) AS failure_rate_pct
FROM dec GROUP BY decile ORDER BY decile;

-- Threshold sweep: for each cut-off, what share of records is flagged, and how many recorded failures fall inside?
-- (the join to `cuts` deliberately repeats each record once per cut-off it satisfies)
-- name: threshold_sweep
WITH scores AS ({risk}),
cuts(cutoff) AS (VALUES (10),(20),(25),(30),(40),(50),(60),(70),(75),(80)),
tot AS (SELECT COUNT(*) AS n, SUM(machine_failure) AS f FROM scores)
SELECT c.cutoff AS score_at_least, COUNT(s.udi) AS records_flagged,
       ROUND(100.0 * COUNT(s.udi) / tot.n, 2) AS pct_of_records,
       COALESCE(SUM(s.machine_failure), 0) AS failures_flagged,
       ROUND(100.0 * COALESCE(SUM(s.machine_failure), 0) / NULLIF(tot.f, 0), 2) AS pct_of_failures_captured,
       ROUND(100.0 * SUM(s.machine_failure) / NULLIF(COUNT(s.udi), 0), 2) AS failure_rate_among_flagged_pct
FROM cuts c CROSS JOIN tot LEFT JOIN scores s ON s.risk_score >= c.cutoff
GROUP BY c.cutoff, tot.n, tot.f ORDER BY c.cutoff;

-- Rank separation (AUC) from the Mann-Whitney U statistic using window functions; ties get the average rank.
-- name: rank_separation
WITH scores AS ({risk}),
ranked AS (
    SELECT machine_failure,
           RANK() OVER (ORDER BY risk_score) + (COUNT(*) OVER (PARTITION BY risk_score) - 1) / 2.0 AS avg_rank
    FROM scores
),
c AS (SELECT SUM(machine_failure) AS n_pos, SUM(1 - machine_failure) AS n_neg,
             SUM(CASE WHEN machine_failure = 1 THEN avg_rank END) AS rank_sum_pos FROM ranked)
SELECT n_pos AS failed_records, n_neg AS non_failed_records,
       ROUND((rank_sum_pos - n_pos * (n_pos + 1) / 2.0) / NULLIF(1.0 * n_pos * n_neg, 0), 4) AS auc
FROM c;

-- Group-level ranking for Module 5: machine type x tool-wear band. Failure counts are descriptive only.
-- name: group_ranking
WITH scores AS ({risk}),
grp AS (
    SELECT machine_type,
           CASE WHEN tool_wear_min < 50 THEN '0-49' WHEN tool_wear_min < 100 THEN '50-99'
                WHEN tool_wear_min < 150 THEN '100-149' WHEN tool_wear_min < 200 THEN '150-199'
                WHEN tool_wear_min < 240 THEN '200-239' ELSE '240+' END AS tool_wear_band,
           COUNT(*) AS records, AVG(risk_score) AS avg_score, MAX(risk_score) AS max_score,
           AVG(c_tool_wear) AS avg_c_tool_wear, AVG(c_strain) AS avg_c_strain,
           AVG(c_power) AS avg_c_power, AVG(c_heat) AS avg_c_heat,
           SUM(machine_failure) AS recorded_failures, MIN(udi) AS first_udi
    FROM scores GROUP BY machine_type, tool_wear_band
)
SELECT RANK() OVER (ORDER BY avg_score DESC) AS priority_rank, machine_type, tool_wear_band, records,
       ROUND(avg_score, 2) AS avg_risk_score, ROUND(max_score, 2) AS max_risk_score,
       ROUND(avg_c_tool_wear, 2) AS avg_c_tool_wear, ROUND(avg_c_strain, 2) AS avg_c_strain,
       ROUND(avg_c_power, 2) AS avg_c_power, ROUND(avg_c_heat, 2) AS avg_c_heat,
       recorded_failures, ROUND(100.0 * recorded_failures / records, 2) AS recorded_failure_rate_pct,
       CASE WHEN records < :min_n THEN 1 ELSE 0 END AS low_sample
FROM grp ORDER BY priority_rank, machine_type;
