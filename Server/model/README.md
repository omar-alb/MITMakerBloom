# Plugging in the trained model

The model is a **gradient-boosted tree classifier** (XGBoost, LightGBM, or
scikit-learn's `HistGradientBoostingClassifier`). Until one is here, the
server scores with built-in rules.

Put two files in this folder and restart the server (or `POST /model/reload`):

1. the model, saved with joblib: `bloom.joblib`
2. `model.json`:

```json
{
  "file": "bloom.joblib",
  "version": "xgboost-v1",
  "features": ["temperature", "ph", "turbidity", "conductivity", "nitrate", "ammonium"]
}
```

| Key | |
|---|---|
| `file` | the model file (`.joblib`, or `.pkl` saved with `pickle`) |
| `version` | stored with every reading it scores (defaults to the file name) |
| `features` | the inputs, **in training order** |

No code changes: markers, cells, alerts, hotspots, and the headline all follow it.

## Training data

Copy `Server/data/bloomdata.db` off the Pi, with `missions.db` beside it to
leave out bench-demo readings:

```python
import sqlite3, pandas

db = sqlite3.connect("bloomdata.db")
db.execute("ATTACH 'missions.db' AS missions")
table = pandas.read_sql("""SELECT r.* FROM readings r
                           JOIN missions.reading_details d ON d.reading_id = r.id
                           WHERE d.hardware != 'simulated'""", db)
# columns: id, mission_id, taken_at, latitude, longitude, temperature, ph,
#          turbidity, conductivity, nitrate, ammonium
```

Add a `bloom` label (0 or 1) yourself, e.g. from lab results matched by `id`
or by time and place.

## Training and saving

```python
import joblib
from xgboost import XGBClassifier   # or lightgbm.LGBMClassifier, or sklearn's HistGradientBoostingClassifier

FEATURES = ["temperature", "ph", "turbidity", "conductivity", "nitrate", "ammonium"]
model = XGBClassifier().fit(table[FEATURES], table["bloom"])
joblib.dump(model, "Server/model/bloom.joblib")
```

Write `model.json` with the same `FEATURES`. The server checks the model's
column names against it when it loads.

## Inputs

| Name | Unit |
|---|---|
| `temperature` | °C |
| `ph` | pH |
| `turbidity` | NTU |
| `conductivity` | µS/cm |
| `nitrate`, `ammonium` | mg/L |
| `latitude`, `longitude` | degrees |
| `hour` | 0–24, local time (14:30 is 14.5) |
| `day_of_year` | 1–366 |

`hour` and `day_of_year` aren't stored; derive them from `taken_at` the same way:

```python
from datetime import datetime

taken = [datetime.fromisoformat(value) for value in table["taken_at"]]
table["hour"] = [t.hour + t.minute / 60 for t in taken]
table["day_of_year"] = [t.timetuple().tm_yday for t in taken]
```

Missing values are NaN, which the trees handle; nothing needs scaling.
Leave out `latitude` and `longitude` unless every mission is on the same
pond, or the model learns places instead of water chemistry.

## Output

A two-class classifier (0 no bloom, 1 bloom): `predict_proba()`'s bloom
column × 100 is the risk. A regressor trained on a 0–1 risk also works. The
risk levels start at 26, 51, and 76, so check that the probabilities line up
with them (calibrate the model, or move `RISK_LEVELS`).

Confidence still comes from how many probes reported, and model-scored
readings have no drivers.

## On the Pi

Commit both files (or `scp` them), then install the model's library at
**the version you trained with**:

```text
python3 -m venv --system-site-packages ~/bloom-env
~/bloom-env/bin/pip install xgboost==<version> joblib pandas    # pip show xgboost
sudo ~/bloom-env/bin/python3 Server/app.py
```

Use `lightgbm` or `scikit-learn` instead as needed, and run the service with
the same Python (HARDWARE_SETUP.md, "Run at boot").

## Checking it

`curl http://10.0.0.1/model` shows whether the model is scoring and, if not,
why. The dashboard's footer says what is scoring.

- A model that can't load (bad `model.json`, missing library, wrong columns,
  answers outside 0–1) leaves the rules scoring.
- If it fails on one reading, the rules score that reading; `scored_by` records which.
- Only readings that arrive after loading use it; start a new mission so
  one map doesn't mix both.
- Only load model files you trust: loading one runs code from it.
