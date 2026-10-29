"""Simulated probes and GPS, for bench demos with no hardware at all.

Used only when config.SIMULATE_HARDWARE is True. The boat then runs its
normal loop - outbox and all - with these values in place of real ones, and
the server marks every reading as simulated.
"""
import math
import random
import time

import config

# Typical probe output in millivolts: (centre, random spread either side).
MILLIVOLTS = {
    "ph": (1470, 160),
    "temperature": (720, 80),
    "turbidity": (2750, 450),
    "conductivity": (1250, 350),
    "nitrate": (1520, 180),
    "ammonium": (1420, 180),
}

# Occasional dips that mimic a bloom or runoff event: (chance per reading, millivolts).
EVENTS = {
    "turbidity": (0.18, -850),
    "nitrate": (0.16, -220),
    "ammonium": (0.16, -240),
}

# The track wanders, rather than circling exactly: north-south and east-west
# swing on these different timescales (about 3.5 and 4.5 minutes a sweep).
NORTH_SOUTH_SECONDS = 35
EAST_WEST_SECONDS = 42
METERS_PER_DEGREE_LATITUDE = 111320  # a fact about the Earth

_started = time.time()


def read_probes(names):
    """Millivolts for each named probe, like sensors.read_probes() would give."""
    return {name: _millivolts(name) for name in names}


def read_position():
    """A slowly moving position within config.SIMULATED_TRACK_RADIUS_METERS, like gps.read_position() would give."""
    elapsed = time.time() - _started
    latitude, longitude = config.SIMULATED_TRACK_CENTER
    north = config.SIMULATED_TRACK_RADIUS_METERS * math.sin(elapsed / NORTH_SOUTH_SECONDS)
    east = config.SIMULATED_TRACK_RADIUS_METERS * math.cos(elapsed / EAST_WEST_SECONDS)
    return {
        "status": "simulated",
        "latitude": round(latitude + north / METERS_PER_DEGREE_LATITUDE, 6),
        "longitude": round(longitude + east / (METERS_PER_DEGREE_LATITUDE * math.cos(math.radians(latitude))), 6),
    }


def _millivolts(name):
    centre, spread = MILLIVOLTS[name]
    chance, offset = EVENTS.get(name, (0, 0))
    value = centre + random.uniform(-spread, spread)
    if random.random() < chance:
        value += offset
    # Never more than the ADC itself could read.
    return round(max(0.0, min(config.ADC_FULL_SCALE_VOLTS * 1000, value)), 2)
