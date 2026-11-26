"""Database schema and migrations for Miele Local."""

import logging
import sqlite3

logger = logging.getLogger(__name__)

SUPPORTED_SCHEMA_VERSION = 2

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS appliances (
    device_id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_name TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    host TEXT NOT NULL,
    group_id TEXT NOT NULL,
    group_key TEXT NOT NULL,
    device_route TEXT NOT NULL DEFAULT 'auto',
    device_type INTEGER,
    decoded_device_type TEXT,
    category TEXT DEFAULT 'appliance',
    tech_type TEXT,
    serial_number TEXT,
    xkm_tech_type TEXT,
    xkm_release_version TEXT,
    dop2_supported INTEGER,
    remote_start_capable INTEGER,
    enabled INTEGER NOT NULL DEFAULT 1,
    poll_interval INTEGER NOT NULL DEFAULT 15,
    last_seen TIMESTAMP,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_appliances_endpoint ON appliances(endpoint_name);
CREATE INDEX IF NOT EXISTS idx_appliances_host ON appliances(host);

CREATE TABLE IF NOT EXISTS appliance_state_history (
    device_id INTEGER NOT NULL,
    timestamp_bucket TEXT NOT NULL,
    status_code INTEGER,
    decoded_status TEXT,
    program_id INTEGER,
    decoded_program_id TEXT,
    program_phase INTEGER,
    decoded_program_phase TEXT,
    progress REAL,
    remaining_minutes INTEGER,
    elapsed_minutes INTEGER,
    current_temperature REAL,
    target_temperature REAL,
    spinning_speed INTEGER,
    signal_door INTEGER,
    signal_info INTEGER,
    signal_failure INTEGER,
    light INTEGER,
    standby_state INTEGER,
    raw_json TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (device_id, timestamp_bucket),
    FOREIGN KEY (device_id) REFERENCES appliances(device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_history_device_time
    ON appliance_state_history(device_id, timestamp_bucket DESC);

CREATE TABLE IF NOT EXISTS dop2_cache (
    device_id INTEGER PRIMARY KEY,
    dop2_tree_json TEXT NOT NULL,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (device_id) REFERENCES appliances(device_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS appliance_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_minutes INTEGER DEFAULT 0,
    program_id INTEGER,
    decoded_program_id TEXT,
    target_temperature REAL,
    spinning_speed INTEGER,
    final_status INTEGER,
    decoded_final_status TEXT,
    outcome TEXT NOT NULL DEFAULT 'in_progress',
    energy_kwh REAL,
    water_litres REAL,
    consumption_source TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (device_id) REFERENCES appliances(device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_runs_device_started
    ON appliance_runs(device_id, started_at DESC);

CREATE TABLE IF NOT EXISTS program_estimates (
    device_id INTEGER NOT NULL,
    program_name TEXT NOT NULL,
    energy_kwh REAL,
    water_litres REAL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (device_id, program_name),
    FOREIGN KEY (device_id) REFERENCES appliances(device_id) ON DELETE CASCADE
);
"""


def ensure_schema_and_migrate(db_path: str) -> None:
    """Create schema if needed and apply incremental migrations via PRAGMA user_version."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        row = conn.execute("PRAGMA user_version").fetchone()
        current_version = row[0] if row else 0
        if current_version > SUPPORTED_SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version ({current_version}) is newer than supported ({SUPPORTED_SCHEMA_VERSION})"
            )

        for stmt in [s.strip() for s in DB_SCHEMA.split(";") if s.strip()]:
            conn.execute(stmt)

        if current_version < SUPPORTED_SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {SUPPORTED_SCHEMA_VERSION}")

        conn.commit()
    finally:
        conn.close()
