"""Shared set-up for the server tests: a scratch database, sample data, no model."""

import json
import pickle
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

SERVER_DIR = Path(__file__).resolve().parents[1]
BOAT_DIR = SERVER_DIR.parent / "Boat"
sys.path.insert(0, str(SERVER_DIR))

import config  # noqa: E402
import database  # noqa: E402
import processing  # noqa: E402

NOW = datetime(2026, 8, 15, 16, 0, tzinfo=timezone.utc)
HERE = (42.28084, -83.74304)
NO_MODEL = Path(tempfile.gettempdir()) / "bloom-tests-no-model"


def use_scratch_database(test):
    """Point the server at empty databases for the length of one test."""
    directory = tempfile.TemporaryDirectory()
    test.addCleanup(directory.cleanup)
    for setting, name in (("READINGS_DATABASE", "bloomdata.db"), ("MISSIONS_DATABASE", "missions.db")):
        patcher = mock.patch.object(config, setting, Path(directory.name) / "data" / name)
        patcher.start()
        test.addCleanup(patcher.stop)
    database.init()
    return Path(directory.name)


def use_rules_only(test):
    """Score with the built-in rules, whatever is in Server/model/ on this machine.

    Returns an empty folder the test can put its own model in (see
    install_model); either way the rules are back in charge once it ends.
    """
    directory = tempfile.TemporaryDirectory()
    test.addCleanup(directory.cleanup)
    patcher = mock.patch.object(config, "MODEL_DIR", Path(directory.name))
    patcher.start()
    test.addCleanup(patcher.stop)
    test.addCleanup(_unload_model)
    processing.load_model()
    return Path(directory.name)


class Constant:
    """A stand-in for a trained classifier: always the same probability of a bloom."""

    def __init__(self, probability):
        self.probability = probability

    def predict_proba(self, rows):
        return [[1 - self.probability, self.probability]] * len(rows)


def write_model(directory, model, file="model.pkl", **description):
    """Put `model` and a model.json describing it in `directory`, as a person would.

    `model` is pickled, unless it is bytes (a whole model file, like ONNX).
    """
    path = Path(directory) / file
    path.write_bytes(model if isinstance(model, bytes) else pickle.dumps(model))
    description = {"file": file, "features": ["temperature", "ph", "turbidity", "conductivity"], **description}
    (Path(directory) / "model.json").write_text(json.dumps(description), encoding="utf-8")


def install_model(directory, model, file="model.pkl", **description):
    """write_model(), then load it. Returns what processing.load_model() reports."""
    write_model(directory, model, file, **description)
    with mock.patch("sys.stderr"), mock.patch("builtins.print"):
        return processing.load_model()


def _unload_model():
    with mock.patch.object(config, "MODEL_DIR", NO_MODEL):
        processing.load_model()


def payload(**overrides):
    """A reading as the boat sends it."""
    body = {
        "device": "boat",
        "hardware": "ok",
        "millivolts": {"ph": 1450.0, "temperature": 720.0, "turbidity": 2600.0, "conductivity": 2100.0},
        "gps": {"status": "fix", "latitude": HERE[0], "longitude": HERE[1]},
    }
    body.update(overrides)
    return body


def stored(identifier, risk=20, latitude=HERE[0], longitude=HERE[1], seconds_ago=0, **overrides):
    """A reading as database.mission_readings() returns it."""
    taken = (NOW - timedelta(seconds=seconds_ago)).isoformat(timespec="seconds")
    row = {
        "id": identifier,
        "mission_id": 1,
        "sample_id": "boat-test-{}".format(identifier),
        "device": "boat",
        "taken_at": taken,
        "received_at": taken,
        "latitude": latitude,
        "longitude": None if latitude is None else longitude,
        "gps": "fix" if latitude is not None else "no_fix",
        "hardware": "ok",
        **{sensor.key: None for sensor in config.SENSORS},
        "temperature": 22.4,
        "ph": 7.6,
        "turbidity": 8.0,
        "conductivity": 520.0,
        "risk": risk,
        "confidence": 72,
        "drivers": [],
        "scored_by": processing.RULES_VERSION,
    }
    row.update(overrides)
    return row


def mission(identifier=1, grid_latitude=HERE[0], ended=False, **overrides):
    """A mission as database.get_mission() returns it."""
    row = {
        "id": identifier,
        "name": "Harbor sweep",
        "started_at": "2026-08-15T11:00:00-04:00",
        "ended_at": "2026-08-15T12:00:00-04:00" if ended else None,
        "grid_latitude": grid_latitude,
    }
    row.update(overrides)
    return row
