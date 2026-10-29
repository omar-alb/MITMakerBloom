# Plugging in the trained model

The bloom risk model for this project is a **gradient-boosted tree
classifier**: XGBoost, LightGBM, or scikit-learn's
`HistGradientBoostingClassifier`. Until one is trained, the server scores with
built-in rules (see the main README); once it is in this folder, it scores
every new reading instead.

Put two files here and restart the server (or `POST /model/reload`):

1. **the trained model**, saved with joblib: `bloom.joblib`
2. **`model.json`**, which says what it is:

```json
{
  "file": "bloom.joblib",
  "version": "xgboost-2026-11-02",
  "features": ["temperature", "ph", "turbidity", "conductivity"]
}
```

| Key | |
|---|---|
| `file` | the model file, in this folder (`.joblib`, or `.pkl` if saved with `pickle`) |
| `version` | shown on the dashboard and stored with every reading the model scores (optional; the file name if left out) |
| `features` | the model's inputs, **in the order it was trained on** (the names are below) |

No code changes, and everything follows the model's numbers: marker colours,
map cells, alerts, hotspots, the headline risk, and the CSV.

## The training data

Every reading the boat has ever sent is in `Server/data/bloomdata.db` on the
Pi, kept apart from everything else for exactly this. Copy it to your own
computer and load it:

```python
import sqlite3, pandas

table = pandas.read_sql("SELECT * FROM readings", sqlite3.connect("bloomdata.db"))
# columns: id, mission_id, taken_at, latitude, longitude, temperature, ph,
#          turbidity, conductivity, nitrate, ammonium
```

The column names are the feature names below, so they go straight into the
model. Add the `bloom` label (0 or 1) yourself, for example from lab results
of water samples, matched to readings by `id` or by time and place.

Readings from a bench demo (the boat's `SIMULATE_HARDWARE`) are invented, so
leave them out. They are marked in `missions.db`, which sits next to
`bloomdata.db`:

```python
db = sqlite3.connect("bloomdata.db")
db.execute("ATTACH 'missions.db' AS missions")
table = pandas.read_sql("""SELECT r.* FROM readings r
                           JOIN missions.reading_details d ON d.reading_id = r.id
                           WHERE d.hardware != 'simulated'""", db)
```

## Training and saving it

Train on the table with its `bloom` label, and save the model with joblib:

```python
import joblib
from xgboost import XGBClassifier            # or: from lightgbm import LGBMClassifier
                                             # or: from sklearn.ensemble import HistGradientBoostingClassifier
FEATURES = ["temperature", "ph", "turbidity", "conductivity"]

model = XGBClassifier(n_estimators=200, max_depth=4).fit(table[FEATURES], table["bloom"])
joblib.dump(model, "Server/model/bloom.joblib")
```

Then write `model.json` with the same `FEATURES`. Because the model was
trained on named columns, the server checks them against `model.json` when it
loads, so a wrong order is caught instead of silently giving wrong risks.

## What the model is given

Each name in `features` is one input:

| Name | Unit | |
|---|---|---|
| `temperature` | °C | |
| `ph` | pH | |
| `turbidity` | NTU | |
| `conductivity` | µS/cm | |
| `nitrate`, `ammonium` | mg/L | missing until those probes are wired |
| `latitude`, `longitude` | degrees | missing without a GPS fix |
| `hour` | 0–24 | local time of day the reading was taken; 14:30 is 14.5 |
| `day_of_year` | 1–366 | |

The values are exactly the ones in `bloomdata.db` (and in the dashboard's
**Download CSV**). `hour` and `day_of_year` are not stored; work them out from
`taken_at` the same way the server does:

```python
from datetime import datetime

taken = [datetime.fromisoformat(value) for value in table["taken_at"]]
table["hour"] = [t.hour + t.minute / 60 for t in taken]
table["day_of_year"] = [t.timetuple().tm_yday for t in taken]
```

**Missing values are NaN**: a probe that did not report, no GPS fix, or a
probe not wired yet. Gradient-boosted trees handle NaN by themselves, so
nothing needs filling in. Nor does anything need scaling: trees do not care
about units.

## What the model must answer

A classifier with two classes, no bloom (0) and bloom (1). The server takes
`predict_proba()`'s bloom column, the probability of a bloom from 0 to 1, and
multiplies it by 100 to get the risk (Low 0–25, Moderate 26–50, High 51–75,
Severe 76–100). A regressor trained on a 0–1 risk also works: its `predict()`
is used instead. Anything outside 0–1 is refused rather than guessed at.

Confidence is still worked out from how many probes reported, and readings
scored by the model list no drivers.

## Getting it onto the Pi

This folder is part of the repository, so commit `bloom.joblib` and
`model.json` and pull them on the Pi (or copy them over with `scp`). Then
install the model's library on the Pi, **the same version you trained with**
(a joblib file only loads with the version that saved it). On Raspberry Pi OS,
`pip` goes in a virtual environment:

```text
python3 -m venv --system-site-packages ~/bloom-env
~/bloom-env/bin/pip install xgboost==3.2.0 joblib pandas    # your versions: pip show xgboost
sudo ~/bloom-env/bin/python3 Server/app.py
```

For LightGBM, install `lightgbm` instead of `xgboost`; for scikit-learn,
`scikit-learn`. `--system-site-packages` keeps the Flask installed with `apt`.
Run the service with that Python as well (HARDWARE_SETUP.md, "Run at boot").

To try it before it goes to the Pi, run `python3 Server/app.py --dev` on your
own computer, where the training libraries already are.

## Checking it

```text
curl http://10.0.0.1/model
```

```json
{"model": true, "scoredBy": "xgboost-2026-11-02", "features": ["temperature", "ph", "turbidity", "conductivity"], "message": "Scoring with Server/model/bloom.joblib (xgboost-2026-11-02).", "failures": 0}
```

`"model": false` means the built-in rules are scoring, and `message` says why.
The dashboard's footer always says what is scoring.

## When something goes wrong

- When the server starts, and on `POST /model/reload`, the model is loaded
  and tried on a typical reading (`MODEL_TRIAL_READING` in
  `Server/config.py`). If `model.json` is wrong, the file is
  missing, the library is not installed (or is a different version), the
  column names it was trained on do not match `features`, or it answers
  something other than a probability, the reason is printed and shown on
  `GET /model`, and the built-in rules keep scoring.
- If the model fails on one reading, the rules score that reading, `GET
  /model` counts the failure, and data collection carries on. Every stored
  reading records what scored it (`scored_by`, and "Scored by" in the CSV).
- A new model only scores readings that arrive after it is loaded; earlier
  readings keep the risk they were stored with.
- Only use model files you trust: loading a `.joblib` or `.pkl` file runs code
  from it.
