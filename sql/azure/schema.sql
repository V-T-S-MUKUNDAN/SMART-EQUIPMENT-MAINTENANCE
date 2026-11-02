-- Azure Predictive Maintenance dataset: tables, keys and indexes.
-- Timestamps are stored as ISO-8601 text 'YYYY-MM-DD HH:MM:SS' (sortable, usable by strftime/datetime).

CREATE TABLE IF NOT EXISTS azure_machines (
    machine_id INTEGER PRIMARY KEY,
    model      TEXT    NOT NULL,
    age        INTEGER NOT NULL CHECK (age >= 0)
);

-- One reading per machine per hour. WITHOUT ROWID makes the primary key the clustered index,
-- so range scans for (machine_id, ts BETWEEN ...) read contiguous pages.
CREATE TABLE IF NOT EXISTS azure_telemetry (
    machine_id INTEGER NOT NULL REFERENCES azure_machines (machine_id),
    ts         TEXT    NOT NULL,
    volt       REAL,
    rotate     REAL,
    pressure   REAL,
    vibration  REAL,
    PRIMARY KEY (machine_id, ts)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_az_telemetry_ts ON azure_telemetry (ts);

-- Several components can fail/err/be serviced at the same instant, so the component/type
-- is part of the natural key.
CREATE TABLE IF NOT EXISTS azure_errors (
    event_id   INTEGER PRIMARY KEY,
    machine_id INTEGER NOT NULL REFERENCES azure_machines (machine_id),
    ts         TEXT    NOT NULL,
    error_type TEXT    NOT NULL,
    UNIQUE (machine_id, ts, error_type)
);
CREATE INDEX IF NOT EXISTS idx_az_errors_ts   ON azure_errors (ts);
CREATE INDEX IF NOT EXISTS idx_az_errors_type ON azure_errors (error_type, machine_id);

CREATE TABLE IF NOT EXISTS azure_failures (
    event_id   INTEGER PRIMARY KEY,
    machine_id INTEGER NOT NULL REFERENCES azure_machines (machine_id),
    ts         TEXT    NOT NULL,
    component  TEXT    NOT NULL,
    UNIQUE (machine_id, ts, component)
);
CREATE INDEX IF NOT EXISTS idx_az_failures_ts   ON azure_failures (ts);
CREATE INDEX IF NOT EXISTS idx_az_failures_comp ON azure_failures (component, machine_id);

CREATE TABLE IF NOT EXISTS azure_maintenance (
    event_id   INTEGER PRIMARY KEY,
    machine_id INTEGER NOT NULL REFERENCES azure_machines (machine_id),
    ts         TEXT    NOT NULL,
    component  TEXT    NOT NULL,
    UNIQUE (machine_id, ts, component)
);
CREATE INDEX IF NOT EXISTS idx_az_maint_ts   ON azure_maintenance (ts);
CREATE INDEX IF NOT EXISTS idx_az_maint_comp ON azure_maintenance (component, machine_id);

-- The fleet baseline is now computed per as-of date in sql/azure/risk.sql; remove the old stored copy.
DROP TABLE IF EXISTS azure_fleet_baseline;
