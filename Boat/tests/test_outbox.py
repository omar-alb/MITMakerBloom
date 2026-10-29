"""The outbox: readings on flash, sent oldest-first, never lost or blocked."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import outbox  # noqa: E402


class OutboxTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.file = Path(directory.name) / "outbox.jsonl"
        self.copy = Path(str(self.file) + ".tmp")
        for patcher in (mock.patch.object(config, "OUTBOX_FILE", str(self.file)), mock.patch("builtins.print")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def add(self, count):
        for number in range(count):
            self.assertTrue(outbox.add({"n": number}))

    def waiting(self):
        if not self.file.exists():
            return []
        return [json.loads(line)["n"] for line in self.file.read_text().splitlines() if line.strip()]

    def send_all(self, outcomes, budget_ms=None):
        """Send with a fake server; `outcomes` maps reading number to outcome."""
        sent = []

        def send(reading):
            outcome = outcomes(reading["n"])
            if outcome == outbox.SENT:
                sent.append(reading["n"])
            return outcome

        return outbox.send_all(send, budget_ms=budget_ms), sent

    def test_everything_is_sent_in_order_and_the_file_removed(self):
        self.add(60)
        emptied, sent = self.send_all(lambda n: outbox.SENT)
        self.assertTrue(emptied)
        self.assertEqual(sent, list(range(60)))
        self.assertFalse(self.file.exists())
        self.assertEqual(outbox.count(), 0)

    def test_an_outage_keeps_the_rest_in_order(self):
        self.add(40)
        emptied, _ = self.send_all(lambda n: outbox.SENT if n < 30 else outbox.FAILED)
        self.assertFalse(emptied)
        self.assertEqual(self.waiting(), list(range(30, 40)))
        self.assertEqual(outbox.count(), 10)

    def test_a_failed_first_attempt_does_not_rewrite_the_file(self):
        # Rewriting flash on every failed attempt wears the board out.
        self.add(3)
        with mock.patch.object(outbox, "_drop_oldest") as drop:
            self.send_all(lambda n: outbox.FAILED)
        drop.assert_not_called()

    def test_a_refused_reading_does_not_block_the_ones_behind_it(self):
        self.add(3)
        emptied, sent = self.send_all(lambda n: outbox.REJECTED if n == 1 else outbox.SENT)
        self.assertTrue(emptied)
        self.assertEqual(sent, [0, 2])

    def test_a_corrupt_line_is_skipped(self):
        self.file.write_text('{"n": 0}\nnot json\n\n[1, 2]\n{"n": 2}\n')
        emptied, sent = self.send_all(lambda n: outbox.SENT)
        self.assertEqual((emptied, sent), (True, [0, 2]))

    def test_a_long_backlog_drains_over_several_cycles(self):
        self.add(10)
        clock = iter(range(0, 100_000, 1000))  # every send "takes" a second
        with mock.patch.object(outbox, "ticks_ms", side_effect=lambda: next(clock)):
            emptied, _ = self.send_all(lambda n: outbox.SENT, budget_ms=3000)
        self.assertFalse(emptied)
        self.assertEqual(self.waiting(), list(range(3, 10)))

    def test_a_full_outbox_drops_its_oldest_readings(self):
        with mock.patch.object(config, "OUTBOX_MAX_BYTES", 200):
            self.add(40)
        waiting = self.waiting()
        self.assertEqual(waiting[-1], 39)
        self.assertNotIn(0, waiting)
        self.assertLess(len(waiting), 40)

    def test_a_rewrite_cut_short_after_the_delete_is_finished_at_boot(self):
        self.copy.write_text('{"n": 7}\n')
        outbox.recover()
        self.assertEqual(self.waiting(), [7])
        self.assertFalse(self.copy.exists())

    def test_a_rewrite_cut_short_before_the_delete_is_undone_at_boot(self):
        self.add(2)
        self.copy.write_text('{"n": 1}\n')
        outbox.recover()
        self.assertEqual(self.waiting(), [0, 1])
        self.assertFalse(self.copy.exists())

    def test_the_file_is_copied_into_place_when_rename_is_refused(self):
        self.add(3)
        with mock.patch.object(outbox.os, "rename", side_effect=OSError("EEXIST")):
            outbox._drop_oldest(1)
        self.assertEqual(self.waiting(), [1, 2])
        self.assertFalse(self.copy.exists())


if __name__ == "__main__":
    unittest.main()
