"""Simulated probes and GPS for bench demos (config.SIMULATE_HARDWARE)."""
import math
import random
import time

import config

# Millivolts: (centre, random spread either side).
MILLIVOLTS = {
    "ph": (1460, 90),
    "temperature": (720, 80),
    "turbidity": (2799, 1),
    "conductivity": (82, 25),
    "nitrate": (1705, 25),
    "ammonium": (1595, 25),
}

# Occasional bloom or runoff events: (chance per reading, millivolt shift).
EVENTS = {
    "turbidity": (0.18, -8),
    "nitrate": (0.16, -45),
    "ammonium": (0.16, 45),
}

# Different periods per axis, so the track wanders instead of circling.
NORTH_SOUTH_SECONDS = 35
EAST_WEST_SECONDS = 42
METERS_PER_DEGREE_LATITUDE = 111320

_started = time.time()


def read_probes(names):
    """Millivolts for each named probe."""
    return {name: _millivolts(name) for name in names}


def read_position():
    """A slowly moving position around config.SIMULATED_TRACK_CENTER."""
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
    return round(max(0.0, min(config.ADC_FULL_SCALE_VOLTS * 1000, value)), 2)
