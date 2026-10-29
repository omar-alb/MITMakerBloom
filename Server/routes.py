"""Every URL the boat and the dashboard use.

    GET  /config.js                       the dashboard's settings, from config.py
    POST /readings                        the boat sends one reading
    GET  /dashboard                       everything the dashboard shows, for the active mission
    GET  /missions/<id>/dashboard         the same for any mission
    GET  /missions                        every mission, newest first
    POST /missions                        start a new mission ({"name": ...} optional); ends the active one
    POST /missions/end                    end the active mission
    GET  /missions/<id>/readings.csv      every reading of a mission
    GET  /missions/<id>/replay/<reading>  the mission as it stood when that reading arrived
    POST /alerts/<id>/acknowledge         acknowledge one alert
    POST /alerts/acknowledge              acknowledge every open alert of the active mission
    GET  /model                           which model is scoring, and why
    POST /model/reload                    load the model in Server/model/ again
    POST /shutdown                        stop the server (and the Wi-Fi network with it)

Errors are JSON: {"error": "..."}.
"""

import csv
import io
import json
import os
import re
import signal
import threading
from datetime import datetime

from flask import Flask, Response, jsonify, request, stream_with_context
from werkzeug.exceptions import HTTPException

import analysis
import config
import database
import processing

app = Flask(__name__, static_folder=str(config.WEB_DIR), static_url_path="")


@app.route("/")
def page():
    return app.send_static_file("index.html")


@app.route("/config.js")
def dashboard_settings():
    """The dashboard's settings from config.py."""
    settings = {
        "pollSeconds": config.DASHBOARD_POLL_SECONDS,
        "timeoutSeconds": config.DASHBOARD_TIMEOUT_SECONDS,
        "alertPageSize": config.ALERT_PAGE_SIZE,
        "pageSize": config.TABLE_PAGE_SIZE,
        "pageSizePhone": config.TABLE_PAGE_SIZE_PHONE,
        "replayStepSeconds": config.REPLAY_STEP_SECONDS,
        "maxZoom": config.MAP_MAX_ZOOM,
        "fitZoom": config.MAP_FIT_ZOOM,
        "openZoom": config.MAP_OPEN_ZOOM,
        "focusZoom": config.MAP_FOCUS_ZOOM,
        "gridMinPixels": config.MAP_GRID_MIN_PIXELS,
        "shadingRadiusMeters": config.SHADING_RADIUS_METERS,
    }
    return Response(
        "window.BLOOM_SETTINGS = {};\n".format(json.dumps(settings)),
        mimetype="text/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.route("/readings", methods=["POST"])
def post_reading():
    """Store one reading with its alerts. A resent sample_id gets 200 but isn't stored twice."""
    try:
        row = processing.process(request.get_json(silent=True))
    except processing.ReadingError as error:
        return _error(400, str(error))
    reading_id, created = database.save_reading(row, analysis.alerts_for(row))
    return jsonify({"status": "saved" if created else "duplicate", "id": reading_id})


@app.route("/dashboard")
@app.route("/missions/<int:mission_id>/dashboard")
def get_dashboard(mission_id=None):
    if mission_id is None:
        mission = database.active_mission()
    else:
        mission = database.get_mission(mission_id)
        if mission is None:
            return _error(404, "There is no mission {}.".format(mission_id))
    if mission is None:
        return jsonify(dashboard_document(None, []))

    return jsonify(dashboard_document(
        mission,
        database.mission_readings(mission["id"]),
        # An ended mission shows acknowledged alerts too.
        alerts=database.mission_alerts(mission["id"], include_acknowledged=mission["ended_at"] is not None),
        reading_count=database.count_readings(mission["id"]),
    ))


@app.route("/missions")
def get_missions():
    return jsonify([_mission_json(mission) for mission in database.list_missions()])


@app.route("/missions", methods=["POST"])
def post_mission():
    payload = request.get_json(silent=True)
    payload = {} if payload is None else payload
    if not isinstance(payload, dict) or not isinstance(payload.get("name"), (str, type(None))):
        return _error(400, 'Send {"name": "..."}, or nothing for an automatic name.')
    name = payload.get("name")
    if name and len(name.strip()) > config.MISSION_NAME_MAX_LENGTH:
        return _error(400, "A mission name can be at most {} characters.".format(config.MISSION_NAME_MAX_LENGTH))
    try:
        mission = database.start_mission(name)
    except database.MissionNameTaken as error:
        return _error(409, str(error))
    return jsonify(_mission_json(mission)), 201


@app.route("/missions/end", methods=["POST"])
def post_end_mission():
    mission = database.end_mission()
    if mission is None:
        return _error(404, "No mission is active.")
    return jsonify(_mission_json(mission))


@app.route("/missions/<int:mission_id>/readings.csv")
def get_readings_csv(mission_id):
    mission = database.get_mission(mission_id)
    if mission is None:
        return _error(404, "There is no mission {}.".format(mission_id))
    lines = csv_lines(mission, database.each_reading(mission_id), database.mission_readings(mission_id))
    filename = "bloom-{}.csv".format(re.sub(r"[^a-z0-9]+", "-", mission["name"].lower()).strip("-"))
    return Response(
        stream_with_context(lines),
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="{}"'.format(filename)},
    )


@app.route("/missions/<int:mission_id>/replay/<int:reading_id>")
def get_replay(mission_id, reading_id):
    """The cells, hotspots, headline, and alerts as they stood when one reading arrived."""
    mission = database.get_mission(mission_id)
    if mission is None:
        return _error(404, "There is no mission {}.".format(mission_id))
    readings = database.mission_readings(mission_id, upto=reading_id)
    if not readings or readings[-1]["id"] != reading_id:
        return _error(404, "Mission {} has no reading {}.".format(mission_id, reading_id))
    then = datetime.fromisoformat(readings[-1]["received_at"])
    alerts = database.mission_alerts(mission_id, include_acknowledged=True, upto=reading_id)
    for alert in alerts:
        # Acknowledged later counts as still open at that moment.
        if alert["acknowledged_at"] and datetime.fromisoformat(alert["acknowledged_at"]) > then:
            alert["acknowledged_at"] = None
    overview = analysis.overview(readings, mission["grid_latitude"], now=then)
    return jsonify({
        "readingId": reading_id,
        "cells": overview["cells"],
        "hotspots": overview["hotspots"],
        "summary": overview["summary"],
        "alerts": [_alert_json(alert) for alert in alerts],
    })


@app.route("/alerts/<int:alert_id>/acknowledge", methods=["POST"])
def post_acknowledge_alert(alert_id):
    if not database.acknowledge_alert(alert_id):
        return _error(404, "There is no alert {}.".format(alert_id))
    return jsonify({"status": "acknowledged"})


@app.route("/alerts/acknowledge", methods=["POST"])
def post_acknowledge_all():
    mission = database.active_mission()
    count = database.acknowledge_all(mission["id"]) if mission else 0
    return jsonify({"status": "acknowledged", "count": count})


@app.route("/model")
def get_model():
    return jsonify(processing.model_status())


@app.route("/model/reload", methods=["POST"])
def post_model_reload():
    return jsonify(processing.load_model())


@app.route("/shutdown", methods=["POST"])
def post_shutdown():
    # app.py turns SIGTERM into a clean exit that takes the Wi-Fi down.
    threading.Timer(config.SHUTDOWN_DELAY_SECONDS, os.kill, (os.getpid(), signal.SIGTERM)).start()
    return jsonify({"status": "shutting down"}), 202


@app.errorhandler(HTTPException)
def http_error(error):
    return _error(error.code, error.description)


@app.errorhandler(Exception)
def unexpected_error(error):
    app.logger.exception("Error while serving %s", request.path)
    return _error(500, "Internal server error: {}".format(error))


def _error(status, message):
    return jsonify({"error": message}), status


# ---------------------------------------------------------------------------
# What the dashboard gets (GET /dashboard)
# ---------------------------------------------------------------------------


def dashboard_document(mission, readings, alerts=(), reading_count=None, now=None):
    """Everything the dashboard shows for one mission. `readings` are oldest first."""
    grid_latitude = mission["grid_latitude"] if mission else None
    model = processing.model_status()
    return {
        "mission": _mission_json(mission, len(readings) if reading_count is None else reading_count),
        "mapCenter": {"lat": config.MAP_CENTER[0], "lng": config.MAP_CENTER[1]},
        "grid": {"steps": list(config.MAP_GRID_METERS), "cellSize": config.CELL_SIZE_METERS, "latitude": grid_latitude},
        "riskLevels": [{"label": rung["label"], "min": rung["min"], "color": rung["color"]} for rung in config.RISK_LEVELS],
        "sensors": [
            {"key": sensor.key, "label": sensor.label, "unit": sensor.unit, "digits": sensor.digits}
            for sensor in config.SENSORS
        ],
        "readings": [_reading_json(reading, grid_latitude) for reading in readings[-config.DASHBOARD_READINGS:]],
        **analysis.overview(readings, grid_latitude, now),  # cells, hotspots, summary, boat
        "alerts": [_alert_json(alert) for alert in alerts],
        "model": {"model": model["model"], "scoredBy": model["scoredBy"]},
    }


def _mission_json(mission, reading_count=None):
    if mission is None:
        return None
    result = {
        "id": mission["id"],
        "name": mission["name"],
        "active": mission["ended_at"] is None,
        "startedAt": mission["started_at"],
        "endedAt": mission["ended_at"],
    }
    if reading_count is not None:
        result["readingCount"] = reading_count
    return result


def _reading_json(reading, grid_latitude):
    located = reading["latitude"] is not None
    return {
        "id": reading["id"],
        "takenAt": reading["taken_at"],
        "lat": reading["latitude"],
        "lng": reading["longitude"],
        "gps": reading["gps"],
        **{sensor.key: reading[sensor.key] for sensor in config.SENSORS},
        "risk": reading["risk"],
        "level": analysis.level(reading["risk"]),
        "confidence": reading["confidence"],
        "drivers": reading["drivers"],
        "alert": analysis.is_alert(reading["risk"]),
        "cell": analysis.cell_id(reading["latitude"], reading["longitude"], grid_latitude) if located else None,
        "scoredBy": reading["scored_by"],
    }


def _alert_json(alert):
    return {
        "id": alert["id"],
        "kind": alert["kind"],
        "level": alert["level"],
        "title": alert["title"],
        "message": alert["message"],
        "raisedAt": alert["raised_at"],
        "acknowledged": alert["acknowledged_at"] is not None,
    }


# ---------------------------------------------------------------------------
# The CSV export (GET /missions/<id>/readings.csv)
# ---------------------------------------------------------------------------


def csv_lines(mission, readings, cell_readings):
    """A mission's readings as CSV, a line at a time. `cell_readings` builds the cells, as on the map."""
    cells = {cell["id"]: cell for cell in analysis.build_cells(cell_readings, mission["grid_latitude"])}
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")

    def line(values):
        writer.writerow(values)
        text = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate()
        return text

    # A byte-order mark, so Excel reads UTF-8.
    yield "﻿"
    yield line([
        "Reading", "Taken at", "Latitude", "Longitude", "GPS",
        *(_heading(sensor) for sensor in config.SENSORS),
        "Risk", "Level", "Confidence %", "Drivers", "Scored by", "Cell", "Cell risk", "Cell readings",
        *("{} mV".format(sensor.label) for sensor in config.SENSORS),
        "Hardware", "Device", "Sample ID", "Received at",
    ])
    for reading in readings:
        located = reading["latitude"] is not None
        cell_key = analysis.cell_id(reading["latitude"], reading["longitude"], mission["grid_latitude"]) if located else None
        cell = cells.get(cell_key, {})
        yield line([
            reading["id"], reading["taken_at"], _blank(reading["latitude"]), _blank(reading["longitude"]), reading["gps"],
            *(_blank(reading[sensor.key]) for sensor in config.SENSORS),
            reading["risk"], analysis.level(reading["risk"]), reading["confidence"],
            "; ".join(reading["drivers"]), reading["scored_by"],
            cell_key or "", _blank(cell.get("risk")), _blank(cell.get("readings")),
            *(_blank(reading["millivolts"].get(sensor.key)) for sensor in config.SENSORS),
            reading["hardware"], reading["device"], reading["sample_id"] or "", reading["received_at"],
        ])


def _heading(sensor):
    return "{} ({})".format(sensor.label, sensor.unit) if sensor.unit else sensor.label


def _blank(value):
    return "" if value is None else value
