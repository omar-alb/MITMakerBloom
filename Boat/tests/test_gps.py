"""The GPS: NMEA parsing and what the boat reports with and without a fix.

Runs under desktop Python: the parsing and fallback logic is what decides
whether a reading is mapped at the right place, the wrong place, or not at all.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import gps  # noqa: E402


def nmea(body):
    """A sentence with its checksum: the XOR of every character between $ and *."""
    checksum = 0
    for character in body:
        checksum ^= ord(character)
    return "${}*{:02X}".format(body, checksum)


LOCKED_GGA = nmea("GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,")


class FakeUart:
    """Hands out queued chunks the way a non-blocking UART read() does."""

    def __init__(self, chunks):
        self.chunks = list(chunks)

    def read(self):
        return self.chunks.pop(0) if self.chunks else None


class ParsingTests(unittest.TestCase):
    def test_a_locked_gga_sentence_gives_decimal_degrees(self):
        latitude, longitude = gps.parse_sentence(LOCKED_GGA)
        self.assertAlmostEqual(latitude, 48.1173, places=4)
        self.assertAlmostEqual(longitude, 11.5167, places=4)

    def test_the_published_example_passes_its_checksum(self):
        self.assertEqual(LOCKED_GGA, "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47")

    def test_a_sentence_without_a_lock_gives_no_position(self):
        # Some receivers repeat their last known position while unlocked;
        # taking it would map stale coordinates as live ones.
        self.assertIsNone(gps.parse_sentence(nmea("GPGGA,123519,4807.038,N,01131.000,E,0,00,,545.4,M,46.9,M,,")))
        self.assertIsNone(gps.parse_sentence(nmea("GPRMC,123519,V,4807.038,N,01131.000,E,022.4,084.4,230394,,")))

    def test_southern_and_western_hemispheres_are_negative(self):
        latitude, longitude = gps.parse_sentence(nmea("GNRMC,123519,A,4807.038,S,01131.000,W,022.4,084.4,230394,,"))
        self.assertLess(latitude, 0)
        self.assertLess(longitude, 0)

    def test_noise_and_other_sentences_are_ignored(self):
        self.assertIsNone(gps.parse_sentence("garbage from a floating rx pin"))
        self.assertIsNone(gps.parse_sentence(nmea("GPVTG,054.7,T,034.4,M,005.5,N")))
        self.assertIsNone(gps.parse_sentence(""))

    def test_a_corrupted_digit_fails_the_checksum(self):
        # Still a believable position, so only the checksum catches it.
        self.assertIsNone(gps.parse_sentence(LOCKED_GGA.replace("4807.038", "4807.938")))

    def test_a_sentence_cut_off_before_its_checksum_is_ignored(self):
        self.assertIsNone(gps.parse_sentence("$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4"))

    def test_a_garbled_coordinate_is_dropped(self):
        for body in (
            "GPGGA,123519,9907.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,",  # 99 degrees latitude
            "GPGGA,123519,4807.038,N,18107.038,E,1,08,0.9,545.4,M,46.9,M,,",  # 181 degrees longitude
            "GPGGA,123519,4875.000,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,",  # 75 minutes
            "GPGGA,123519,4807.038,X,01131.000,E,1,08,0.9,545.4,M,46.9,M,,",  # no hemisphere
        ):
            with self.subTest(body=body):
                self.assertIsNone(gps.parse_sentence(nmea(body)))


class ReadPositionTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("UART", gps.UART), ("Pin", gps.Pin), ("_uart", None), ("_buffer", b""),
                            ("_last_fix", None), ("_last_fix_ms", None), ("_warned", False)):
            self.addCleanup(setattr, gps, name, getattr(gps, name))
            setattr(gps, name, value)
        for name, pin in (("GPS_RX_PIN", 16), ("GPS_TX_PIN", 17)):
            patcher = mock.patch.object(config, name, pin)
            patcher.start()
            self.addCleanup(patcher.stop)
        printer = mock.patch("builtins.print")
        printer.start()
        self.addCleanup(printer.stop)

    def read_from(self, uart):
        gps.UART = lambda *args, **kwargs: uart
        gps.Pin = lambda number: number
        with mock.patch.object(gps.time, "sleep"):
            return gps.read_position()

    def test_a_fix_is_reported_with_its_position(self):
        position = self.read_from(FakeUart([None, (LOCKED_GGA + "\r\n").encode()]))
        self.assertEqual(position["status"], "fix")
        self.assertAlmostEqual(position["latitude"], 48.1173, places=4)

    def test_unset_pins_mean_no_gps_and_the_uart_is_never_opened(self):
        # The UART's default pins belong to the flash chip on ESP32 modules.
        uart = mock.Mock()
        gps.UART = uart
        with mock.patch.object(config, "GPS_RX_PIN", None):
            position = gps.read_position()
        uart.assert_not_called()
        self.assertEqual(position, {"status": "unavailable", "latitude": None, "longitude": None})

    def test_no_uart_at_all_means_no_gps(self):
        gps.UART = None
        self.assertEqual(gps.read_position()["status"], "unavailable")

    def test_a_uart_that_cannot_open_means_no_gps(self):
        gps.UART = mock.Mock(side_effect=OSError("uart busy"))
        gps.Pin = lambda number: number
        self.assertEqual(gps.read_position()["status"], "unavailable")

    def test_a_sentence_split_across_reads_is_put_back_together(self):
        line = (LOCKED_GGA + "\r\n").encode()
        self.assertEqual(self.read_from(FakeUart([None, line[:25], line[25:]]))["status"], "fix")

    def test_sentences_buffered_since_the_last_reading_are_thrown_away(self):
        stale = nmea("GPGGA,120000,4000.000,N,07000.000,W,1,08,0.9,10.0,M,0.0,M,,") + "\r\n"
        position = self.read_from(FakeUart([stale.encode(), None, (LOCKED_GGA + "\r\n").encode()]))
        self.assertAlmostEqual(position["latitude"], 48.1173, places=4)

    def test_a_noisy_line_is_skipped(self):
        noise = b"\xff\xfe$GP\x80\r\n"
        self.assertEqual(self.read_from(FakeUart([None, noise + (LOCKED_GGA + "\r\n").encode()]))["status"], "fix")

    def test_no_lock_and_no_recent_fix_means_no_position(self):
        with mock.patch.object(config, "GPS_READ_TIMEOUT_MS", 0):
            self.assertEqual(self.read_from(FakeUart([]))["status"], "no_fix")

    def lose_fix(self, seconds_ago):
        gps._last_fix = gps.parse_sentence(LOCKED_GGA)
        gps._last_fix_ms = gps.ticks_ms() - seconds_ago * 1000
        with mock.patch.object(config, "GPS_READ_TIMEOUT_MS", 0):
            return self.read_from(FakeUart([]))

    def test_a_short_dropout_reuses_the_last_fix_as_stale(self):
        position = self.lose_fix(10)
        self.assertEqual(position["status"], "stale")
        self.assertAlmostEqual(position["latitude"], 48.1173, places=4)

    def test_a_long_dropout_stops_reusing_the_old_position(self):
        # The boat has moved on; every reading would be mapped at the old spot.
        position = self.lose_fix(config.GPS_STALE_LIMIT_SECONDS + 5)
        self.assertEqual((position["status"], position["latitude"]), ("no_fix", None))


if __name__ == "__main__":
    unittest.main()
