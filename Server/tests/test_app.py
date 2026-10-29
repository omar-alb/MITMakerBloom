"""Starting the server: on the Raspberry Pi with its Wi-Fi network, or on any computer with --dev.

Nothing here touches the real network: every command app.py would run is
recorded instead, and the web server is never actually started.
"""

import builtins
import subprocess
import unittest
from unittest import mock

from helpers import use_rules_only, use_scratch_database

import app
import config
import database


def worked(command, **_):
    """What subprocess.run returns for a command that succeeded."""
    return subprocess.CompletedProcess(command, 0, stdout="raspberrypi\n" if command == ["hostname"] else "", stderr="")


class StartTests(unittest.TestCase):
    def setUp(self):
        use_scratch_database(self)
        use_rules_only(self)
        for patcher in (
            mock.patch.object(app.routes.app, "run"),
            mock.patch.object(app.subprocess, "run", side_effect=worked),
            mock.patch.object(app.signal, "signal"),
            mock.patch("builtins.print"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def commands(self):
        return [call.args[0] for call in app.subprocess.run.call_args_list]

    def as_user(self, user_id):
        return mock.patch.object(app.os, "geteuid", return_value=user_id, create=True)

    def test_dev_serves_on_any_computer_without_touching_the_network(self):
        app.main(["--dev"])
        app.routes.app.run.assert_called_once_with(host=config.HOST, port=config.DEV_PORT, threaded=True, use_reloader=False)
        self.assertEqual(self.commands(), [])
        self.assertIsNotNone(database.active_mission(), "the database and this run's mission are ready")

    def test_a_port_can_be_chosen(self):
        app.main(["--dev", "--port", "8080"])
        self.assertEqual(app.routes.app.run.call_args.kwargs["port"], 8080)

    def test_the_field_needs_root_and_says_what_to_run_instead(self):
        with self.as_user(1000), self.assertRaises(SystemExit) as stopped:
            app.main([])
        self.assertIn("--dev", str(stopped.exception))
        app.routes.app.run.assert_not_called()

    def test_a_wifi_password_must_be_long_enough(self):
        with self.as_user(0), mock.patch.object(config, "WIFI_PASSWORD", "short"), self.assertRaises(SystemExit):
            app.main([])
        self.assertEqual(self.commands(), [])

    def test_the_field_brings_the_network_up_and_takes_it_down_on_the_way_out(self):
        app.routes.app.run.side_effect = KeyboardInterrupt  # Ctrl-C while serving
        with self.as_user(0), mock.patch.object(config, "WIFI_PASSWORD", "harbor-crew-7"):
            app.main([])

        commands = self.commands()
        self.assertEqual(commands[1], ["hostnamectl", "set-hostname", config.HOSTNAME])
        self.assertEqual(commands[3][:3], ["nmcli", "connection", "add"])
        self.assertIn("ap", commands[3])
        self.assertEqual(commands[4], ["nmcli", "connection", "up", config.ACCESS_POINT_CONNECTION])
        self.assertEqual(commands[-1], ["nmcli", "connection", "down", config.ACCESS_POINT_CONNECTION])
        app.routes.app.run.assert_called_once()
        self.assertEqual(app.routes.app.run.call_args.kwargs["port"], config.PORT)

        printed = " ".join(str(part) for call in builtins.print.call_args_list for part in call.args)
        self.assertNotIn("harbor-crew-7", printed, "the Wi-Fi password is never printed")


if __name__ == "__main__":
    unittest.main()
