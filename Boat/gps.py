"""The GPS receiver: NMEA sentences over UART, turned into a position.

read_position() returns {"status", "latitude", "longitude"}:

    fix          a sentence with a satellite lock arrived just now
    stale        no lock right now, so the last fix (under a minute old) is reused
    no_fix       the GPS is talking but has no lock; no position is sent
    unavailable  there is no working GPS (pins not set, or the UART failed)

Without a position the server still records the water chemistry, it just
does not put the reading on the map.
"""
import time

import config

try:
    from machine import UART, Pin
except ImportError:  # desktop Python, for tests
    UART = Pin = None

# MicroPython's millisecond clock, with a stand-in for desktop Python.
ticks_ms = getattr(time, "ticks_ms", lambda: int(time.time() * 1000))
ticks_diff = getattr(time, "ticks_diff", lambda end, start: end - start)

# NMEA caps a sentence at 82 characters. Past this with no line ending, what
# has built up is noise (or the wrong baud rate) and is thrown away.
MAX_LINE_BYTES = 200

# While waiting for a sentence, check the UART this often.
POLL_PAUSE_SECONDS = 0.05
# Emptying stale data stops after this many reads: the receiver keeps talking meanwhile.
DISCARD_READS = 8

_uart = None
_buffer = b""
_last_fix = None  # (latitude, longitude)
_last_fix_ms = None
_warned = False


def read_position():
    global _last_fix, _last_fix_ms, _warned

    if UART is None or config.GPS_RX_PIN is None or config.GPS_TX_PIN is None:
        if not _warned:
            print("GPS_RX_PIN and GPS_TX_PIN are not set in config.py; readings will have no position.")
            _warned = True
        return _no_position("unavailable")

    try:
        uart = _open_uart()
    except Exception as error:
        print("GPS UART could not be opened:", error)
        return _no_position("unavailable")

    # Whatever sits in the buffer arrived just after the previous reading,
    # seconds ago. Throw it away and wait for the receiver's next sentences.
    _discard_buffered(uart)
    started = ticks_ms()
    while ticks_diff(ticks_ms(), started) < config.GPS_READ_TIMEOUT_MS:
        line = _next_line(uart)
        if line is None:
            time.sleep(POLL_PAUSE_SECONDS)
            continue
        position = parse_sentence(line)
        if position is not None:
            _last_fix, _last_fix_ms = position, ticks_ms()
            return _position("fix", position)

    # A short dropout reuses the last fix. A long one does not: the boat has
    # moved on, and every reading would be mapped at the old spot.
    if _last_fix is not None:
        age_ms = ticks_diff(ticks_ms(), _last_fix_ms)
        if 0 <= age_ms < config.GPS_STALE_LIMIT_SECONDS * 1000:
            return _position("stale", _last_fix)
    return _no_position("no_fix")


def parse_sentence(line):
    """(latitude, longitude) from a GGA or RMC sentence that reports a lock.

    Anything else - another sentence type, a bad checksum, no lock, or
    coordinates that are malformed or out of range - gives None. A receiver
    without a lock can still repeat its last known position, so the lock
    flag matters as much as the coordinates.
    """
    if not line.startswith("$") or not _checksum_ok(line):
        return None
    fields = line[: line.rfind("*")].split(",")
    kind = fields[0][3:]  # "$GPGGA" and "$GNGGA" are both GGA
    if kind == "GGA" and len(fields) >= 7:
        locked = fields[6].isdigit() and int(fields[6]) > 0  # fix quality
        latitude, longitude = _coordinate(fields[2], fields[3]), _coordinate(fields[4], fields[5])
    elif kind == "RMC" and len(fields) >= 7:
        locked = fields[2] == "A"
        latitude, longitude = _coordinate(fields[3], fields[4]), _coordinate(fields[5], fields[6])
    else:
        return None
    if not locked or latitude is None or longitude is None:
        return None
    return latitude, longitude


def _checksum_ok(line):
    """True when the sentence ends in *hh, the XOR of everything between $ and *.

    Noise on the UART can change a digit inside a coordinate and still leave a
    believable position; this is what catches it. A line cut off before its
    checksum fails too.
    """
    star = line.rfind("*")
    if star < 1 or len(line) < star + 3:
        return False
    checksum = 0
    for character in line[1:star]:
        checksum ^= ord(character)
    try:
        return checksum == int(line[star + 1 : star + 3], 16)
    except ValueError:
        return False


def _coordinate(value, hemisphere):
    """NMEA ddmm.mmmm (latitude) or dddmm.mmmm (longitude) to signed degrees."""
    if not value or hemisphere not in ("N", "S", "E", "W"):
        return None
    is_latitude = hemisphere in ("N", "S")
    degree_digits = 2 if is_latitude else 3
    try:
        degrees = float(value[:degree_digits])
        minutes = float(value[degree_digits:])
    except ValueError:
        return None
    if not 0 <= minutes < 60:
        return None
    result = degrees + minutes / 60
    if result > (90 if is_latitude else 180):
        return None
    return round(-result if hemisphere in ("S", "W") else result, 6)


def _open_uart():
    global _uart
    if _uart is None:
        # timeout=0 keeps read() non-blocking: it returns whatever has arrived.
        _uart = UART(
            config.GPS_UART_ID,
            baudrate=config.GPS_BAUD,
            rx=Pin(config.GPS_RX_PIN),
            tx=Pin(config.GPS_TX_PIN),
            timeout=0,
        )
    return _uart


def _discard_buffered(uart):
    global _buffer
    _buffer = b""
    for _ in range(DISCARD_READS):
        try:
            if not uart.read():
                return
        except Exception:
            return


def _next_line(uart):
    """The next complete line, or None until one has arrived.

    A UART read returns whatever has come in so far, which often ends in the
    middle of a sentence, so the start is kept until the rest arrives. A line
    that is not valid text comes back as "" (MicroPython's decode() has no
    "ignore" mode), which parse_sentence() rejects.
    """
    global _buffer
    newline = _buffer.find(b"\n")
    if newline < 0:
        try:
            chunk = uart.read()
        except Exception as error:
            print("GPS read failed:", error)
            return None
        if chunk:
            _buffer += chunk
            newline = _buffer.find(b"\n")
        if newline < 0:
            if len(_buffer) > MAX_LINE_BYTES:
                _buffer = b""
            return None

    raw, _buffer = _buffer[:newline], _buffer[newline + 1 :]
    try:
        return raw.decode().strip()
    except UnicodeError:
        return ""


def _position(status, position):
    return {"status": status, "latitude": position[0], "longitude": position[1]}


def _no_position(status):
    return {"status": status, "latitude": None, "longitude": None}
