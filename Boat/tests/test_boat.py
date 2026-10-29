"""The boat's loop: probes, sending, and what goes over the wire."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import main  # noqa: E402
import outbox  # noqa: E402
import sensors  # noqa: E402
import simulation  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_simulation_ships_off(self):
        self.assertFalse(
            config.SIMULATE_HARDWARE,
            "SIMULATE_HARDWARE must ship False, or invented readings get recorded as field data.",
        )

    def test_only_the_wired_probes_are_listed(self):
        # An unconnected input floats, and the server would turn the noise
        # into a real-looking nutrient reading and raise alerts on it.
        self.assertEqual(set(config.PROBES), {"ph", "temperature", "turbidity", "conductivity"})

    def test_every_probe_is_on_its_own_input_of_a_configured_board(self):
        inputs = list(config.PROBES.values())
        self.assertEqual(len(inputs), len(set(inputs)))
        for board, channel in inputs:
            self.assertLess(board, len(config.ADC_BOARDS))
            self.assertIn(channel, (0, 1, 2, 3))


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, sensors, "_boards", None)
        printer = mock.patch("builtins.print")
        printer.start()
        self.addCleanup(printer.stop)

    def boards(self, *readers):
        sensors._boards = [mock.Mock(read_volts=reader) for reader in readers]

    def test_every_wired_probe_is_read_in_millivolts(self):
        self.boards(lambda channel: 1.0 + channel / 10, lambda channel: 9.9)
        millivolts, hardware = sensors.read_probes()
        self.assertEqual(millivolts, {"ph": 1000.0, "temperature": 1100.0, "turbidity": 1200.0, "conductivity": 1300.0})
        self.assertEqual(hardware, "ok")

    def test_a_probe_that_does_not_answer_is_left_out_not_the_whole_reading(self):
        def flaky(channel):
            if channel == 2:
                raise OSError("I2C error")
            return 1.5

        self.boards(flaky, flaky)
        millivolts, hardware = sensors.read_probes()
        self.assertNotIn("turbidity", millivolts)
        self.assertEqual(len(millivolts), 3)
        self.assertEqual(hardware, "partial")
        self.assertIsNone(sensors._boards, "the bus is reopened next time")

    def test_no_probe_answering_is_an_error(self):
        def dead(channel):
            raise OSError("no ACK")

        self.boards(dead, dead)
        with self.assertRaises(RuntimeError):
            sensors.read_probes()

    def test_without_the_machine_module_the_probes_cannot_open(self):
        with self.assertRaises(RuntimeError):
            sensors.read_probes()


class Ads1115Tests(unittest.TestCase):
    def test_the_wait_matches_the_sample_rate(self):
        # A fixed short wait returned the previous channel's conversion at
        # low sample rates, so every probe read the one before it.
        waits = {}
        for rate in (8, 860):
            bus = mock.Mock()
            bus.readfrom.return_value = bytes([0x80, 0x00])  # conversion done
            adc = sensors.ADS1115(bus, 0x48, samples_per_second=rate)
            sleeps = []
            adc._sleep_ms = sleeps.append
            adc.read_volts(0)
            waits[rate] = sleeps[0]
        self.assertGreaterEqual(waits[8], 125)
        self.assertLess(waits[860], 10)

    def test_a_negative_count_reads_as_negative_volts(self):
        bus = mock.Mock()
        bus.readfrom.return_value = bytes([0xFF, 0xFF])
        self.assertLess(sensors.ADS1115(bus, 0x48).read_volts(1), 0)


class RequestBodyTests(unittest.TestCase):
    def test_private_outbox_fields_stay_on_the_boat(self):
        body = main.request_body(main.stamp({"device": "boat"}))
        self.assertNotIn("_boot", body)
        self.assertNotIn("_taken_ms", body)
        self.assertTrue(body["sample_id"].startswith(config.DEVICE_NAME + "-" + main.BOOT_ID + "-"))

    def test_a_waiting_reading_says_how_long_ago_it_was_taken(self):
        reading = main.stamp({})
        reading["_taken_ms"] -= 42_000
        self.assertAlmostEqual(main.request_body(reading)["age_seconds"], 42.0, delta=1.0)

    def test_a_reading_from_before_a_reboot_sends_no_age(self):
        # The tick counter restarted, so its age is unknown; the server then
        # uses the arrival time rather than a wrong one.
        reading = main.stamp({})
        reading["_boot"] = "an-earlier-boot"
        self.assertNotIn("age_seconds", main.request_body(reading))

    def test_sample_ids_are_unique(self):
        self.assertEqual(len({main.stamp({})["sample_id"] for _ in range(50)}), 50)

    def test_a_simulated_reading_has_every_probe_and_a_position(self):
        with mock.patch.object(config, "SIMULATE_HARDWARE", True):
            reading = main.take_reading()
        self.assertEqual(set(reading["millivolts"]), set(config.PROBES))
        self.assertEqual((reading["hardware"], reading["gps"]["status"]), ("simulated", "simulated"))
        self.assertTrue(set(simulation.MILLIVOLTS) >= set(config.PROBES))


class SendTests(unittest.TestCase):
    def send_with_status(self, status):
        with mock.patch.object(main, "requests", None), \
                mock.patch.object(main, "_post", return_value=status), mock.patch("builtins.print"):
            return main.send({"sample_id": "boat-x-1"})

    def test_2xx_is_sent(self):
        self.assertEqual(self.send_with_status(200), outbox.SENT)

    def test_a_refused_reading_is_not_retried_forever(self):
        for status in (400, 404, 413):
            self.assertEqual(self.send_with_status(status), outbox.REJECTED)

    def test_server_trouble_and_try_again_later_are_retried(self):
        for status in (500, 503, 408, 429):
            self.assertEqual(self.send_with_status(status), outbox.FAILED)

    def test_a_network_error_is_retried(self):
        with mock.patch.object(main, "_post", side_effect=OSError("host unreachable")), mock.patch("builtins.print"):
            self.assertEqual(main.send({}), outbox.FAILED)


class CycleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for patcher in (
            mock.patch.object(config, "OUTBOX_FILE", str(Path(directory.name) / "outbox.jsonl")),
            mock.patch("builtins.print"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_outbox_still_empties_when_the_probes_fail(self):
        outbox.add({"n": 1})
        outbox.add({"n": 2})
        with mock.patch.object(main, "take_reading", side_effect=RuntimeError("No probe answered.")), \
                mock.patch.object(main, "send", return_value=outbox.SENT):
            self.assertTrue(main.run_cycle())
        self.assertEqual(outbox.count(), 0)

    def test_without_wifi_readings_wait_in_the_outbox(self):
        with mock.patch.object(main, "take_reading", return_value={"device": "boat"}), \
                mock.patch.object(main, "wifi_ready", return_value=False):
            self.assertFalse(main.run_cycle())
            self.assertFalse(main.run_cycle())
        self.assertEqual(outbox.count(), 2)


if __name__ == "__main__":
    unittest.main()
