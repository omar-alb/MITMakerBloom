"""The HTTP API end to end, the dashboard document, and the CSV export."""

import csv
import importlib.util
import io
import json
import re
import unittest
from unittest import mock

from helpers import HERE, NOW, Constant, install_model, mission, payload, stored, use_rules_only
from helpers import use_scratch_database, write_model

import analysis
import config
import database
import processing
import routes

RISKY = {"ph": 1380.0, "temperature": 790.0, "turbidity": 1200.0, "conductivity": 2100.0}


def document(rows, **options):
    return routes.dashboard_document(options.pop("mission", mission()), rows, now=NOW, **options)


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        use_scratch_database(self)
        self.model_folder = use_rules_only(self)
        self.mission = database.start_mission()
        routes.app.logger.disabled = True  # the 500 test logs a traceback on purpose
        self.addCleanup(setattr, routes.app.logger, "disabled", False)
        self.client = routes.app.test_client()

    def post(self, body):
        response = self.client.post("/readings", json=body)
        return response.status_code, response.get_json()


class ReadingRouteTests(ApiTestCase):
    def test_a_reading_is_stored(self):
        status, body = self.post(payload(sample_id="boat-1-1"))
        self.assertEqual((status, body["status"]), (200, "saved"))
        self.assertEqual(database.count_readings(self.mission["id"]), 1)

    def test_a_resent_reading_gets_a_200_but_is_not_stored_twice(self):
        # Anything but 2xx and the boat would keep resending it forever.
        self.post(payload(sample_id="boat-1-2"))
        status, body = self.post(payload(sample_id="boat-1-2"))
        self.assertEqual((status, body["status"]), (200, "duplicate"))
        self.assertEqual(database.count_readings(self.mission["id"]), 1)

    def test_a_risky_reading_stores_its_alerts(self):
        self.post(payload(millivolts=RISKY))
        kinds = [alert["kind"] for alert in database.mission_alerts(self.mission["id"])]
        self.assertEqual(kinds, ["bloom", "turbidity"])

    def test_a_bad_reading_is_a_400_with_the_reason(self):
        for body in (payload(device=None), payload(gps={"status": "fix", "latitude": 95.0, "longitude": 0.0}), [], {}):
            with self.subTest(body=body):
                status, response = self.post(body)
                self.assertEqual(status, 400)
                self.assertTrue(response["error"])

    def test_a_body_that_is_not_json_is_a_400(self):
        response = self.client.post("/readings", data="nope", content_type="text/plain")
        self.assertEqual(response.status_code, 400)


class DashboardRouteTests(ApiTestCase):
    def test_the_dashboard_shows_the_active_mission(self):
        self.post(payload())
        body = self.client.get("/dashboard").get_json()
        self.assertEqual(body["mission"]["id"], self.mission["id"])
        self.assertEqual(body["mission"]["readingCount"], 1)
        self.assertEqual(len(body["cells"]), 1)

    def test_any_mission_can_be_opened_by_id(self):
        self.post(payload())
        database.start_mission()
        body = self.client.get("/missions/{}/dashboard".format(self.mission["id"])).get_json()
        self.assertEqual((body["mission"]["id"], body["mission"]["active"]), (self.mission["id"], False))
        self.assertEqual(self.client.get("/missions/9999/dashboard").status_code, 404)

    def test_with_no_mission_at_all_the_dashboard_is_empty(self):
        database.end_mission()
        body = self.client.get("/dashboard").get_json()
        self.assertIsNone(body["mission"])
        self.assertEqual(body["summary"]["basis"], "none")

    def test_the_page_and_its_files_are_served(self):
        for path in ("/", "/dashboard.js", "/style.css", "/vendor/leaflet/leaflet.js"):
            with self.subTest(path=path), self.client.get(path) as response:
                self.assertEqual(response.status_code, 200)

    def test_the_dashboard_gets_every_setting_it_uses_from_config(self):
        with self.client.get("/config.js") as response:
            self.assertEqual(response.mimetype, "text/javascript")
            text = response.get_data(as_text=True)
        prefix = "window.BLOOM_SETTINGS = "
        self.assertTrue(text.startswith(prefix))
        settings = json.loads(text[len(prefix):].strip().rstrip(";"))

        script = (config.WEB_DIR / "dashboard.js").read_text(encoding="utf-8")
        used = set(re.findall(r"SETTINGS\.(\w+)", script))
        self.assertTrue(used, "dashboard.js reads no settings?")
        self.assertEqual(used - set(settings), set(), "dashboard.js uses settings /config.js does not send")
        self.assertEqual(settings["maxZoom"], config.MAP_MAX_ZOOM)
        self.assertEqual(settings["shadingRadiusMeters"], config.SHADING_RADIUS_METERS)

    def test_the_dashboard_never_reaches_the_internet(self):
        # The Pi's network has no internet, and data costs: no map tiles, no
        # fonts, no scripts from anywhere else. (The SVG namespace in the
        # page's icon is a name, not a download.)
        for name in ("index.html", "dashboard.js", "style.css"):
            with self.subTest(file=name):
                text = (config.WEB_DIR / name).read_text(encoding="utf-8").replace("http://www.w3.org/2000/svg", "")
                self.assertNotRegex(text, r"https?://|openstreetmap")


class MissionRouteTests(ApiTestCase):
    def test_starting_and_naming_missions(self):
        response = self.client.post("/missions", json={"name": "Inlet transect"})
        self.assertEqual((response.status_code, response.get_json()["name"]), (201, "Inlet transect"))
        self.assertEqual(self.client.post("/missions", json={"name": "inlet TRANSECT"}).status_code, 409)
        self.assertEqual(self.client.post("/missions", json={"name": 42}).status_code, 400)
        self.assertEqual(self.client.post("/missions", json=["x"]).status_code, 400)
        self.assertEqual(self.client.post("/missions").status_code, 201)

    def test_missions_are_listed_newest_first(self):
        self.client.post("/missions", json={"name": "Second"})
        names = [mission["name"] for mission in self.client.get("/missions").get_json()]
        self.assertEqual(names, ["Second", self.mission["name"]])

    def test_ending_the_mission_twice_is_a_404_the_second_time(self):
        self.assertEqual(self.client.post("/missions/end").status_code, 200)
        self.assertEqual(self.client.post("/missions/end").status_code, 404)


class AlertRouteTests(ApiTestCase):
    def test_acknowledging_one_and_all(self):
        self.post(payload(millivolts=RISKY))
        first = database.mission_alerts(self.mission["id"])[0]

        self.assertEqual(self.client.post("/alerts/{}/acknowledge".format(first["id"])).status_code, 200)
        self.assertEqual(self.client.post("/alerts/987654/acknowledge").status_code, 404)
        self.assertEqual(self.client.post("/alerts/acknowledge").get_json()["count"], 1)


class ReplayRouteTests(ApiTestCase):
    def test_replay_shows_the_mission_as_it_stood_at_each_reading(self):
        self.post(payload(sample_id="boat-r-1"))
        self.post(payload(sample_id="boat-r-2", millivolts=RISKY))
        self.post(payload(sample_id="boat-r-3"))
        first, risky, last = (r["id"] for r in database.mission_readings(self.mission["id"]))
        # Acknowledged long after the replayed moment.
        with mock.patch.object(database, "_now", return_value="2099-01-01T00:00:00+00:00"):
            self.client.post("/alerts/acknowledge")

        def replay(reading_id):
            response = self.client.get("/missions/{}/replay/{}".format(self.mission["id"], reading_id))
            self.assertEqual(response.status_code, 200)
            return response.get_json()

        before, during, after = replay(first), replay(risky), replay(last)
        self.assertEqual([c["readings"] for c in (before["cells"] + during["cells"] + after["cells"])], [1, 2, 3])
        self.assertEqual((len(before["hotspots"]), len(during["hotspots"])), (0, 1))
        self.assertNotIn("bloom", [a["kind"] for a in before["alerts"]])
        self.assertIn("bloom", [a["kind"] for a in during["alerts"]])
        self.assertFalse(any(alert["acknowledged"] for alert in during["alerts"]), "acknowledged later: still open then")
        self.assertEqual(during["summary"]["risk"], analysis.rollup([during["cells"][0]["risk"]]))

    def test_an_unknown_mission_or_reading_is_a_404(self):
        self.post(payload())
        reading_id = database.mission_readings(self.mission["id"])[0]["id"]
        other = database.start_mission()
        for url in ("/missions/987/replay/{}".format(reading_id),
                    "/missions/{}/replay/987".format(self.mission["id"]),
                    "/missions/{}/replay/{}".format(other["id"], reading_id)):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)


class ExportRouteTests(ApiTestCase):
    def test_the_whole_mission_downloads_as_csv(self):
        for index in range(3):
            self.post(payload(sample_id="s-{}".format(index), gps={"status": "fix", "latitude": 42.28 + index / 1000, "longitude": -83.74}))
        self.post(payload(sample_id="s-nofix", gps={"status": "no_fix"}))

        response = self.client.get("/missions/{}/readings.csv".format(self.mission["id"]))

        self.assertEqual((response.status_code, response.mimetype), (200, "text/csv"))
        self.assertIn("attachment", response.headers["Content-Disposition"])
        rows = list(csv.DictReader(io.StringIO(response.get_data().decode("utf-8-sig"))))
        self.assertEqual([row["Sample ID"] for row in rows], ["s-0", "s-1", "s-2", "s-nofix"])
        self.assertTrue(all(row["Cell"] for row in rows[:3]))
        self.assertEqual((rows[3]["Latitude"], rows[3]["Cell"], rows[3]["GPS"]), ("", "", "no_fix"))
        self.assertEqual(self.client.get("/missions/9999/readings.csv").status_code, 404)


class ModelAndErrorRouteTests(ApiTestCase):
    @unittest.skipUnless(importlib.util.find_spec("numpy"), "numpy is not installed")
    def test_model_status_and_reload(self):
        self.assertEqual(self.client.get("/model").get_json()["scoredBy"], "rules-v2")
        write_model(self.model_folder, Constant(0.5), version="api-v1")
        with mock.patch("builtins.print"):
            reloaded = self.client.post("/model/reload").get_json()
        self.assertEqual((reloaded["model"], reloaded["scoredBy"]), (True, "api-v1"))
        self.assertEqual(reloaded["features"], ["temperature", "ph", "turbidity", "conductivity"])

        self.assertEqual(self.post(payload())[0], 200)
        self.assertEqual(database.mission_readings(self.mission["id"])[-1]["scored_by"], "api-v1")

    def test_errors_are_json_not_html(self):
        # The boat and the dashboard read every answer as JSON.
        for method, path, status in (("get", "/no-such-page", 404), ("delete", "/readings", 405)):
            with self.subTest(path=path):
                response = getattr(self.client, method)(path)
                self.assertEqual(response.status_code, status)
                self.assertTrue(response.is_json)

    def test_an_unexpected_failure_is_a_json_500(self):
        with mock.patch.object(database, "active_mission", side_effect=RuntimeError("disk on fire")):
            response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 500)
        self.assertIn("disk on fire", response.get_json()["error"])

    def test_shutdown_answers_before_stopping(self):
        with mock.patch("threading.Timer") as timer:
            response = self.client.post("/shutdown")
        self.assertEqual(response.status_code, 202)
        timer.return_value.start.assert_called_once()


class DocumentTests(unittest.TestCase):
    def setUp(self):
        use_rules_only(self)

    def test_a_reading_carries_its_values_and_its_risk(self):
        reading = document([stored(1, risk=64)])["readings"][0]

        self.assertEqual(reading["id"], 1)
        self.assertEqual((reading["lat"], reading["lng"]), HERE)
        self.assertEqual(reading["temperature"], 22.4)
        self.assertEqual((reading["risk"], reading["level"], reading["alert"]), (64, "High", True))
        self.assertIsNotNone(reading["cell"])
        # Small on purpose: 500 of these go out on every poll.
        self.assertLess(len(json.dumps(reading)), 500)

    def test_the_sensors_and_the_ladder_come_from_config(self):
        body = document([])
        self.assertEqual([sensor["key"] for sensor in body["sensors"]], [sensor.key for sensor in config.SENSORS])
        self.assertEqual([level["label"] for level in body["riskLevels"]], ["Low", "Moderate", "High", "Severe"])

    def test_cells_cover_the_whole_mission_but_only_the_newest_readings_are_sent(self):
        early = [stored(index, risk=90, latitude=42.30, seconds_ago=3600) for index in range(1, 4)]
        late = [stored(index, risk=10, seconds_ago=60) for index in range(4, 7)]
        with mock.patch.object(config, "DASHBOARD_READINGS", 3):
            body = document(early + late, reading_count=6)

        self.assertEqual([reading["id"] for reading in body["readings"]], [4, 5, 6])
        self.assertEqual(len(body["cells"]), 2)
        self.assertEqual(body["mission"]["readingCount"], 6)
        self.assertGreater(body["summary"]["risk"], 50)

    def test_readings_without_a_fix_count_but_are_not_mapped(self):
        body = document([stored(1, risk=60, latitude=None)])
        self.assertEqual(body["cells"], [])
        self.assertIsNone(body["readings"][0]["cell"])

    def test_no_mission_and_an_empty_mission_both_render(self):
        for body in (routes.dashboard_document(None, [], now=NOW), document([])):
            self.assertEqual(body["summary"]["basis"], "none")
            self.assertFalse(body["boat"]["online"])
            self.assertEqual(body["readings"], [])

    def test_the_document_is_strict_json(self):
        json.dumps(document([stored(1), stored(2, latitude=None)]), allow_nan=False)


class OneNumberTests(unittest.TestCase):
    """Every view is built from the one stored risk; nothing scores twice."""

    def setUp(self):
        self.model_folder = use_rules_only(self)

    def test_building_the_dashboard_never_scores_anything(self):
        rows = [stored(index, risk=40 + index, latitude=42.28 + index / 1000) for index in range(1, 5)]
        with mock.patch.object(processing, "score", side_effect=AssertionError("re-scored")), \
                mock.patch.object(processing, "score_with_rules", side_effect=AssertionError("re-scored")):
            body = document(rows)
        self.assertEqual(len(body["cells"]), 4)

    def test_a_cell_is_the_rollup_of_the_readings_inside_it(self):
        body = document([stored(1, risk=90), stored(2, risk=10)])
        cell = body["cells"][0]
        inside = [reading["risk"] for reading in body["readings"] if reading["cell"] == cell["id"]]
        self.assertEqual(sorted(inside), [10, 90])
        self.assertEqual(cell["risk"], analysis.rollup(inside))

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "numpy is not installed")
    def test_a_model_moves_every_view_together(self):
        install_model(self.model_folder, Constant(0.9), version="hot-v1")

        row = {**processing.process(payload(), now=NOW), "id": 1}
        body = document([row])

        self.assertEqual(row["risk"], 90)
        self.assertEqual(body["readings"][0]["risk"], 90)
        self.assertEqual(body["cells"][0]["risk"], 90)
        self.assertEqual(body["summary"]["risk"], 90)
        self.assertEqual(body["hotspots"][0]["risk"], 90)
        bloom = analysis.alerts_for(row)[0]
        self.assertEqual((bloom["kind"], bloom["level"]), ("bloom", "Severe"))
        self.assertIn("90/100", bloom["message"])
        self.assertEqual(body["model"], {"model": True, "scoredBy": "hot-v1"})


class CsvTests(unittest.TestCase):
    def test_every_reading_exports_with_its_cell(self):
        rows = [
            stored(1, risk=30, millivolts={"ph": 1450.0}),
            stored(2, risk=70, millivolts={"ph": 1300.0}),
            stored(3, latitude=None, millivolts={}),
        ]
        text = "".join(routes.csv_lines(mission(), iter(rows), rows))

        self.assertTrue(text.startswith("﻿"), "a byte-order mark keeps Excel on UTF-8")
        exported = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
        self.assertEqual([row["Reading"] for row in exported], ["1", "2", "3"])
        self.assertEqual(exported[0]["Cell"], analysis.cell_id(HERE[0], HERE[1], HERE[0]))
        self.assertEqual(exported[0]["Cell risk"], str(analysis.rollup([30, 70])))
        self.assertEqual(exported[1]["pH mV"], "1300.0")
        self.assertEqual((exported[2]["Latitude"], exported[2]["Cell"]), ("", ""))
        self.assertIn("Temperature (°C)", exported[0])


if __name__ == "__main__":
    unittest.main()
