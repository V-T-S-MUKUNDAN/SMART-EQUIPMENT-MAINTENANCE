-- AI4I 2020 Predictive Maintenance dataset (independent of the azure_* tables; never joined to them).
-- Original column names are preserved in docs/README (see ai4i.loader.COLUMN_MAP); SQLite-safe names are used here.
-- UDI is a row identifier, NOT a timestamp: rows are not a chronological history of one machine.

CREATE TABLE IF NOT EXISTS ai4i_machine_data (
    udi                   INTEGER PRIMARY KEY,                                  -- 'UDI' (verified unique)
    product_id            TEXT    NOT NULL,                                     -- 'Product ID'
    machine_type          TEXT    NOT NULL CHECK (machine_type IN ('L','M','H')),   -- 'Type'
    air_temp_k            REAL    NOT NULL,                                     -- 'Air temperature [K]'
    process_temp_k        REAL    NOT NULL,                                     -- 'Process temperature [K]'
    rotational_speed_rpm  INTEGER NOT NULL,                                     -- 'Rotational speed [rpm]'
    torque_nm             REAL    NOT NULL,                                     -- 'Torque [Nm]'
    tool_wear_min         INTEGER NOT NULL CHECK (tool_wear_min >= 0),          -- 'Tool wear [min]'
    machine_failure       INTEGER NOT NULL CHECK (machine_failure IN (0,1)),    -- 'Machine failure'
    twf                   INTEGER NOT NULL CHECK (twf IN (0,1)),                -- Tool wear failure
    hdf                   INTEGER NOT NULL CHECK (hdf IN (0,1)),                -- Heat dissipation failure
    pwf                   INTEGER NOT NULL CHECK (pwf IN (0,1)),                -- Power failure
    osf                   INTEGER NOT NULL CHECK (osf IN (0,1)),                -- Overstrain failure
    rnf                   INTEGER NOT NULL CHECK (rnf IN (0,1))                 -- Random failure
);
CREATE INDEX IF NOT EXISTS idx_ai4i_type        ON ai4i_machine_data (machine_type, machine_failure);
CREATE INDEX IF NOT EXISTS idx_ai4i_failure     ON ai4i_machine_data (machine_failure);
CREATE INDEX IF NOT EXISTS idx_ai4i_tool_wear   ON ai4i_machine_data (tool_wear_min);
CREATE INDEX IF NOT EXISTS idx_ai4i_product     ON ai4i_machine_data (product_id);

-- Derived columns used by the analytics. Nothing here edits a source value: labels are only *classified*.
DROP VIEW IF EXISTS v_ai4i_labeled;
CREATE VIEW v_ai4i_labeled AS
SELECT d.*,
       process_temp_k - air_temp_k                                   AS temp_diff_k,
       torque_nm * rotational_speed_rpm * 2.0 * pi() / 60.0          AS power_w,        -- torque x angular speed (rad/s)
       tool_wear_min * torque_nm                                     AS strain_minnm,   -- tool wear x torque
       (twf + hdf + pwf + osf + rnf)                                 AS mode_count,
       CASE WHEN machine_failure = 1 AND (twf + hdf + pwf + osf + rnf) = 0 THEN 'failure_without_mode'
            WHEN machine_failure = 0 AND (twf + hdf + pwf + osf + rnf) > 0 THEN 'mode_without_failure'
            WHEN machine_failure = 1 THEN 'consistent_failure'
            ELSE 'consistent_no_failure' END                         AS label_consistency,
       -- documented failure-mode rules (published dataset description); used to *check* labels, never to alter them
       CASE WHEN process_temp_k - air_temp_k < 8.6 AND rotational_speed_rpm < 1380 THEN 1 ELSE 0 END AS hdf_rule,
       CASE WHEN torque_nm * rotational_speed_rpm * 2.0 * pi() / 60.0 < 3500
              OR torque_nm * rotational_speed_rpm * 2.0 * pi() / 60.0 > 9000 THEN 1 ELSE 0 END      AS pwf_rule,
       CASE WHEN tool_wear_min * torque_nm >
                 CASE machine_type WHEN 'L' THEN 11000 WHEN 'M' THEN 12000 ELSE 13000 END THEN 1 ELSE 0 END AS osf_rule
FROM ai4i_machine_data d;
