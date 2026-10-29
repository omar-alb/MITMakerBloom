"""What happens to a reading when it arrives: it is checked, converted, and scored.

The boat sends raw probe millivolts and its GPS state:

    {
      "device": "boat",
      "sample_id": "boat-3f9a1c0d7b2e-17",
      "age_seconds": 12.5,
      "hardware": "ok",
      "millivolts": {"ph": 1450.0, "temperature": 720.0, "turbidity": 2600.0, "conductivity": 2100.0},
      "gps": {"status": "fix", "latitude": 42.28084, "longitude": -83.74304}
    }

process() turns that into the row the database stores: every sensor in its
unit (pH, °C, NTU, ...), the time the reading was taken, and its bloom risk
from 0 to 100. The risk comes from the trained gradient-boosted tree model
in Server/model/ when there is one (see model/README.md), and from the
built-in rules otherwise.

This is the only place a reading is scored. Alerts, map cells, hotspots, and
the headline risk are all built from the stored risk (analysis.py), so none
of them can disagree with it. A payload that cannot be used raises
ReadingError, which the server answers with HTTP 400 and the reason.
"""

import json
import pickle
import sys
from datetime import datetime, timedelta
from math import isfinite

import config

GPS_STATUSES = ("fix", "stale", "simulated", "no_fix", "unavailable")
LOCATED_GPS_STATUSES = ("fix", "stale", "simulated")
HARDWARE_STATUSES = ("ok", "partial", "simulated")
COORDINATE_DIGITS = 6  # about 10 cm

# Facts from the sensors' datasheets and standards, not settings.
MV_PER_DEGREE = 10.0  # TMP36 and LM35 alike
TMP36_ZERO_MV = 500.0  # the TMP36 reads 500 mV at 0 °C; the LM35 reads 0 mV
PH_SCALE = (0.0, 14.0)
CONDUCTIVITY_REFERENCE_C = 25.0  # conductivity is reported as it would be at 25 °C

RULES_VERSION = "rules-v2"
TOTAL_WEIGHT = sum(sensor.weight for sensor in config.SENSORS)

# What a trained model can be given, by the names its model.json uses.
FEATURES = (*(sensor.key for sensor in config.SENSORS), "latitude", "longitude", "hour", "day_of_year")
MODEL_FORMATS = (".joblib", ".pkl")


class ReadingError(ValueError):
    """The payload cannot be stored. The message says why."""


def process(payload, now=None):
    """Check a POST /readings body and return the row to store, scored."""
    if not isinstance(payload, dict):
        raise ReadingError("A reading must be a JSON object.")
    now = now or datetime.now().astimezone()

    device = _text(payload, "device", required=True)
    sample_id = _text(payload, "sample_id")
    millivolts = _millivolts(payload.get("millivolts"))
    gps, latitude, longitude = _gps(payload.get("gps"))
    taken_at = now - timedelta(seconds=_age(payload.get("age_seconds")))

    row = {
        "sample_id": sample_id,
        "device": device,
        "taken_at": taken_at.isoformat(timespec="seconds"),
        "received_at": now.isoformat(timespec="seconds"),
        "latitude": latitude,
        "longitude": longitude,
        "gps": gps,
        "hardware": payload.get("hardware") if payload.get("hardware") in HARDWARE_STATUSES else "unknown",
        **to_units(millivolts),
        "millivolts": millivolts,
    }
    row.update(score(row))
    return row


# ---------------------------------------------------------------------------
# Checking the payload
# ---------------------------------------------------------------------------


def _millivolts(raw):
    """The known sensors' millivolts. Unknown names and non-numbers are skipped."""
    if not isinstance(raw, dict):
        raise ReadingError('millivolts must be an object, like {"ph": 1450.0}.')
    values = {}
    for sensor in config.SENSORS:
        number = _number(raw.get(sensor.key))
        if number is not None:
            values[sensor.key] = number
    if not values:
        names = ", ".join(sensor.key for sensor in config.SENSORS)
        raise ReadingError("millivolts has no value for any known sensor ({}).".format(names))
    return values


def _gps(raw):
    """(status, latitude, longitude). Only a status with a position keeps one."""
    if raw is None:
        return "unavailable", None, None
    if not isinstance(raw, dict) or raw.get("status") not in GPS_STATUSES:
        raise ReadingError("gps.status must be one of: {}.".format(", ".join(GPS_STATUSES)))
    status = raw["status"]
    if status not in LOCATED_GPS_STATUSES:
        return status, None, None
    return status, _coordinate(raw, "latitude", 90), _coordinate(raw, "longitude", 180)


def _coordinate(gps, key, limit):
    number = _number(gps.get(key))
    if number is None or not -limit <= number <= limit:
        raise ReadingError("gps.{} must be a number from -{} to {}.".format(key, limit, limit))
    return round(number, COORDINATE_DIGITS)


def _age(value):
    """Seconds since the boat took the reading; 0 when unknown or implausible."""
    number = _number(value)
    return number if number is not None and 0 <= number <= config.READING_MAX_AGE_SECONDS else 0


def _text(payload, key, required=False):
    value = payload.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ReadingError("{} is required.".format(key))
        return None
    if not isinstance(value, str):
        raise ReadingError("{} must be text.".format(key))
    if len(value.strip()) > config.TEXT_MAX_LENGTH:
        raise ReadingError("{} must be at most {} characters.".format(key, config.TEXT_MAX_LENGTH))
    return value.strip()


def _number(value):
    """A finite int or float, else None. (JSON allows NaN; the database must not.)"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if isfinite(value) else None


# ---------------------------------------------------------------------------
# Millivolts to units
# ---------------------------------------------------------------------------


def to_units(millivolts):
    """Every sensor's value in its unit, None for a probe that did not report.

    Values are rounded to the sensor's `digits`, so what is stored is exactly
    what the dashboard and the CSV show.
    """
    temperature = _temperature(millivolts.get("temperature"))
    values = {
        "temperature": temperature,
        "ph": _ph(millivolts.get("ph")),
        "turbidity": _turbidity(millivolts.get("turbidity")),
        "conductivity": _conductivity(millivolts.get("conductivity"), temperature),
        "nitrate": _ion_selective(
            millivolts.get("nitrate"),
            config.NITRATE_REFERENCE_MV,
            config.NITRATE_REFERENCE_MG_L,
            config.NITRATE_MV_PER_DECADE,
        ),
        "ammonium": _ion_selective(
            millivolts.get("ammonium"),
            config.AMMONIUM_REFERENCE_MV,
            config.AMMONIUM_REFERENCE_MG_L,
            config.AMMONIUM_MV_PER_DECADE,
        ),
    }
    return {
        sensor.key: None if values[sensor.key] is None else round(values[sensor.key], sensor.digits)
        for sensor in config.SENSORS
    }


def _temperature(mv):
    if mv is None:
        return None
    zero = 0.0 if config.TEMPERATURE_SENSOR == "lm35" else TMP36_ZERO_MV
    return _clamp((mv - zero) / MV_PER_DEGREE, *config.TEMPERATURE_RANGE_C)


def _ph(mv):
    if mv is None:
        return None
    return _clamp(config.PH_REFERENCE_PH + (mv - config.PH_REFERENCE_MV) / config.PH_MV_PER_PH, *PH_SCALE)


def _turbidity(mv):
    if mv is None:
        return None
    # Linear between clear water and the calibration standard; the probe's
    # voltage falls as the water gets cloudier.
    share = (config.TURBIDITY_CLEAR_MV - mv) / (config.TURBIDITY_CLEAR_MV - config.TURBIDITY_DIRTY_MV)
    return _clamp(share, 0, 1) * config.TURBIDITY_DIRTY_NTU


def _conductivity(mv, temperature):
    if mv is None:
        return None
    # Linear through the calibration point and beyond it, not capped there:
    # brackish or runoff-laden water must still read higher than the standard.
    share = (mv - config.CONDUCTIVITY_ZERO_MV) / (config.CONDUCTIVITY_CAL_MV - config.CONDUCTIVITY_ZERO_MV)
    water = CONDUCTIVITY_REFERENCE_C if temperature is None else temperature
    compensation = 1 + config.CONDUCTIVITY_PER_DEGREE * (water - CONDUCTIVITY_REFERENCE_C)
    # (The floor only guards against dividing by nothing if the setting is far off.)
    return max(0.0, share * config.CONDUCTIVITY_CAL_US_CM / max(compensation, 0.1))


def _ion_selective(mv, reference_mv, reference_mg_l, mv_per_decade):
    if mv is None:
        return None
    # Nernst: every mv_per_decade of change is a tenfold change in concentration.
    limit = config.ION_SELECTIVE_MAX_DECADES
    decades = _clamp((mv - reference_mv) / mv_per_decade, -limit, limit)
    return _clamp(reference_mg_l * 10**decades, 0, config.ION_SELECTIVE_MAX_MG_L)


def _clamp(value, low, high):
    return min(high, max(low, value))


# ---------------------------------------------------------------------------
# The bloom risk
# ---------------------------------------------------------------------------


def score(reading):
    """The bloom risk of one reading: the trained model's when one is loaded, else the rules'.

    `reading` is a row as process() builds it. Returns the risk (0-100),
    confidence (0-100), drivers (what pushed the risk up, strongest first;
    only the rules name them), and scored_by (which model produced it).
    """
    if _model is not None:
        try:
            probability = _as_probability(_model["run"](_features(reading, _model["features"])))
        except Exception as error:
            # One bad reading must not stop data collection: the rules score
            # it instead, and the row records that they did.
            _model_failed(error)
        else:
            return {
                "risk": round(probability * 100),
                "confidence": confidence(reading),
                "drivers": [],
                "scored_by": _model["version"],
            }
    return score_with_rules(reading)


def score_with_rules(reading):
    """The built-in score: a weighted average of how risky each probe looks.

    Each sensor with a value scores 0 at or below its `low` threshold, 100 at
    or above its `high`, and in proportion between. Sensors without a value
    are left out rather than counted as clean water. The average is then
    scaled down when the probes that did report carry less than
    FULL_EVIDENCE_SHARE of the total weight, so one elevated probe on its own
    stays below the alert levels.
    """
    parts = {}
    for sensor in config.SENSORS:
        value = reading.get(sensor.key)
        if value is not None:
            parts[sensor] = _ramp(value, sensor.low, sensor.high)

    if parts:
        reported_weight = sum(sensor.weight for sensor in parts)
        average = sum(part * sensor.weight for sensor, part in parts.items()) / reported_weight
        evidence = min(1.0, reported_weight / TOTAL_WEIGHT / config.FULL_EVIDENCE_SHARE)
        value = average * evidence
    else:
        value = 0

    strongest_first = sorted(parts.items(), key=lambda item: -item[1])
    return {
        "risk": round(value),
        "confidence": confidence(reading),
        "drivers": [sensor.driver for sensor, part in strongest_first if part >= config.DRIVER_THRESHOLD],
        "scored_by": RULES_VERSION,
    }


def confidence(reading):
    """How much evidence is behind a score, within config.CONFIDENCE_RANGE (30 to 90).

    It grows with the share of the scoring weight that actually reported, and
    stops short of 100 because even a full set of probes gives an estimate.
    """
    lowest, highest = config.CONFIDENCE_RANGE
    reported = sum(sensor.weight for sensor in config.SENSORS if reading.get(sensor.key) is not None)
    return round(lowest + (highest - lowest) * reported / TOTAL_WEIGHT)


def _ramp(value, low, high):
    if high == low:
        return 100.0 if value >= high else 0.0
    return min(100.0, max(0.0, (value - low) / (high - low) * 100))


# ---------------------------------------------------------------------------
# A trained gradient-boosted tree model (Server/model/, see model/README.md)
# ---------------------------------------------------------------------------

_model = None  # while a trained model is scoring: {"run", "file", "version", "features"}
_status = {}


def load_model():
    """Score with the trained model in Server/model/ when there is one, else with the rules.

    Runs at startup and on POST /model/reload. Never raises: a model that
    cannot be loaded, or fails on a typical reading, is reported in the log
    and on GET /model, and the rules keep scoring.
    """
    global _model, _status
    _model = None
    description = config.MODEL_DIR / "model.json"
    if not description.exists():
        _status = {"message": "No model.json in Server/model/, so the built-in rules are scoring."}
        return model_status()

    try:
        spec = _read_description(description)
        run = _load(config.MODEL_DIR / spec["file"], spec["features"])
        # Try it on a typical reading now, so a broken model shows up at
        # startup rather than on the first reading in the field.
        _as_probability(run(_features(_typical_reading(), spec["features"])))
    except Exception as error:
        _status = {
            "message": "The model in Server/model/ could not be used, so the built-in rules are scoring: {}".format(error),
            "error": str(error),
        }
        print(_status["message"], file=sys.stderr)
        return model_status()

    _model = {"run": run, **spec}
    _status = {"message": "Scoring with Server/model/{} ({}).".format(spec["file"], spec["version"]), "failures": 0}
    print(_status["message"])
    return model_status()


def model_status():
    """Which model is scoring new readings, and why. Shown on GET /model."""
    status = {"model": _model is not None, "scoredBy": _model["version"] if _model else RULES_VERSION, **_status}
    if _model:
        status["features"] = _model["features"]
    return status


def _read_description(path):
    """model.json, checked: the model's file and its inputs, in training order."""
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ValueError("model.json is not valid JSON: {}".format(error)) from None
    allowed = ("file", "version", "features")
    if not isinstance(spec, dict) or set(spec) - set(allowed):
        raise ValueError("model.json must be an object with only these keys: {}".format(", ".join(allowed)))

    file, features = spec.get("file"), spec.get("features")
    if not isinstance(file, str) or not file.endswith(MODEL_FORMATS):
        raise ValueError('"file" must name the model file, ending in {}'.format(" or ".join(MODEL_FORMATS)))
    if not isinstance(features, list) or not features or any(name not in FEATURES for name in features):
        raise ValueError('"features" must list the model\'s inputs, in training order, from: {}'.format(", ".join(FEATURES)))
    if len(set(features)) != len(features):
        raise ValueError('"features" lists the same input twice')
    return {"file": file, "version": str(spec.get("version") or file), "features": features}


def _load(path, features):
    """The model as a function: one row of inputs in, the probability of a bloom out.

    The file is a classifier saved with joblib (or pickle): XGBClassifier,
    LGBMClassifier, or scikit-learn's HistGradientBoostingClassifier. Missing
    inputs are NaN, which gradient-boosted trees handle by themselves.
    """
    if not path.exists():
        raise FileNotFoundError("{} is not in Server/model/".format(path.name))
    if path.suffix == ".joblib":
        import joblib

        model = joblib.load(path)
    else:
        with open(path, "rb") as file:
            model = pickle.load(file)

    names = getattr(model, "feature_names_in_", None)
    if names is not None:
        # Trained on a table with named columns: check they are the inputs
        # model.json lists, in the same order, and give it a table back.
        import pandas

        names = [str(name) for name in names]
        if names != features:
            raise ValueError("model.json lists {} but the model was trained on {}".format(features, names))

        def table(row):
            return pandas.DataFrame([row], columns=names)
    else:
        import numpy

        def table(row):
            return numpy.array([row], dtype=float)

    if hasattr(model, "predict_proba"):
        def run(row):
            probabilities = model.predict_proba(table(row))[0]
            if len(probabilities) != 2:
                raise ValueError("the classifier must have two classes (no bloom, bloom), not {}".format(len(probabilities)))
            return probabilities[1]

        return run
    if hasattr(model, "predict"):  # a regressor trained on a 0-1 risk
        return lambda row: model.predict(table(row))[0]
    raise TypeError("the model has neither predict_proba() nor predict()")


def _features(reading, names):
    """The model's inputs for one reading. A missing value (no probe, no GPS fix) is NaN."""
    taken = datetime.fromisoformat(reading["taken_at"])
    known = {
        **{name: reading.get(name) for name in FEATURES},
        "hour": taken.hour + taken.minute / 60,  # local time of day, 0-24
        "day_of_year": taken.timetuple().tm_yday,
    }
    return [float("nan") if known[name] is None else float(known[name]) for name in names]


def _as_probability(value):
    """A model's answer as a float from 0 to 1. Anything else is an error."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise TypeError("the model must answer with a probability from 0 to 1, not {!r}".format(value)) from None
    if not 0.0 <= number <= 1.0:  # also rejects NaN
        raise ValueError("the model must answer with a probability from 0 to 1, not {!r}".format(value))
    return number


def _model_failed(error):
    _status["failures"] = _status.get("failures", 0) + 1
    _status["lastError"] = str(error)
    if _status["failures"] == 1:
        print("The model failed on a reading, so the rules scored it: {}".format(error), file=sys.stderr)


def _typical_reading():
    """config.MODEL_TRIAL_READING, shaped exactly like a real row, to try a model on as it loads."""
    return {
        **{sensor.key: config.MODEL_TRIAL_READING.get(sensor.key) for sensor in config.SENSORS},
        "latitude": config.MAP_CENTER[0],
        "longitude": config.MAP_CENTER[1],
        "taken_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
