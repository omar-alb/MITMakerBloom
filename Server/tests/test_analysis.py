"""What the stored readings add up to: levels, alerts, cells, hotspots, the headline, the boat."""

import unittest

from helpers import HERE, NOW, stored

import analysis
import config


def overview(readings):
    return analysis.overview(readings, HERE[0], now=NOW)


class LadderTests(unittest.TestCase):
    def test_every_score_lands_on_exactly_one_level(self):
        mins = [rung["min"] for rung in config.RISK_LEVELS]
        self.assertEqual(mins[0], 0)
        self.assertEqual(mins, sorted(set(mins)))
        self.assertEqual(analysis.level(0), "Low")
        self.assertEqual(analysis.level(100), "Severe")

    def test_a_fraction_lands_where_it_is_shown(self):
        # Rounded first, so 50.4 is Moderate everywhere, never High somewhere.
        self.assertEqual(analysis.level(50.4), "Moderate")
        self.assertEqual(analysis.level(50.6), "High")
        for tenths in range(0, 1001):
            self.assertEqual(analysis.level(tenths / 10), analysis.level(round(tenths / 10)))

    def test_alert_levels_are_levels_on_the_ladder(self):
        labels = [rung["label"] for rung in config.RISK_LEVELS]
        self.assertTrue(set(config.ALERT_LEVELS) <= set(labels))
        self.assertIn(config.SENSOR_ALERT_LEVEL, labels)
        self.assertFalse(analysis.is_alert(50))
        self.assertTrue(analysis.is_alert(51))

    def test_a_rollup_blends_the_mean_with_the_peak(self):
        self.assertEqual(analysis.rollup([]), 0)
        self.assertEqual(analysis.rollup([40]), 40)
        self.assertEqual(analysis.rollup([10, 90]), round(50 + 40 * config.ROLLUP_PEAK_SHARE))


class AlertTests(unittest.TestCase):
    def test_a_bloom_alert_quotes_the_risk_that_raised_it(self):
        raised = analysis.alerts_for(stored(1, risk=92, drivers=["warm water", "high pH"]))
        self.assertEqual([alert["kind"] for alert in raised], ["bloom"])
        self.assertEqual(raised[0]["level"], "Severe")
        self.assertIn("92/100", raised[0]["message"])
        self.assertIn("driven by warm water and high pH", raised[0]["message"])

    def test_a_quiet_reading_raises_nothing(self):
        self.assertEqual(analysis.alerts_for(stored(1, risk=50)), [])

    def test_an_unlocated_alert_says_so(self):
        self.assertIn("(no GPS fix)", analysis.alerts_for(stored(1, risk=90, latitude=None))[0]["message"])

    def test_sensor_alerts_fire_at_their_thresholds(self):
        threshold = next(sensor.alert_at for sensor in config.SENSORS if sensor.key == "turbidity")
        self.assertEqual([a["kind"] for a in analysis.alerts_for(stored(1, turbidity=threshold))], ["turbidity"])
        self.assertEqual(analysis.alerts_for(stored(1, turbidity=threshold - 0.1)), [])
        self.assertEqual(analysis.alerts_for(stored(1, nitrate=None, ammonium=None)), [])


class CellTests(unittest.TestCase):
    def test_cells_stay_square_wherever_the_boat_launches(self):
        for latitude, longitude in ((42.36, -71.06), (47.61, -122.33), (-33.86, 151.21)):
            key = analysis.cell_id(latitude, longitude, latitude)
            (south, west), (north, east) = analysis.cell_bounds(key, latitude)
            height = analysis.distance_m(south, west, north, west)
            width = analysis.distance_m(south, west, south, east)
            self.assertAlmostEqual(height, config.CELL_SIZE_METERS, delta=0.5)
            self.assertAlmostEqual(width, config.CELL_SIZE_METERS, delta=0.5)

    def test_a_cell_contains_the_readings_that_made_it(self):
        cell = analysis.build_cells([stored(1)], HERE[0])[0]
        (south, west), (north, east) = cell["bounds"]
        self.assertTrue(south <= HERE[0] <= north and west <= HERE[1] <= east)

    def test_a_cell_is_the_rollup_of_the_readings_inside_it(self):
        cell = analysis.build_cells([stored(1, risk=90), stored(2, risk=10)], HERE[0])[0]
        self.assertEqual((cell["risk"], cell["peak"], cell["readings"]), (analysis.rollup([90, 10]), 90, 2))
        self.assertEqual(cell["level"], analysis.level(cell["risk"]))


class HotspotTests(unittest.TestCase):
    def test_nearby_alert_level_readings_form_one_hotspot_and_quiet_ones_none(self):
        near = config.HOTSPOT_JOIN_METERS / 2 / analysis.METERS_PER_DEGREE_LATITUDE  # half the join distance
        rows = [
            stored(1, risk=80, latitude=42.2800),
            stored(2, risk=70, latitude=42.2800 + near),
            stored(3, risk=90, latitude=42.3500),
            stored(4, risk=10, latitude=42.2800 + near / 2),
        ]
        spots = analysis.build_hotspots(rows, NOW)

        self.assertEqual(sorted(spot["readings"] for spot in spots), [1, 2])
        self.assertEqual({spot["level"] for spot in spots}, {"Severe"})
        self.assertTrue(all(spot["radius"] >= config.HOTSPOT_MIN_RADIUS_METERS for spot in spots))

    def test_hotspots_are_ordered_for_revisiting_and_the_first_few_numbered(self):
        rows = [stored(index, risk=90, latitude=42.28 + index * 0.01, seconds_ago=60) for index in range(8)]
        spots = analysis.build_hotspots(rows, NOW)

        priorities = [spot["priority"] for spot in spots]
        self.assertEqual(priorities, sorted(priorities, reverse=True))
        self.assertEqual([spot["revisit"] for spot in spots], [1, 2, 3, 4, 5, None, None, None])
        # The boat is at the last reading, so that hotspot is the nearest.
        self.assertEqual(spots[-1]["distance"], 0)

    def test_the_reason_explains_the_priority(self):
        spot = analysis.build_hotspots([stored(1, risk=80, seconds_ago=1200)], NOW)[0]
        self.assertEqual(spot["reason"], "Severe (80/100), 72% confidence, 0 m from the boat, last sampled 20 min ago.")


class OverviewTests(unittest.TestCase):
    def test_the_headline_is_the_rollup_of_the_cells(self):
        summary = overview([stored(1, risk=80), stored(2, risk=20, latitude=42.29)])["summary"]
        self.assertEqual((summary["basis"], summary["counted"]), ("cells", 2))
        self.assertEqual(summary["risk"], analysis.rollup([80, 20]))
        self.assertEqual(summary["alertCells"], 1)
        self.assertEqual([level["count"] for level in summary["distribution"]], [1, 0, 0, 1])

    def test_without_any_fix_the_headline_rolls_up_the_readings(self):
        result = overview([stored(1, risk=60, latitude=None), stored(2, risk=30, latitude=None)])
        self.assertEqual((result["cells"], result["hotspots"]), ([], []))
        self.assertEqual(result["summary"]["basis"], "readings")
        self.assertEqual(result["summary"]["risk"], analysis.rollup([60, 30]))

    def test_an_empty_mission_has_a_quiet_overview(self):
        result = overview([])
        self.assertEqual((result["summary"]["basis"], result["summary"]["peak"]), ("none", None))
        self.assertFalse(result["boat"]["online"])

    def test_the_boat_is_online_while_it_is_being_heard_from(self):
        self.assertTrue(overview([stored(1, seconds_ago=5)])["boat"]["online"])
        self.assertFalse(overview([stored(1, seconds_ago=config.BOAT_OFFLINE_AFTER_SECONDS + 1)])["boat"]["online"])

    def test_a_backlog_arriving_now_counts_as_online(self):
        # Taken an hour ago, but sent just now from the outbox.
        late = stored(1, seconds_ago=3600, received_at=NOW.isoformat())
        self.assertTrue(overview([late])["boat"]["online"])

    def test_gps_and_probe_state_come_from_the_latest_reading(self):
        boat = overview([stored(1), stored(2, latitude=None, hardware="partial")])["boat"]
        self.assertEqual((boat["gps"], boat["hardware"]), ("no_fix", "partial"))


if __name__ == "__main__":
    unittest.main()
