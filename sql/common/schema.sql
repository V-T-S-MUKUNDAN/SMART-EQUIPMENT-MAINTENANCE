-- Shared bookkeeping tables used by both dataset pipelines.
-- Every load attempt is recorded; rows that fail validation are kept (never silently dropped).

CREATE TABLE IF NOT EXISTS data_load_log (
    load_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset       TEXT    NOT NULL,              -- 'azure' | 'ai4i'
    table_name    TEXT    NOT NULL,
    source_file   TEXT    NOT NULL,
    source_sha256 TEXT    NOT NULL,              -- fingerprint of the untouched source file
    rows_read     INTEGER NOT NULL,
    rows_loaded   INTEGER NOT NULL,
    rows_rejected INTEGER NOT NULL DEFAULT 0,
    status        TEXT    NOT NULL CHECK (status IN ('success', 'skipped', 'failed')),
    message       TEXT,
    loaded_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_load_log_dataset ON data_load_log (dataset, table_name, load_id);

CREATE TABLE IF NOT EXISTS rejected_records (
    reject_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset    TEXT    NOT NULL,
    table_name TEXT    NOT NULL,
    source_row INTEGER,                          -- 1-based data row number in the CSV (header excluded)
    reason     TEXT    NOT NULL,
    raw_record TEXT                              -- JSON of the original row
);
CREATE INDEX IF NOT EXISTS idx_rejected_dataset ON rejected_records (dataset, table_name);
