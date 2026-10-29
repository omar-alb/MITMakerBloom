"""The boat and the server agree on what a reading looks like.

The boat runs on a separate device, so its probe names and payload are the
one thing this repository has to keep in step by hand. The boat code runs in
its own process here, so its config.py never meets the server's.
"""

import importlib.util
import json
import subprocess
import sys
import unittest

from helpers import BOAT_DIR, NOW, use_rules_only

import config
import processing

SERVER_SENSORS = {sensor.key for sensor in config.SENSORS}


def load_boat_file(name):
    spec = importlib.util.spec_from_file_location("boat_" + name, BOAT_DIR / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BoatContractTests(unittest.TestCase):
    def test_every_probe_the_boat_reads_is_a_sensor_the_server_converts(self):
        self.assertLessEqual(set(load_boat_file("config").PROBES), SERVER_SENSORS)

    def test_the_bench_simulation_covers_every_server_sensor(self):
        # So uncommenting a probe in Boat/config.py never breaks a bench demo.
        self.assertEqual(set(load_boat_file("simulation").MILLIVOLTS), SERVER_SENSORS)

    def test_a_reading_from_the_boat_code_is_stored_as_sent(self):
        use_rules_only(self)
        script = (
            "import json, config\n"
            "config.SIMULATE_HARDWARE = True\n"
            "import main\n"
            "print(json.dumps(main.request_body(main.stamp(main.take_reading()))))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=str(BOAT_DIR), capture_output=True, text=True, timeout=60
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout.strip().splitlines()[-1])

        row = processing.process(body, now=NOW)

        self.assertEqual(set(row["millivolts"]), set(load_boat_file("config").PROBES))
        self.assertEqual((row["gps"], row["hardware"]), ("simulated", "simulated"))
        self.assertIsNotNone(row["latitude"])
        self.assertTrue(row["sample_id"].startswith(load_boat_file("config").DEVICE_NAME + "-"))
        self.assertFalse(any(key.startswith("_") for key in body), "private outbox fields leaked")


if __name__ == "__main__":
    unittest.main()
