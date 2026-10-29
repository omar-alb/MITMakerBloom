"""SQLite storage: missions, readings, alerts."""

import json
import sqlite3
import unittest
from unittest import mock

from helpers import HERE, NOW, payload, use_rules_only, use_scratch_database

import config
import database
import processing


def row(**overrides):
    return {**processing.process(payload(), now=NOW), **overrides}


def columns(path, table):
    with sqlite3.connect(path) as db:
        return [column[1] for column in db.execute("PRAGMA table_info({})".format(table))]


def bloom_alert(kind="bloom"):
    return {"kind": kind, "level": "High", "title": "High bloom risk", "message": "Bloom risk 60/100."}


class MissionTests(unittest.TestCase):
    def setUp(self):
        use_scratch_database(self)

    def test_starting_a_mission_ends_the_active_one(self):
        first = database.start_mission()
        second = database.start_mission("Inlet transect")

        self.assertIsNotNone(database.get_mission(first["id"])["ended_at"])
        self.assertIsNone(second["ended_at"])
        self.assertEqual(database.active_mission()["id"], second["id"])
        self.assertEqual([m["id"] for m in database.list_missions()], [second["id"], first["id"]])

    def test_automatic_names_stay_unique_through_quick_restarts(self):
        names = [database.start_mission()["name"] for _ in range(4)]
        self.assertEqual(len(set(names)), 4)
        self.assertTrue(names[1].endswith("(2)"))

    def test_a_taken_name_is_refused_and_nothing_changes(self):
        active = database.start_mission("Shoreline sweep")
        with self.assertRaises(database.MissionNameTaken):
            database.start_mission("shoreline SWEEP")
        self.assertEqual(database.active_mission()["id"], active["id"])

    def test_ending_twice_ends_nothing_the_second_time(self):
        database.start_mission()
        self.assertIsNotNone(database.end_mission())
        self.assertIsNone(database.end_mission())
        self.assertIsNone(database.active_mission())

    def test_resume_keeps_the_active_mission_or_starts_one(self):
        started = database.resume_mission()
        self.assertEqual(database.resume_mission()["id"], started["id"])


class ReadingTests(unittest.TestCase):
    def setUp(self):
        use_scratch_database(self)
        use_rules_only(self)

    def test_a_resent_reading_is_stored_once(self):
        first_id, created = database.save_reading(row(sample_id="boat-a1-7"))
        again_id, created_again = database.save_reading(row(sample_id="boat-a1-7", risk=99))

        self.assertEqual((created, created_again, first_id), (True, False, again_id))
        mission_id = database.active_mission()["id"]
        self.assertEqual(database.count_readings(mission_id), 1)
        self.assertNotEqual(database.mission_readings(mission_id)[0]["risk"], 99)

    def test_readings_without_a_sample_id_are_never_merged(self):
        database.save_reading(row())
        database.save_reading(row())
        self.assertEqual(database.count_readings(database.active_mission()["id"]), 2)

    def test_a_reading_with_no_active_mission_starts_one(self):
        self.assertIsNone(database.active_mission())
        database.save_reading(row())
        self.assertIsNotNone(database.active_mission())

    def test_lists_and_dicts_come_back_as_they_went_in(self):
        database.save_reading(row(drivers=["warm water"]))
        mission_id = database.active_mission()["id"]
        stored = database.mission_readings(mission_id)[0]
        self.assertEqual(stored["drivers"], ["warm water"])
        self.assertNotIn("millivolts", stored)  # the dashboard does not need them
        self.assertEqual(next(database.each_reading(mission_id))["millivolts"]["ph"], 1450.0)

    def test_mission_readings_are_the_newest_oldest_first(self):
        for risk in range(12):
            database.save_reading(row(risk=risk))
        mission_id = database.active_mission()["id"]
        self.assertEqual([r["risk"] for r in database.mission_readings(mission_id)], list(range(12)))
        self.assertEqual([r["risk"] for r in database.mission_readings(mission_id, limit=5)], list(range(7, 12)))
        self.assertEqual([r["risk"] for r in database.each_reading(mission_id, batch_size=5)], list(range(12)))

    def test_the_first_fix_sizes_the_grid_and_later_ones_leave_it(self):
        database.save_reading(row(latitude=None, longitude=None, gps="no_fix"))
        self.assertIsNone(database.active_mission()["grid_latitude"])
        database.save_reading(row())
        database.save_reading(row(latitude=HERE[0] + 0.1))
        self.assertEqual(database.active_mission()["grid_latitude"], HERE[0])

    def test_a_new_sensor_gets_its_column_on_the_next_start(self):
        extra = config.Sensor("oxygen", "Dissolved oxygen", "mg/L", 1, 0.0, 6.0, 2.0, "low oxygen")
        with mock.patch.object(config, "SENSORS", config.SENSORS + (extra,)), \
                mock.patch.object(database, "SENSOR_KEYS", database.SENSOR_KEYS + ["oxygen"]):
            database.init()
        self.assertIn("oxygen", columns(config.READINGS_DATABASE, "readings"))


class AlertTests(unittest.TestCase):
    def setUp(self):
        use_scratch_database(self)
        use_rules_only(self)

    def test_alerts_are_saved_with_their_reading(self):
        reading_id, _ = database.save_reading(row(), [bloom_alert(), bloom_alert("turbidity")])
        saved = database.mission_alerts(database.active_mission()["id"])
        self.assertEqual([alert["kind"] for alert in saved], ["bloom", "turbidity"])
        self.assertTrue(all(alert["reading_id"] == reading_id for alert in saved))

    def test_a_resent_reading_raises_no_second_alert(self):
        database.save_reading(row(sample_id="boat-a1-9"), [bloom_alert()])
        database.save_reading(row(sample_id="boat-a1-9"), [bloom_alert()])
        self.assertEqual(len(database.mission_alerts(database.active_mission()["id"])), 1)

    def test_upto_gives_the_readings_and_alerts_up_to_a_reading(self):
        ids = [database.save_reading(row(), [bloom_alert()])[0] for _ in range(4)]
        mission_id = database.active_mission()["id"]
        self.assertEqual([r["id"] for r in database.mission_readings(mission_id, upto=ids[1])], ids[:2])
        self.assertEqual(sorted(a["reading_id"] for a in database.mission_alerts(mission_id, upto=ids[1])), ids[:2])

    def test_acknowledging(self):
        database.save_reading(row(), [bloom_alert()])
        database.save_reading(row(), [bloom_alert()])
        mission_id = database.active_mission()["id"]
        first, second = database.mission_alerts(mission_id)

        self.assertTrue(database.acknowledge_alert(first["id"]))
        self.assertFalse(database.acknowledge_alert(987654))
        self.assertEqual([a["id"] for a in database.mission_alerts(mission_id)], [second["id"]])
        self.assertEqual(database.acknowledge_all(mission_id), 1)
        self.assertEqual(database.mission_alerts(mission_id), [])
        self.assertEqual(len(database.mission_alerts(mission_id, include_acknowledged=True)), 2)


class TwoFileTests(unittest.TestCase):
    """bloomdata.db is the training data, and only that; missions.db holds the rest."""

    def setUp(self):
        use_scratch_database(self)
        use_rules_only(self)

    def test_bloomdata_db_holds_only_the_measurements(self):
        sensors = [sensor.key for sensor in config.SENSORS]
        self.assertEqual(columns(config.READINGS_DATABASE, "readings"), ["id", "mission_id", "taken_at", "latitude", "longitude", *sensors])
        with sqlite3.connect(config.READINGS_DATABASE) as db:
            self.assertEqual([r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")], ["readings"])

    def test_bloomdata_db_holds_values_in_their_units_not_millivolts(self):
        sent = row()
        database.save_reading(sent)
        with sqlite3.connect(config.READINGS_DATABASE) as db:
            db.row_factory = sqlite3.Row
            measured = dict(db.execute("SELECT * FROM readings").fetchone())

        units = processing.to_units(sent["millivolts"])
        for sensor in config.SENSORS:
            self.assertEqual(measured[sensor.key], units[sensor.key], sensor.key)
        self.assertEqual(measured["temperature"], 22.0)  # 720 mV from a TMP36 is 22.0 °C
        self.assertEqual(measured["ph"], 7.85)  # 1450 mV, against a 1500 mV pH 7 buffer
        self.assertIsNone(measured["nitrate"])  # not wired: missing, not zero
        # The millivolts themselves are kept, in missions.db, for recalibrating later.
        with sqlite3.connect(config.MISSIONS_DATABASE) as db:
            self.assertEqual(json.loads(db.execute("SELECT millivolts FROM reading_details").fetchone()[0])["ph"], 1450.0)

    def test_missions_db_points_at_readings_by_their_id(self):
        reading_id, _ = database.save_reading(row(sample_id="boat-a1-1", risk=64), [bloom_alert()])

        with sqlite3.connect(config.READINGS_DATABASE) as db:
            measured = db.execute("SELECT id, mission_id, taken_at, latitude, ph FROM readings").fetchall()
        with sqlite3.connect(config.MISSIONS_DATABASE) as db:
            details = db.execute("SELECT reading_id, sample_id, risk FROM reading_details").fetchall()
            alerts = db.execute("SELECT reading_id FROM alerts").fetchall()

        self.assertEqual(len(measured), 1)
        self.assertEqual(measured[0][:2], (reading_id, database.active_mission()["id"]))
        self.assertEqual(details, [(reading_id, "boat-a1-1", 64)])
        self.assertEqual(alerts, [(reading_id,)])
        # The rest of the server still sees one reading with everything joined.
        stored = database.mission_readings(database.active_mission()["id"])[0]
        self.assertEqual((stored["id"], stored["risk"], stored["sample_id"]), (reading_id, 64, "boat-a1-1"))

    def test_a_reading_is_stored_in_both_files_or_in_neither(self):
        broken_alert = {"kind": "bloom"}  # no level, title, or message: fails after both inserts
        with self.assertRaises(KeyError):
            database.save_reading(row(sample_id="boat-a1-2"), [broken_alert])
        for path, table in ((config.READINGS_DATABASE, "readings"), (config.MISSIONS_DATABASE, "reading_details")):
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM {}".format(table)).fetchone()[0], 0, table)
        # And the boat's resend of it is then stored normally.
        self.assertTrue(database.save_reading(row(sample_id="boat-a1-2"))[1])


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        use_scratch_database(self)

    def test_both_files_use_a_rollback_journal(self):
        # WAL would make a commit to both files atomic in each file but not
        # across the two.
        for path in (config.READINGS_DATABASE, config.MISSIONS_DATABASE):
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0].lower(), "delete")

    def test_a_failed_write_is_rolled_back(self):
        with self.assertRaises(RuntimeError):
            with database._connect() as db:
                db.execute("INSERT INTO missions (name, started_at) VALUES ('half done', 'now')")
                raise RuntimeError("boom")
        self.assertEqual(database.list_missions(), [])


if __name__ == "__main__":
    unittest.main()
