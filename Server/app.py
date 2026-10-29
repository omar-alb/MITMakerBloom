"""Start the server.

    sudo python3 Server/app.py        on the Pi: creates the "Bloom" Wi-Fi, serves http://10.0.0.1
    python3 Server/app.py --dev       on any computer: http://localhost:5000

Stopping takes the Wi-Fi network down again.
"""

import argparse
import os
import signal
import subprocess
import sys

import config
import database
import processing
import routes

WPA_MIN_PASSWORD_LENGTH = 8


def main(arguments=None):
    options = _options(arguments)
    field = not options.dev
    port = options.port or (config.PORT if field else config.DEV_PORT)
    if field:
        _check_field_setup()

    # SIGTERM (systemctl stop, Shut down button) exits through the finally below.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
    try:
        if field:
            set_hostname()
            start_access_point(port)
        else:
            print("Dashboard: http://localhost:{}".format(port))
        prepare()
        routes.app.run(host=config.HOST, port=port, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        if field:
            print("Taking the access point down.")
            _run(["nmcli", "connection", "down", config.ACCESS_POINT_CONNECTION], check=False)


def prepare():
    """Open the database, load the model, and open this run's mission."""
    database.init()
    processing.load_model()
    mission = database.start_mission() if config.NEW_MISSION_ON_START else database.resume_mission()
    print("Mission:", mission["name"])


def _options(arguments):
    parser = argparse.ArgumentParser(description="Start the Bloom Detection server.")
    parser.add_argument(
        "--dev", action="store_true",
        help="run on any computer: no Wi-Fi changes, port {}".format(config.DEV_PORT),
    )
    parser.add_argument("--port", type=int, help="serve on this port instead")
    return parser.parse_args(arguments)


# ---------------------------------------------------------------------------
# The Raspberry Pi's Wi-Fi network
# ---------------------------------------------------------------------------


def _check_field_setup():
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        sys.exit(
            "On the Raspberry Pi, run this with sudo: it sets up the Wi-Fi network.\n"
            "On any other computer, use: python3 Server/app.py --dev"
        )
    if config.WIFI_PASSWORD and len(config.WIFI_PASSWORD) < WPA_MIN_PASSWORD_LENGTH:
        sys.exit("BLOOM_WIFI_PASSWORD must be at least {} characters, or unset for an open network.".format(
            WPA_MIN_PASSWORD_LENGTH
        ))


def set_hostname():
    if _run(["hostname"], check=False).stdout.strip() != config.HOSTNAME:
        _run(["hostnamectl", "set-hostname", config.HOSTNAME], check=False)


def start_access_point(port):
    name = config.ACCESS_POINT_CONNECTION
    _run(["nmcli", "connection", "delete", name], check=False)  # from a previous run
    security = ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", config.WIFI_PASSWORD] if config.WIFI_PASSWORD else []
    _run([
        "nmcli", "connection", "add", "type", "wifi", "ifname", config.WIFI_INTERFACE,
        "con-name", name, "autoconnect", "no", "ssid", config.WIFI_NAME,
        "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
        "ipv4.method", "shared", "ipv4.addresses", "{}/{}".format(config.ADDRESS, config.ADDRESS_PREFIX_LENGTH),
        "ipv6.method", "ignore",
        *security,
    ])
    _run(["nmcli", "connection", "up", name])

    shown_port = "" if port == 80 else ":{}".format(port)
    print()
    print("Wi-Fi network: {} ({})".format(
        config.WIFI_NAME, "password protected" if config.WIFI_PASSWORD else "open, no password"
    ))
    print("Dashboard: http://{}{}  (or http://{}.local{} where mDNS works)".format(
        config.ADDRESS, shown_port, config.HOSTNAME, shown_port
    ))
    print()


def _run(command, check=True):
    shown = ["********" if part == config.WIFI_PASSWORD else part for part in command]
    print("$", " ".join(shown))
    result = subprocess.run(command, text=True, capture_output=True)
    output = (result.stdout + result.stderr).strip()
    if output:
        print(output)
    if check and result.returncode != 0:
        raise RuntimeError("Command failed: {}".format(" ".join(shown)))
    return result


if __name__ == "__main__":
    main()
