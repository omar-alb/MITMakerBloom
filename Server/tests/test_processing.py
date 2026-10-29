"""Processing a reading: checking it, converting it, and scoring it with the rules or a gradient-boosted tree model."""

import importlib.util
import io
import math
import random
import unittest
from datetime import datetime
from unittest import mock

from helpers import HERE, NOW, Constant, install_model, payload, use_rules_only

import analysis
import config
import processing
from processing import ReadingError

INSTALLED = {
    name: importlib.util.find_spec(name) is not None
    for name in ("numpy", "pandas", "sklearn", "xgboost", "lightgbm")
}
NOTHING = {sensor.key: None for sensor in config.SENSORS}


def reading(**values):
    return {**NOTHING, "latitude": HERE[0], "longitude": HERE[1], "taken_at": NOW.isoformat(), **values}


def process(**overrides):
    return processing.process(payload(**overrides), now=NOW)


class ConversionTests(unittest.TestCase):
    def test_temperature_follows_the_configured_sensor(self):
        self.assertEqual(processing.to_units({"temperature": 750.0})["temperature"], 25.0)  # TMP36
        with mock.patch.object(config, "TEMPERATURE_SENSOR", "lm35"):
            self.assertEqual(processing.to_units({"temperature": 250.0})["temperature"], 25.0)

    def test_ph_reads_the_reference_buffer_and_stays_on_the_scale(self):
        self.assertEqual(processing.to_units({"ph": config.PH_REFERENCE_MV})["ph"], config.PH_REFERENCE_PH)
        self.assertEqual(processing.to_units({"ph": -100000.0})["ph"], 14.0)

    def test_turbidity_runs_from_clear_to_the_standard(self):
        self.assertEqual(processing.to_units({"turbidity": config.TURBIDITY_CLEAR_MV})["turbidity"], 0.0)
        dirty = processing.to_units({"turbidity": config.TURBIDITY_DIRTY_MV})["turbidity"]
        self.assertEqual(dirty, config.TURBIDITY_DIRTY_NTU)

    def test_conductivity_is_not_flattened_above_the_calibration_point(self):
        # Capping it there would report every brackish or runoff-laden
        # sample as exactly the standard, hiding what the score reacts to.
        at = processing.to_units({"conductivity": config.CONDUCTIVITY_CAL_MV})["conductivity"]
        above = processing.to_units({"conductivity": config.CONDUCTIVITY_CAL_MV + 400})["conductivity"]
        self.assertAlmostEqual(at, config.CONDUCTIVITY_CAL_US_CM, delta=1)
        self.assertGreater(above, at)
        self.assertEqual(processing.to_units({"conductivity": 0.0})["conductivity"], 0.0)

    def test_conductivity_is_compensated_to_25_degrees(self):
        warm = processing.to_units({"conductivity": config.CONDUCTIVITY_CAL_MV, "temperature": 800.0})
        cold = processing.to_units({"conductivity": config.CONDUCTIVITY_CAL_MV, "temperature": 700.0})
        self.assertLess(warm["conductivity"], cold["conductivity"])

    def test_ion_selective_probes_are_tenfold_per_decade(self):
        reference = processing.to_units({"nitrate": config.NITRATE_REFERENCE_MV})["nitrate"]
        one_decade = processing.to_units({"nitrate": config.NITRATE_REFERENCE_MV + config.NITRATE_MV_PER_DECADE})["nitrate"]
        self.assertEqual(reference, config.NITRATE_REFERENCE_MG_L)
        self.assertAlmostEqual(one_decade, config.NITRATE_REFERENCE_MG_L * 10, places=2)

    def test_every_sensor_has_a_conversion_and_is_rounded_to_its_digits(self):
        units = processing.to_units({sensor.key: 1234.5678 for sensor in config.SENSORS})
        for sensor in config.SENSORS:
            with self.subTest(sensor=sensor.key):
                self.assertEqual(units[sensor.key], round(units[sensor.key], sensor.digits))

    def test_a_probe_that_did_not_report_stays_empty(self):
        units = processing.to_units({"ph": 1450.0})
        self.assertIsNotNone(units["ph"])
        self.assertTrue(all(units[key] is None for key in units if key != "ph"))


class ReadingTests(unittest.TestCase):
    def setUp(self):
        use_rules_only(self)

    def test_a_boat_reading_becomes_a_scored_row(self):
        row = process(sample_id="boat-abc-1")

        self.assertEqual(row["sample_id"], "boat-abc-1")
        self.assertEqual(row["device"], "boat")
        self.assertEqual((row["latitude"], row["longitude"]), HERE)
        self.assertEqual(row["gps"], "fix")
        self.assertEqual(row["millivolts"]["ph"], 1450.0)
        self.assertIsNotNone(row["ph"])
        self.assertIsNone(row["nitrate"])
        self.assertTrue(0 <= row["risk"] <= 100)
        self.assertEqual(row["scored_by"], "rules-v2")

    def test_probes_that_did_not_report_and_unknown_probes_turn_into_nothing(self):
        # A probe that did not report stays empty rather than be invented; a
        # name the server does not know is ignored.
        row = process(millivolts={"ph": 1450.0, "dissolved_gold": 3.0})
        self.assertIsNone(row["nitrate"])
        self.assertNotIn("dissolved_gold", row["millivolts"])

    def test_a_non_finite_or_non_numeric_value_is_skipped(self):
        # JSON allows NaN, and a NaN in the database would make /dashboard
        # invalid JSON for the browser.
        row = process(millivolts={"ph": float("nan"), "turbidity": "2600", "temperature": 720.0})
        self.assertIsNone(row["ph"])
        self.assertIsNone(row["turbidity"])
        self.assertIsNotNone(row["temperature"])

    def test_a_reading_without_any_known_probe_is_refused(self):
        for millivolts in ({}, {"ph": None}, "1450", None):
            with self.subTest(millivolts=millivolts), self.assertRaises(ReadingError):
                process(millivolts=millivolts)

    def test_a_queued_reading_keeps_the_time_it_was_taken(self):
        row = process(age_seconds=120)
        taken = datetime.fromisoformat(row["taken_at"])
        self.assertEqual((NOW - taken).total_seconds(), 120)
        self.assertEqual(datetime.fromisoformat(row["received_at"]), NOW)

    def test_an_implausible_age_falls_back_to_the_arrival_time(self):
        for age in (-5, 10 * 24 * 3600, "soon", float("nan"), True):
            with self.subTest(age=age):
                self.assertEqual(datetime.fromisoformat(process(age_seconds=age)["taken_at"]), NOW)

    def test_device_is_required_and_sample_id_is_bounded(self):
        for device in (None, "", "   ", 42):
            with self.subTest(device=device), self.assertRaises(ReadingError):
                process(device=device)
        with self.assertRaises(ReadingError):
            process(sample_id="x" * 500)
        self.assertIsNone(process()["sample_id"])

    def test_the_body_must_be_an_object(self):
        for body in (None, [], "reading", 3):
            with self.subTest(body=body), self.assertRaises(ReadingError):
                processing.process(body)


class GpsTests(unittest.TestCase):
    def setUp(self):
        use_rules_only(self)

    def test_a_reading_without_a_fix_keeps_its_chemistry_but_has_no_position(self):
        for status in ("no_fix", "unavailable"):
            with self.subTest(status=status):
                row = process(gps={"status": status, "latitude": 1.0, "longitude": 2.0})
                self.assertEqual((row["latitude"], row["longitude"], row["gps"]), (None, None, status))
                self.assertIsNotNone(row["ph"])

    def test_a_stale_or_simulated_position_is_kept(self):
        for status in ("stale", "simulated"):
            with self.subTest(status=status):
                self.assertEqual(process(gps={"status": status, "latitude": 1.5, "longitude": 2.5})["latitude"], 1.5)

    def test_no_gps_block_means_no_position(self):
        body = payload()
        del body["gps"]
        row = processing.process(body, now=NOW)
        self.assertEqual((row["gps"], row["latitude"]), ("unavailable", None))

    def test_a_bad_position_is_refused(self):
        for gps in (
            {"status": "fix", "latitude": 91.0, "longitude": 0.0},
            {"status": "fix", "latitude": 0.0, "longitude": -181.0},
            {"status": "fix", "latitude": float("nan"), "longitude": 0.0},
            {"status": "fix", "latitude": "north", "longitude": 0.0},
            {"status": "fix"},
            {"status": "lost"},
            "fix",
        ):
            with self.subTest(gps=gps), self.assertRaises(ReadingError):
                process(gps=gps)


class RuleTests(unittest.TestCase):
    def test_the_weights_add_up_to_one(self):
        self.assertAlmostEqual(sum(sensor.weight for sensor in config.SENSORS), 1.0)

    def test_one_elevated_probe_cannot_raise_an_alert_on_its_own(self):
        alone = processing.score_with_rules(reading(turbidity=40.0))
        backed_up = processing.score_with_rules(reading(
            turbidity=40.0, temperature=29.0, ph=9.0, conductivity=1100.0, nitrate=6.0, ammonium=1.1
        ))
        self.assertFalse(analysis.is_alert(alone["risk"]))
        self.assertEqual(analysis.level(backed_up["risk"]), "Severe")
        self.assertLess(alone["confidence"], backed_up["confidence"])

    def test_the_four_core_probes_alone_count_as_full_evidence(self):
        # With both nutrient electrodes down, the rest still score in full.
        core = sum(sensor.weight for sensor in config.SENSORS if sensor.key in ("temperature", "ph", "turbidity", "conductivity"))
        self.assertGreaterEqual(core, config.FULL_EVIDENCE_SHARE)
        everything_high = reading(temperature=40.0, ph=10.0, turbidity=50.0, conductivity=2000.0)
        self.assertEqual(processing.score_with_rules(everything_high)["risk"], 100)

    def test_a_missing_probe_is_left_out_not_counted_as_clean(self):
        # Probes that did not report must not drag the risk down as if they
        # had measured clean water; a measured clean value does.
        high = reading(temperature=40.0, ph=10.0, turbidity=50.0, conductivity=2000.0)
        self.assertEqual(processing.score_with_rules(high)["risk"], 100)
        self.assertLess(processing.score_with_rules({**high, "nitrate": 0.0})["risk"], 100)
        self.assertEqual(processing.score_with_rules(reading())["risk"], 0)

    def test_drivers_name_what_pushed_the_risk_up_strongest_first(self):
        result = processing.score_with_rules(reading(turbidity=40.0, temperature=26.0, ph=7.0, conductivity=300.0))
        self.assertEqual(result["drivers"], ["high turbidity", "warm water"])

    def test_confidence_grows_with_the_probes_that_reported(self):
        self.assertEqual(processing.confidence(reading()), 30)
        self.assertEqual(processing.confidence(reading(**{sensor.key: 1.0 for sensor in config.SENSORS})), 90)
        four = processing.confidence(reading(temperature=20.0, ph=7.0, turbidity=1.0, conductivity=300.0))
        self.assertTrue(30 < four < 90)


# ---------------------------------------------------------------------------
# Trained models
# ---------------------------------------------------------------------------


class Recorder(Constant):
    """Also remembers every row it is given."""

    rows = []

    def predict_proba(self, rows):
        Recorder.rows.extend([float(value) for value in row] for row in rows)
        return super().predict_proba(rows)


class Failing(Constant):
    """Cannot cope without turbidity (the third feature)."""

    def predict_proba(self, rows):
        if any(math.isnan(row[2]) for row in rows):
            raise ValueError("needs turbidity")
        return super().predict_proba(rows)


class Regressor:
    """Has predict() only: turbidity / 100 is its probability."""

    def predict(self, rows):
        return [row[2] / 100 for row in rows]


class Percentage(Regressor):
    def predict(self, rows):
        return [73.0] * len(rows)


class ThreeClasses(Constant):
    def predict_proba(self, rows):
        return [[0.2, 0.3, 0.5]] * len(rows)


@unittest.skipUnless(INSTALLED["numpy"], "numpy is not installed")
class ModelTests(unittest.TestCase):
    def setUp(self):
        self.folder = use_rules_only(self)
        Recorder.rows = []

    def broken(self, model=None, **description):
        """Install a model that must not load; the rules keep scoring. Returns the reason."""
        status = install_model(self.folder, Constant(0.5) if model is None else model, **description)
        self.assertFalse(status["model"])
        self.assertEqual(processing.score(reading(turbidity=31.0))["scored_by"], "rules-v2")
        return status["error"]

    def broken_json(self, text):
        (self.folder / "model.json").write_text(text, encoding="utf-8")
        with mock.patch("sys.stderr"), mock.patch("builtins.print"):
            status = processing.load_model()
        self.assertFalse(status["model"])
        return status["error"]

    def test_without_model_json_the_rules_score(self):
        status = processing.load_model()
        self.assertFalse(status["model"])
        self.assertEqual(status["scoredBy"], "rules-v2")
        self.assertIn("No model.json", status["message"])

    def test_a_trained_model_scores_every_new_reading(self):
        status = install_model(self.folder, Constant(0.31), version="unit-test-v1")
        self.assertTrue(status["model"], status.get("error"))
        self.assertEqual(status["scoredBy"], "unit-test-v1")

        result = processing.score(reading(turbidity=31.0))
        self.assertEqual((result["risk"], result["scored_by"], result["drivers"]), (31, "unit-test-v1", []))
        self.assertEqual(result["confidence"], processing.confidence(reading(turbidity=31.0)))
        self.assertEqual(process()["scored_by"], "unit-test-v1")

    def test_the_version_defaults_to_the_file_name(self):
        self.assertEqual(install_model(self.folder, Constant(0.5), file="bloom.pkl")["scoredBy"], "bloom.pkl")

    def test_the_model_gets_the_features_model_json_lists_in_that_order(self):
        install_model(self.folder, Recorder(0.5), features=["turbidity", "nitrate", "hour", "day_of_year", "latitude"])
        Recorder.rows = []
        processing.score(reading(turbidity=31.0, taken_at="2026-08-15T14:30:00-04:00"))

        turbidity, nitrate, hour, day_of_year, latitude = Recorder.rows[-1]
        self.assertEqual((turbidity, hour, day_of_year, latitude), (31.0, 14.5, 227, HERE[0]))
        self.assertTrue(math.isnan(nitrate), "a probe that did not report is missing, not zero")

    def test_a_regressor_answers_with_its_prediction(self):
        install_model(self.folder, Regressor())
        self.assertEqual(processing.score(reading(turbidity=31.0))["risk"], 31)

    def test_a_model_json_mistake_leaves_the_rules_scoring_and_says_why(self):
        for description, reason in (
            ({"features": ["temperature", "dissolved_gold"]}, '"features"'),
            ({"features": ["ph", "ph"]}, "twice"),
            ({"features": []}, '"features"'),
            ({"file": "bloom.onnx"}, ".joblib"),
            ({"sequence_length": 8}, "only these keys"),
        ):
            with self.subTest(description=description):
                self.assertIn(reason, self.broken(**description))

    def test_a_missing_or_unreadable_model_leaves_the_rules_scoring(self):
        self.assertIn("not valid JSON", self.broken_json("{file: model.pkl"))
        self.assertIn("gone.pkl", self.broken_json('{"file": "gone.pkl", "features": ["ph"]}'))
        self.broken(model=b"not a pickle")

    def test_an_answer_that_is_not_a_probability_is_refused(self):
        for model, reason in (
            (Percentage(), "73"),
            (Constant(1.7), "1.7"),
            (Constant(float("nan")), "nan"),
            (ThreeClasses(0.5), "two classes"),
            (object(), "predict"),
        ):
            with self.subTest(model=type(model).__name__):
                self.assertIn(reason, self.broken(model=model))

    def test_a_model_that_fails_on_one_reading_is_covered_by_the_rules(self):
        install_model(self.folder, Failing(0.9))
        with mock.patch("sys.stderr"):
            covered = processing.score(reading(ph=7.5))
        self.assertEqual(covered["scored_by"], "rules-v2")
        self.assertEqual(processing.model_status()["failures"], 1)
        self.assertEqual(processing.score(reading(turbidity=3.0))["risk"], 90)

    def test_removing_model_json_and_reloading_goes_back_to_the_rules(self):
        install_model(self.folder, Constant(0.9))
        (self.folder / "model.json").unlink()
        self.assertFalse(processing.load_model()["model"])
        self.assertEqual(processing.score(reading(turbidity=3.0))["scored_by"], "rules-v2")

    @unittest.skipUnless(INSTALLED["pandas"], "pandas is not installed")
    def test_column_names_from_training_must_match_model_json(self):
        named = Constant(0.5)
        named.feature_names_in_ = ["temperature", "ph", "turbidity", "conductivity"]
        self.assertTrue(install_model(self.folder, named)["model"])
        named.feature_names_in_ = ["ph", "temperature", "turbidity", "conductivity"]
        self.assertIn("trained on", self.broken(model=named))


def training_data(rows=400):
    """A made-up table with a plain pattern: warm, cloudy water is a bloom."""
    import pandas

    generator = random.Random(7)
    features, labels = [], []
    for _ in range(rows):
        temperature, turbidity = generator.uniform(10, 32), generator.uniform(0, 60)
        features.append([temperature, generator.uniform(6.5, 9.5), turbidity, generator.uniform(200, 1200)])
        labels.append(int(temperature > 22 and turbidity > 20))
    return pandas.DataFrame(features, columns=["temperature", "ph", "turbidity", "conductivity"]), labels


WARM_AND_CLOUDY = reading(temperature=29.0, ph=8.4, turbidity=45.0, conductivity=700.0)
COLD_AND_CLEAR = reading(temperature=14.0, ph=7.4, turbidity=2.0, conductivity=400.0)


@unittest.skipUnless(INSTALLED["pandas"], "pandas is not installed")
class GradientBoostedTreeTests(unittest.TestCase):
    """Real gradient-boosted tree models, trained on a table and saved with joblib, as model/README.md says."""

    def setUp(self):
        self.folder = use_rules_only(self)

    def assert_works(self, model):
        import joblib

        buffer = io.BytesIO()
        joblib.dump(model, buffer)
        status = install_model(self.folder, buffer.getvalue(), file="bloom.joblib")
        self.assertTrue(status["model"], status.get("error"))

        warm = processing.score(WARM_AND_CLOUDY)
        self.assertEqual(warm["scored_by"], status["scoredBy"])
        self.assertGreater(warm["risk"], processing.score(COLD_AND_CLEAR)["risk"] + 40)
        # A probe that did not report reaches the trees as NaN, which they handle.
        self.assertEqual(processing.score({**WARM_AND_CLOUDY, "turbidity": None})["scored_by"], status["scoredBy"])

    @unittest.skipUnless(INSTALLED["xgboost"], "xgboost is not installed")
    def test_xgboost(self):
        from xgboost import XGBClassifier

        self.assert_works(XGBClassifier(n_estimators=40, max_depth=3).fit(*training_data()))

    @unittest.skipUnless(INSTALLED["lightgbm"], "lightgbm is not installed")
    def test_lightgbm(self):
        from lightgbm import LGBMClassifier

        self.assert_works(LGBMClassifier(n_estimators=40, min_child_samples=5, verbose=-1).fit(*training_data()))

    @unittest.skipUnless(INSTALLED["sklearn"], "scikit-learn is not installed")
    def test_scikit_learn(self):
        from sklearn.ensemble import HistGradientBoostingClassifier

        self.assert_works(HistGradientBoostingClassifier(max_iter=60, random_state=0).fit(*training_data()))


if __name__ == "__main__":
    unittest.main()
