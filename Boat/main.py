"""The boat's program; MicroPython runs main.py at boot.

Every READING_INTERVAL_SECONDS: take a reading, save it to the outbox, and
send what Wi-Fi allows. Each reading has a unique sample_id, so the server
stores a resent one only once.
"""
import gc
import json
import os
import sys
import time

import config
import gps
import outbox
import sensors
import simulation

try:
    import network
except ImportError:  # desktop Python: tests and bench demos
    network = None
if not hasattr(network, "WLAN"):  # an unrelated desktop package can share the name
    network = None

# urequests (older MicroPython) or requests (newer); urllib on a desktop.
try:
    import urequests as requests
except ImportError:
    requests = None
    if sys.implementation.name == "micropython":
        try:
            import requests
        except ImportError:
            pass

ticks_ms = getattr(time, "ticks_ms", lambda: int(time.time() * 1000))
ticks_diff = getattr(time, "ticks_diff", lambda end, start: end - start)

MAX_AGE_MS = 6 * 24 * 3600 * 1000  # ticks_diff() is only valid under about six days
RADIO_SETTLE_SECONDS = 1
MIN_PAUSE_SECONDS = 0.5


def _new_boot_id():
    try:
        return "".join("{:02x}".format(byte) for byte in os.urandom(6))
    except Exception:  # ports without a hardware random source
        import random

        return "{:08x}".format(random.getrandbits(32))


# A fresh id each boot keeps sample ids unique without a counter on flash.
BOOT_ID = _new_boot_id()
_reading_count = 0
_wifi = network.WLAN(network.STA_IF) if network else None
_last_wifi_attempt_ms = None


# ---------------------------------------------------------------------------
# Readings
# ---------------------------------------------------------------------------


def take_reading():
    """One reading: raw probe millivolts plus the GPS position."""
    if config.SIMULATE_HARDWARE:
        millivolts, hardware = simulation.read_probes(config.PROBES), "simulated"
        position = simulation.read_position()
    else:
        millivolts, hardware = sensors.read_probes()
        position = gps.read_position()
    return {
        "device": config.DEVICE_NAME,
        "hardware": hardware,
        "millivolts": millivolts,
        "gps": position,
    }


def stamp(reading):
    """Give a reading its id and note when it was taken. "_" fields are never sent."""
    global _reading_count
    _reading_count += 1
    reading["sample_id"] = "{}-{}-{}".format(config.DEVICE_NAME, BOOT_ID, _reading_count)
    reading["_boot"] = BOOT_ID
    reading["_taken_ms"] = ticks_ms()
    return reading


def request_body(reading):
    """The reading without its "_" fields, plus age_seconds.

    The ESP32 has no clock, so the server works out the time from the age.
    Readings from an earlier boot can't know their age and are sent without one.
    """
    body = {key: value for key, value in reading.items() if not key.startswith("_")}
    if reading.get("_boot") == BOOT_ID and reading.get("_taken_ms") is not None:
        age_ms = ticks_diff(ticks_ms(), reading["_taken_ms"])
        if 0 <= age_ms < MAX_AGE_MS:
            body["age_seconds"] = round(age_ms / 1000, 1)
    return body


def send(reading):
    """POST one reading. Returns outbox.SENT, outbox.REJECTED, or outbox.FAILED."""
    try:
        status = _post(json.dumps(request_body(reading)))
    except Exception as error:
        print("Could not reach the server:", error)
        return outbox.FAILED

    if 200 <= status < 300:
        return outbox.SENT
    # A 4xx won't change on resend, except 408 and 429.
    if 400 <= status < 500 and status not in (408, 429):
        print("Server refused reading {} ({}); dropping it.".format(reading.get("sample_id"), status))
        return outbox.REJECTED
    print("Server answered {}; will retry.".format(status))
    return outbox.FAILED


def _post(body):
    """POST the body to the server and return the HTTP status code."""
    headers = {"Content-Type": "application/json"}
    if requests is not None:
        # Without a timeout a half-open connection hangs the loop. Very old
        # urequests builds don't take one.
        try:
            response = requests.post(
                config.SERVER_URL, data=body, headers=headers, timeout=config.HTTP_TIMEOUT_SECONDS
            )
        except TypeError:
            response = requests.post(config.SERVER_URL, data=body, headers=headers)
        try:
            return response.status_code
        finally:
            response.close()

    from urllib import error, request

    http_request = request.Request(config.SERVER_URL, data=body.encode(), headers=headers, method="POST")
    try:
        with request.urlopen(http_request, timeout=config.HTTP_TIMEOUT_SECONDS) as response:
            return response.status
    except error.HTTPError as http_error:
        return http_error.code


# ---------------------------------------------------------------------------
# Wi-Fi
# ---------------------------------------------------------------------------


def connect_wifi():
    """Try to connect at boot. Readings are kept in the outbox either way."""
    if _wifi is None:
        return True  # desktop Python uses the computer's own network

    _wifi.active(True)
    for attempt in range(1, config.WIFI_CONNECT_ATTEMPTS + 1):
        if _wifi.isconnected():
            break
        print("Connecting to {} (attempt {}/{})".format(config.WIFI_NAME, attempt, config.WIFI_CONNECT_ATTEMPTS))
        _restart_radio()
        try:
            _start_connect()
        except OSError as error:
            print("Wi-Fi error:", error)
            time.sleep(config.WIFI_ERROR_PAUSE_SECONDS)
            continue
        for _ in range(config.WIFI_CONNECT_WAIT_SECONDS):
            if _wifi.isconnected():
                break
            time.sleep(1)

    if _wifi.isconnected():
        print("Connected:", _wifi.ifconfig())
        return True
    print("No Wi-Fi yet; readings will wait in the outbox.")
    return False


def wifi_ready():
    """True when connected; otherwise starts a background reconnect (at most
    every WIFI_RETRY_SECONDS) and returns False without waiting."""
    global _last_wifi_attempt_ms
    if _wifi is None or _wifi.isconnected():
        return True

    now = ticks_ms()
    if _last_wifi_attempt_ms is not None and ticks_diff(now, _last_wifi_attempt_ms) < config.WIFI_RETRY_SECONDS * 1000:
        return False
    _last_wifi_attempt_ms = now

    try:
        if _wifi.status() == getattr(network, "STAT_CONNECTING", None):
            return False
        _wifi.disconnect()
    except OSError:
        pass
    try:
        _start_connect()
        print("Wi-Fi down; reconnecting in the background.")
    except OSError as error:
        print("Wi-Fi error:", error)
        _restart_radio()
    return False


def _start_connect():
    if config.WIFI_PASSWORD is None:
        _wifi.connect(config.WIFI_NAME)
    else:
        _wifi.connect(config.WIFI_NAME, config.WIFI_PASSWORD)


def _restart_radio():
    try:
        _wifi.disconnect()
    except OSError:
        pass
    _wifi.active(False)
    time.sleep(RADIO_SETTLE_SECONDS)
    _wifi.active(True)
    time.sleep(RADIO_SETTLE_SECONDS)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run_cycle():
    """Take one reading, save it, and send whatever Wi-Fi allows."""
    reading = None
    try:
        reading = stamp(take_reading())
    except Exception as error:
        print("Could not take a reading:", error)

    saved = reading is not None and outbox.add(reading)
    if not wifi_ready():
        if reading is not None and not saved:
            print("No Wi-Fi and the outbox is failing; this reading is lost.")
        return False
    if reading is not None and not saved:
        send(reading)  # the outbox is failing, so try this one directly
    budget_ms = config.READING_INTERVAL_SECONDS * 1000 * config.SEND_SHARE_OF_INTERVAL
    return outbox.send_all(send, budget_ms=budget_ms)


def main():
    print("Boat starting. Sending to", config.SERVER_URL)
    outbox.recover()
    connect_wifi()
    while True:
        started = ticks_ms()
        try:
            run_cycle()
        except Exception as error:
            print("Reading cycle failed:", error)
        gc.collect()
        elapsed = ticks_diff(ticks_ms(), started) / 1000
        time.sleep(max(MIN_PAUSE_SECONDS, config.READING_INTERVAL_SECONDS - elapsed))


if __name__ == "__main__":
    main()
