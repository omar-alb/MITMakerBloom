"""SQLite storage in two files (Server/data/).

bloomdata.db: readings (id, mission_id, taken_at, position, each sensor in its unit),
the model's training data.
missions.db: missions, reading_details (risk, GPS status, millivolts, ...), alerts.

Each connection attaches bloomdata.db to missions.db, so a reading is written
to both or neither. A new sensor in config.SENSORS gets its column at startup.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

import config

SENSOR_KEYS = [sensor.key for sensor in config.SENSORS]
DETAIL_COLUMNS = [
    "sample_id", "device", "received_at", "gps", "hardware", "millivolts", "risk", "confidence", "drivers", "scored_by",
]
JSON_COLUMNS = ("millivolts", "drivers")

READINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id INTEGER NOT NULL,  -- a mission in missions.db
    taken_at TEXT NOT NULL,  -- when the boat took it: ISO 8601, local time with the offset
    latitude REAL,  -- NULL without a GPS fix
    longitude REAL,
    {sensor_columns}  -- in units (°C, pH, NTU, µS/cm, mg/L); NULL when that probe did not report
);
CREATE INDEX IF NOT EXISTS readings_by_mission ON readings (mission_id, id);
"""

MISSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS missions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    grid_latitude REAL  -- the mission's first GPS fix; its map cells are sized for this latitude
);
CREATE TABLE IF NOT EXISTS reading_details (
    reading_id INTEGER PRIMARY KEY,  -- the reading's id in bloomdata.db
    sample_id TEXT UNIQUE,  -- the boat's id for the reading; a resend is ignored
    device TEXT NOT NULL,
    received_at TEXT NOT NULL,
    gps TEXT NOT NULL,  -- fix, stale, simulated, no_fix, or unavailable
    hardware TEXT NOT NULL,  -- ok, partial, or simulated (a bench demo)
    millivolts TEXT NOT NULL,  -- JSON, exactly as the boat sent them
    risk INTEGER NOT NULL,
    confidence INTEGER NOT NULL,
    drivers TEXT NOT NULL,  -- JSON list
    scored_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id INTEGER NOT NULL REFERENCES missions(id),
    reading_id INTEGER NOT NULL,  -- a reading in bloomdata.db
    kind TEXT NOT NULL,  -- "bloom", or the sensor key for a sensor alert
    level TEXT NOT NULL,  -- a RISK_LEVELS label
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    raised_at TEXT NOT NULL,
    acknowledged_at TEXT,
    UNIQUE (reading_id, kind)
);
CREATE INDEX IF NOT EXISTS alerts_by_mission ON alerts (mission_id, id);
"""


class MissionNameTaken(ValueError):
    """Another mission already has this name (ignoring case)."""


def init():
    """Create both databases, or bring existing ones up to date."""
    for path in (config.READINGS_DATABASE, config.MISSIONS_DATABASE):
        path.parent.mkdir(parents=True, exist_ok=True)
        with _open(path) as db:
            # Not WAL: only a rollback journal keeps a two-file commit atomic.
            db.execute("PRAGMA journal_mode = DELETE")
    with _open(config.READINGS_DATABASE) as db:
        db.executescript(READINGS_SCHEMA.format(sensor_columns=",\n    ".join(key + " REAL" for key in SENSOR_KEYS)))
        existing = {row["name"] for row in db.execute("PRAGMA table_info(readings)")}
        for key in SENSOR_KEYS:
            if key not in existing:
                db.execute("ALTER TABLE readings ADD COLUMN {} REAL".format(key))
    with _open(config.MISSIONS_DATABASE) as db:
        db.executescript(MISSIONS_SCHEMA)


# ---------------------------------------------------------------------------
# Missions
# ---------------------------------------------------------------------------


def start_mission(name=None):
    """End the active mission and start a new one (named by time if unnamed)."""
    with _connect() as db:
        db.execute("UPDATE missions SET ended_at = ? WHERE ended_at IS NULL", (_now(),))
        try:
            mission_id = _create_mission(db, name)
        except sqlite3.IntegrityError:
            raise MissionNameTaken("A mission named '{}' already exists.".format(name.strip())) from None
        return _mission(db, mission_id)


def end_mission():
    """End the active mission and return it, or None."""
    with _connect() as db:
        mission = _active_mission(db)
        if mission is None:
            return None
        db.execute("UPDATE missions SET ended_at = ? WHERE id = ?", (_now(), mission["id"]))
        return _mission(db, mission["id"])


def resume_mission():
    """The active mission, or a new one when none is active."""
    with _connect() as db:
        return _active_mission(db) or _mission(db, _create_mission(db))


def active_mission():
    with _connect() as db:
        return _active_mission(db)


def get_mission(mission_id):
    with _connect() as db:
        return _mission(db, mission_id)


def list_missions():
    """Every mission, newest first."""
    with _connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM missions ORDER BY id DESC")]


# ---------------------------------------------------------------------------
# Readings and their alerts
# ---------------------------------------------------------------------------


def save_reading(row, alerts=()):
    """Store a reading and its alerts in the active mission. Returns (reading id, created).

    A sample_id already stored is not stored again.
    """
    with _connect() as db:
        # Lock first, so two copies arriving at once can't both pass the duplicate check.
        db.execute("BEGIN IMMEDIATE")
        if row["sample_id"] is not None:
            existing = db.execute(
                "SELECT reading_id FROM reading_details WHERE sample_id = ?", (row["sample_id"],)
            ).fetchone()
            if existing:
                return existing["reading_id"], False

        mission = _active_mission(db) or _mission(db, _create_mission(db))
        measured = ["mission_id", "taken_at", "latitude", "longitude", *SENSOR_KEYS]
        reading_id = db.execute(
            "INSERT INTO readings ({}) VALUES ({})".format(", ".join(measured), ", ".join("?" * len(measured))),
            [mission["id"] if column == "mission_id" else row[column] for column in measured],
        ).lastrowid
        db.execute(
            "INSERT INTO reading_details (reading_id, {}) VALUES (?, {})".format(
                ", ".join(DETAIL_COLUMNS), ", ".join("?" * len(DETAIL_COLUMNS))
            ),
            [reading_id, *(json.dumps(row[c]) if c in JSON_COLUMNS else row[c] for c in DETAIL_COLUMNS)],
        )

        if row["latitude"] is not None and mission["grid_latitude"] is None:
            db.execute("UPDATE missions SET grid_latitude = ? WHERE id = ?", (row["latitude"], mission["id"]))
        for alert in alerts:
            db.execute(
                """INSERT INTO alerts (mission_id, reading_id, kind, level, title, message, raised_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (reading_id, kind) DO NOTHING""",
                (mission["id"], reading_id, alert["kind"], alert["level"], alert["title"], alert["message"], row["received_at"]),
            )
        return reading_id, True


def mission_readings(mission_id, limit=None, upto=None):
    """A mission's newest readings, oldest first, without millivolts. `upto`: only up to that reading id."""
    columns = _reading_columns(with_millivolts=False)
    with _connect() as db:
        rows = db.execute(
            """SELECT * FROM (
                   SELECT {} FROM readings r JOIN reading_details d ON d.reading_id = r.id
                   WHERE r.mission_id = ? AND (? IS NULL OR r.id <= ?) ORDER BY r.id DESC LIMIT ?
               ) ORDER BY id""".format(columns),
            (mission_id, upto, upto, limit or config.MISSION_READING_LIMIT),
        ).fetchall()
    return [_reading(row) for row in rows]


def each_reading(mission_id, batch_size=None):
    """Every reading of a mission, oldest first, in batches with short connections (for the CSV)."""
    columns = _reading_columns(with_millivolts=True)
    last_id = 0
    while True:
        with _connect() as db:
            rows = db.execute(
                """SELECT {} FROM readings r JOIN reading_details d ON d.reading_id = r.id
                   WHERE r.mission_id = ? AND r.id > ? ORDER BY r.id LIMIT ?""".format(columns),
                (mission_id, last_id, batch_size or config.CSV_BATCH_READINGS),
            ).fetchall()
        if not rows:
            return
        for row in rows:
            yield _reading(row)
        last_id = rows[-1]["id"]


def count_readings(mission_id):
    with _connect() as db:
        return db.execute("SELECT COUNT(*) FROM readings WHERE mission_id = ?", (mission_id,)).fetchone()[0]


def mission_alerts(mission_id, include_acknowledged=False, limit=None, upto=None):
    """A mission's newest alerts, open ones only by default. `upto`: only up to that reading id."""
    with _connect() as db:
        rows = db.execute(
            """SELECT * FROM alerts
               WHERE mission_id = ? AND (? OR acknowledged_at IS NULL) AND (? IS NULL OR reading_id <= ?)
               ORDER BY reading_id DESC, kind = 'bloom' DESC, id LIMIT ?""",
            (mission_id, include_acknowledged, upto, upto, limit or config.DASHBOARD_ALERTS),
        ).fetchall()
    return [dict(row) for row in rows]


def acknowledge_alert(alert_id):
    """Acknowledge one alert. False if there is no such alert."""
    with _connect() as db:
        if db.execute("SELECT 1 FROM alerts WHERE id = ?", (alert_id,)).fetchone() is None:
            return False
        db.execute(
            "UPDATE alerts SET acknowledged_at = ? WHERE id = ? AND acknowledged_at IS NULL", (_now(), alert_id)
        )
        return True


def acknowledge_all(mission_id):
    """Acknowledge every open alert of a mission. Returns how many."""
    with _connect() as db:
        return db.execute(
            "UPDATE alerts SET acknowledged_at = ? WHERE mission_id = ? AND acknowledged_at IS NULL",
            (_now(), mission_id),
        ).rowcount


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextmanager
def _connect():
    """missions.db with bloomdata.db attached, committed or rolled back together."""
    with _open(config.MISSIONS_DATABASE) as db:
        db.execute("ATTACH DATABASE ? AS measured", (str(config.READINGS_DATABASE),))
        yield db


@contextmanager
def _open(path):
    """One database file, committed on success and rolled back on error."""
    db = sqlite3.connect(path, timeout=config.DATABASE_WAIT_SECONDS)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _reading_columns(with_millivolts):
    """Columns of a reading joined with its details."""
    measured = ["r.id", "r.mission_id", "r.taken_at", "r.latitude", "r.longitude", *("r." + key for key in SENSOR_KEYS)]
    details = ["d." + column for column in DETAIL_COLUMNS if with_millivolts or column != "millivolts"]
    return ", ".join(measured + details)


def _create_mission(db, name=None):
    name = (name or "").strip() or _automatic_name(db)
    return db.execute("INSERT INTO missions (name, started_at) VALUES (?, ?)", (name, _now())).lastrowid


def _automatic_name(db):
    """'Mission <date> <time>', with ' (2)', ' (3)'... if taken."""
    base = "Mission {:%Y-%m-%d %H:%M}".format(datetime.now())
    name, number = base, 1
    while db.execute("SELECT 1 FROM missions WHERE name = ?", (name,)).fetchone():
        number += 1
        name = "{} ({})".format(base, number)
    return name


def _active_mission(db):
    row = db.execute("SELECT * FROM missions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def _mission(db, mission_id):
    row = db.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone()
    return dict(row) if row else None


def _reading(row):
    reading = dict(row)
    for column in JSON_COLUMNS:
        if column in reading:
            reading[column] = json.loads(reading[column])
    return reading


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")
