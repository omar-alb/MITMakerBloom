"""What the stored readings add up to: risk levels, alerts, map cells, hotspots, and the headline.

Nothing here scores a reading. Each reading's risk was worked out once, when
it arrived (processing.py), and everything here is built from those stored
risks, so a marker, its cell, an alert, a hotspot, and the headline can never
disagree.

    level(risk), is_alert(risk), rollup(risks)    a risk read against config.RISK_LEVELS
    alerts_for(reading)                           the alerts a new reading raises; stored with it
    overview(readings, ...)                       a mission's cells, hotspots, headline, and boat status
"""

from datetime import datetime
from math import asin, cos, floor, radians, sin, sqrt

import config

# Facts about the Earth, not settings (those are in config.py).
METERS_PER_DEGREE_LATITUDE = 111_320.0
EARTH_RADIUS_METERS = 6_371_000.0
MAX_GRID_LATITUDE = 85.0  # cells are sized by cos(latitude), which reaches zero at the poles


def overview(readings, grid_latitude, now=None):
    """Everything a mission's readings add up to, as the dashboard shows it.

    `readings` are the mission's readings, oldest first; `grid_latitude` sizes
    its map cells.
    """
    now = now or datetime.now().astimezone()
    cells = build_cells(readings, grid_latitude)
    hotspots = build_hotspots(readings, now)
    return {
        "cells": cells,
        "hotspots": hotspots,
        "summary": _summary(readings, cells, hotspots),
        "boat": _boat(readings[-1] if readings else None, now),
    }


# ---------------------------------------------------------------------------
# Risk levels
# ---------------------------------------------------------------------------


def level(risk):
    """The risk level a score falls on: "Low", "Moderate", "High", or "Severe"."""
    return _rung(risk)["label"]


def is_alert(risk):
    """True when a score is at one of the alert levels (High or Severe)."""
    return level(risk) in config.ALERT_LEVELS


def rollup(risks):
    """One score for a group: the mean of the scores blended with the peak.

    So one hot reading is neither averaged away nor allowed to speak for its
    whole area. A cell rolls up its readings; the headline rolls up the cells.
    """
    risks = list(risks)
    if not risks:
        return 0
    mean = sum(risks) / len(risks)
    return round(mean + (max(risks) - mean) * config.ROLLUP_PEAK_SHARE)


def _rung(risk):
    """The config.RISK_LEVELS rung a score falls on.

    The score is rounded first, exactly as it is shown, so 50.4 is Moderate
    everywhere rather than Moderate in one place and High in another.
    """
    value = round(min(100, max(0, risk)))
    found = config.RISK_LEVELS[0]
    for rung in config.RISK_LEVELS:
        if value >= rung["min"]:
            found = rung
    return found


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


def alerts_for(reading):
    """The alerts a new reading raises, each as {kind, level, title, message}.

    A bloom alert fires when the reading's risk is at an alert level, and
    quotes that risk, so an alert and the marker that raised it always agree.
    A sensor alert fires when a sensor with an `alert_at` threshold reaches
    it. Alerts are stored with their reading and stay until acknowledged.
    """
    alerts = []
    if is_alert(reading["risk"]):
        rung = _rung(reading["risk"])
        if reading["latitude"] is None:
            where = "(no GPS fix)"
        else:
            where = "at {:.5f}, {:.5f}".format(reading["latitude"], reading["longitude"])
        because = ", driven by " + _and_list(reading["drivers"]) if reading["drivers"] else ""
        alerts.append({
            "kind": "bloom",
            "level": rung["label"],
            "title": "{} bloom risk".format(rung["label"]),
            "message": "Bloom risk {}/100 {}{}. {}".format(reading["risk"], where, because, rung["advice"]),
        })

    for sensor in config.SENSORS:
        value = reading.get(sensor.key)
        if sensor.alert_at is not None and value is not None and value >= sensor.alert_at:
            unit = " " + sensor.unit if sensor.unit else ""
            alerts.append({
                "kind": sensor.key,
                "level": config.SENSOR_ALERT_LEVEL,
                "title": "High {}".format(sensor.label.lower()),
                "message": "{} reached {:.{}f}{}.".format(sensor.label, value, sensor.digits, unit),
            })
    return alerts


def _and_list(items):
    """'a', 'a and b', 'a, b, and c'."""
    if len(items) <= 2:
        return " and ".join(items)
    return "{}, and {}".format(", ".join(items[:-1]), items[-1])


# ---------------------------------------------------------------------------
# Map cells
# ---------------------------------------------------------------------------


def build_cells(readings, grid_latitude):
    """A cell for every square that holds at least one located reading.

    Cells are CELL_SIZE_METERS on a side, sized for the latitude of the
    mission's first GPS fix so they stay square wherever the boat is
    launched. A cell's risk is the roll-up of the readings inside it.
    """
    members = {}
    for reading in readings:
        if reading["latitude"] is not None:
            key = cell_id(reading["latitude"], reading["longitude"], grid_latitude)
            members.setdefault(key, []).append(reading)
    return [_cell(key, group, grid_latitude) for key, group in sorted(members.items())]


def cell_id(latitude, longitude, grid_latitude):
    """The id ("row:column") of the cell a position falls in."""
    row = floor(latitude * METERS_PER_DEGREE_LATITUDE / config.CELL_SIZE_METERS)
    column = floor(longitude * _meters_per_degree_longitude(grid_latitude) / config.CELL_SIZE_METERS)
    return "{}:{}".format(row, column)


def cell_bounds(key, grid_latitude):
    """[[south, west], [north, east]] of a cell, in degrees."""
    row, column = (int(part) for part in key.split(":"))
    size = config.CELL_SIZE_METERS
    per_degree_longitude = _meters_per_degree_longitude(grid_latitude)
    south, north = row * size / METERS_PER_DEGREE_LATITUDE, (row + 1) * size / METERS_PER_DEGREE_LATITUDE
    west, east = column * size / per_degree_longitude, (column + 1) * size / per_degree_longitude
    return [[round(south, 7), round(west, 7)], [round(north, 7), round(east, 7)]]


def distance_m(latitude_a, longitude_a, latitude_b, longitude_b):
    """Great-circle distance in metres."""
    d_latitude = radians(latitude_b - latitude_a)
    d_longitude = radians(longitude_b - longitude_a)
    a = sin(d_latitude / 2) ** 2 + cos(radians(latitude_a)) * cos(radians(latitude_b)) * sin(d_longitude / 2) ** 2
    return 2 * EARTH_RADIUS_METERS * asin(sqrt(min(1.0, a)))


def _cell(key, readings, grid_latitude):
    risks = [reading["risk"] for reading in readings]
    risk = rollup(risks)
    averages = {}
    for sensor in config.SENSORS:
        values = [reading[sensor.key] for reading in readings if reading[sensor.key] is not None]
        if values:
            averages[sensor.key] = round(_mean(values), sensor.digits)

    return {
        "id": key,
        "bounds": cell_bounds(key, grid_latitude),
        "risk": risk,
        "peak": max(risks),
        "level": level(risk),
        "confidence": round(_mean([reading["confidence"] for reading in readings])),
        "readings": len(readings),
        "averages": averages,
    }


def _meters_per_degree_longitude(latitude):
    latitude = min(MAX_GRID_LATITUDE, max(-MAX_GRID_LATITUDE, latitude or 0.0))
    return METERS_PER_DEGREE_LATITUDE * cos(radians(latitude))


# ---------------------------------------------------------------------------
# Hotspots
# ---------------------------------------------------------------------------


def build_hotspots(readings, now):
    """Clusters of alert-level readings, the one most worth revisiting first.

    Located readings at an alert level that lie within HOTSPOT_JOIN_METERS of
    a hotspot's centre join it. Riskier, less certain, farther from the boat,
    and longer since it was sampled all push a hotspot's revisit priority up;
    the first REVISIT_TARGETS are numbered on the map.
    """
    clusters = []
    for reading in readings:
        if reading["latitude"] is None or not is_alert(reading["risk"]):
            continue
        position = (reading["latitude"], reading["longitude"])
        cluster = next(
            (each for each in clusters if distance_m(*each["center"], *position) <= config.HOTSPOT_JOIN_METERS),
            None,
        )
        if cluster is None:
            clusters.append({"center": position, "readings": [reading]})
        else:
            cluster["readings"].append(reading)
            count = len(cluster["readings"])
            cluster["center"] = tuple(old + (new - old) / count for old, new in zip(cluster["center"], position))

    located = [reading for reading in readings if reading["latitude"] is not None]
    boat = (located[-1]["latitude"], located[-1]["longitude"]) if located else None
    hotspots = sorted((_hotspot(cluster, boat, now) for cluster in clusters), key=lambda spot: -spot["priority"])
    for rank, hotspot in enumerate(hotspots, start=1):
        hotspot["id"] = rank
        hotspot["revisit"] = rank if rank <= config.REVISIT_TARGETS else None
    return hotspots


def _hotspot(cluster, boat, now):
    readings = cluster["readings"]
    latitude, longitude = cluster["center"]
    spread = max(distance_m(latitude, longitude, r["latitude"], r["longitude"]) for r in readings)
    risk = max(reading["risk"] for reading in readings)
    confidence = round(_mean([reading["confidence"] for reading in readings]))
    last_taken_at = readings[-1]["taken_at"]
    distance = distance_m(latitude, longitude, *boat)
    age = max(0.0, (now - datetime.fromisoformat(last_taken_at)).total_seconds())

    weights = config.HOTSPOT_PRIORITY_WEIGHTS
    priority = (
        weights["risk"] * risk / 100
        + weights["uncertainty"] * (1 - confidence / 100)
        + weights["distance"] * min(1.0, distance / config.HOTSPOT_FAR_METERS)
        + weights["age"] * min(1.0, age / config.HOTSPOT_OLD_SECONDS)
    )
    label = level(risk)
    return {
        "lat": round(latitude, 6),
        "lng": round(longitude, 6),
        "radius": round(min(
            config.HOTSPOT_MAX_RADIUS_METERS, max(config.HOTSPOT_MIN_RADIUS_METERS, spread + config.HOTSPOT_MARGIN_METERS)
        )),
        "risk": risk,
        "level": label,
        "confidence": confidence,
        "readings": len(readings),
        "lastTakenAt": last_taken_at,
        "distance": round(distance),
        "priority": round(priority * 100),
        "reason": "{} ({}/100), {}% confidence, {} m from the boat, last sampled {} min ago.".format(
            label, risk, confidence, round(distance), round(age / 60)
        ),
    }


# ---------------------------------------------------------------------------
# The headline and the boat
# ---------------------------------------------------------------------------


def _summary(readings, cells, hotspots):
    """The headline risk, and what it was rolled up from.

    With located readings it is the roll-up of the cells on the map, so it
    cannot disagree with the map. Without any GPS fix it rolls up the
    readings themselves.
    """
    if cells:
        basis, risks = "cells", [cell["risk"] for cell in cells]
    elif readings:
        basis, risks = "readings", [reading["risk"] for reading in readings]
    else:
        basis, risks = "none", []
    risk = rollup(risks)
    levels = [level(value) for value in risks]
    return {
        "risk": risk,
        "level": level(risk),
        "basis": basis,
        "counted": len(risks),
        "peak": max(risks) if risks else None,
        "distribution": [
            {"label": rung["label"], "count": levels.count(rung["label"])} for rung in config.RISK_LEVELS
        ],
        "alertCells": sum(is_alert(cell["risk"]) for cell in cells),
        "hotspots": len(hotspots),
    }


def _boat(latest, now):
    """Online while the boat was heard from within BOAT_OFFLINE_AFTER_SECONDS."""
    if latest is None:
        return {"online": False, "lastHeard": None, "gps": None, "hardware": None}
    silent_for = (now - datetime.fromisoformat(latest["received_at"])).total_seconds()
    return {
        "online": silent_for <= config.BOAT_OFFLINE_AFTER_SECONDS,
        "lastHeard": latest["received_at"],
        "gps": latest["gps"],
        "hardware": latest["hardware"],
    }


def _mean(values):
    return sum(values) / len(values)
