# How the code works

How a reading travels through the system, what each file does, the API, the
database, and how the risk is worked out. Hardware is in
[HARDWARE_SETUP.md](HARDWARE_SETUP.md); running it is in the [README](README.md).

## Conventions

- **One name and one definition per thing.** Each sensor is one line of
  `SENSORS` in `Server/config.py` (key, label, unit, weight, thresholds), and
  its key is used everywhere from the boat's payload to the CSV header.
- **Scored once.** A reading's risk is worked out on arrival and stored;
  cells, hotspots, alerts, and the headline are built from stored risks.
- **The boat measures, the server interprets.** The boat sends raw millivolts.
- **Settings live in config files:** `Boat/config.py` and `Server/config.py`
  (the dashboard gets its share as `/config.js`). Map drawing details are the
  `STYLE` block in `dashboard.js`.

## The life of a reading

```text
Boat/sensors.py       millivolts from each probe
Boat/gps.py           the position
Boat/main.py          adds a sample_id, saves to the outbox, POSTs it with age_seconds
        |  Wi-Fi
        v
Server/routes.py      POST /readings
Server/processing.py  validates, converts to units, sets taken_at, scores (model or rules)
Server/analysis.py    the alerts it raises
Server/database.py    stores it in the active mission (a repeated sample_id is ignored)
        |
        v
Server/web/dashboard.js   polls GET /dashboard; routes.py builds it with analysis.py
```

## The boat (`Boat/`, MicroPython on an ESP32)

| File | What it does |
|---|---|
| `main.py` | The loop, run at boot: read, save to the outbox, send what Wi-Fi allows. `send()` maps the HTTP answer to SENT (2xx), REJECTED (4xx except 408/429, dropped) or FAILED (kept). |
| `config.py` | Every boat setting. |
| `sensors.py` | ADS1115 driver and `read_probes()`. A probe that doesn't answer is left out (`partial`). |
| `gps.py` | `read_position()`: NMEA GGA/RMC with checksum and lock checks; reuses the last fix as `stale` for up to `GPS_STALE_LIMIT_SECONDS`. |
| `outbox.py` | The on-flash queue (`outbox.jsonl`). Sends oldest-first within a time budget, caps the size, recovers from power cuts. |
| `simulation.py` | Fake probes and track for `SIMULATE_HARDWARE = True`. |

The ESP32 has no clock, so readings are sent with `age_seconds` and the
server subtracts it. Readings left over from before a reboot have no age and
get their arrival time.

## The server (`Server/`, Python and Flask on a Raspberry Pi)

| File | What it does |
|---|---|
| `app.py` | Start here. On the Pi (`sudo`) it creates the `Bloom` access point, then opens the database, loads the model, opens the mission, and serves. `--dev` skips the network and serves on port 5000. |
| `routes.py` | Every URL, the dashboard document, and the streamed CSV. |
| `processing.py` | `process(payload)`: validation, `to_units`, and `score` (the model if loaded, else `score_with_rules`). |
| `analysis.py` | Levels, roll-ups, alerts, cells, hotspots, and the headline. |
| `database.py` | SQLite in two files (below). |
| `config.py` | Every server and dashboard setting. |
| `model/` | Where the trained model goes; see its README. |
| `web/` | `index.html`, `dashboard.js`, `style.css`, and Leaflet in `vendor/`. Nothing loads from the internet. |

## Reading payload (`POST /readings`)

```json
{
  "device": "boat",
  "sample_id": "boat-3f9a1c0d7b2e-17",
  "age_seconds": 12.5,
  "hardware": "ok",
  "millivolts": {"ph": 1450.0, "temperature": 720.0, "turbidity": 2600.0, "conductivity": 2100.0,
                 "nitrate": 1560.0, "ammonium": 1560.0},
  "gps": {"status": "fix", "latitude": 42.36003, "longitude": -71.09413}
}
```

- `device` is required; a repeated `sample_id` gets `{"status": "duplicate"}`.
- `millivolts` needs at least one known sensor; unknown keys and non-numbers are skipped.
- `gps.status`: `fix`, `stale`, `simulated` (with a position) or `no_fix`, `unavailable`.
- `hardware`: `ok`, `partial`, or `simulated`.

## HTTP API

| Request | |
|---|---|
| `GET /config.js` | the dashboard's settings |
| `POST /readings` | one reading → `{"status": "saved" or "duplicate", "id": ...}` |
| `GET /dashboard` | everything the dashboard shows, for the active mission |
| `GET /missions/<id>/dashboard` | the same for any mission |
| `GET /missions` | every mission, newest first |
| `POST /missions` | start a new mission, ending the active one; `{"name": ...}` optional (409 if taken) |
| `POST /missions/end` | end the active mission |
| `GET /missions/<id>/readings.csv` | every reading, with its cell and raw millivolts |
| `GET /missions/<id>/replay/<reading>` | cells, hotspots, headline, and alerts as they stood at that reading |
| `POST /alerts/<id>/acknowledge` | acknowledge one alert |
| `POST /alerts/acknowledge` | acknowledge every open alert of the active mission |
| `GET /model` | which model is scoring, and why |
| `POST /model/reload` | reload `Server/model/` |
| `POST /shutdown` | stop the server and the Wi-Fi network |

Errors are JSON: `{"error": "..."}`. Nothing is authenticated.

## The dashboard document (`GET /dashboard`)

```text
mission      {id, name, active, startedAt, endedAt, readingCount}, or null
mapCenter    {lat, lng}
grid         {steps, cellSize, latitude}
riskLevels   [{label, min, color}]
sensors      [{key, label, unit, digits}]
readings     newest 500: {id, takenAt, lat, lng, gps, <sensors>, risk, level, confidence,
             drivers, alert, cell, scoredBy}
cells        {id, bounds, risk, peak, level, confidence, readings, averages}
hotspots     {id, lat, lng, radius, risk, level, confidence, readings, lastTakenAt,
             distance, priority, reason, revisit}
summary      {risk, level, basis, counted, peak, distribution, alertCells, hotspots}
boat         {online, lastHeard, gps, hardware}
alerts       {id, kind, level, title, message, raisedAt, acknowledged}
model        {model, scoredBy}
```

Cells, hotspots, and the summary cover the whole mission, not just the 500 readings sent.

## The databases (`Server/data/`)

```text
bloomdata.db (the training data)
  readings          id, mission_id, taken_at, latitude, longitude, one column per sensor (in units)

missions.db
  missions          id, name, started_at, ended_at (NULL while active), grid_latitude
  reading_details   reading_id, sample_id, device, received_at, gps, hardware,
                    millivolts (JSON), risk, confidence, drivers (JSON), scored_by
  alerts            id, mission_id, reading_id, kind, level, title, message,
                    raised_at, acknowledged_at
```

`bloomdata.db` is attached to every `missions.db` connection, so a reading is
written to both or neither (which is why they use a rollback journal, not
WAL). One mission is active at a time; a reading with none active starts one.
A sensor added to `SENSORS` gets its column at the next start.

## The bloom risk

**Rules** (`score_with_rules`): each sensor ramps from 0 at `low` to 100 at
`high`, and the risk is their weighted average. Missing sensors are left out.
If the reporting sensors carry less than `FULL_EVIDENCE_SHARE` (0.5) of the
weight, the result is scaled down, so one probe alone can't raise an alert.
The four core probes alone carry 0.70.

**Drivers** are sensors whose part reached `DRIVER_THRESHOLD` (60).
**Confidence** (30–90) is the share of weight that reported. **Levels** come
from `RISK_LEVELS`; High and Severe raise alerts and can form hotspots.
**Roll-ups** (cells, headline) pull the mean 45% toward the peak.

**Cells** are 5 m squares, sized for the mission's first fix.
**Hotspots** gather alert-level readings within 10 m of a centre (radius 5–20 m)
and are ranked for revisiting by risk (0.5), uncertainty (0.25), distance
(0.15) and age (0.1). Cells show the whole pond; hotspots show where to
sample next.

**The map** has no street map. Its grid uses the finest `MAP_GRID_METERS`
spacing at least 20 px apart.

**The trained model** replaces the rules for new readings: `predict_proba()`
× 100 is the risk, missing inputs are NaN, confidence is unchanged, and drivers
are empty. If it fails to load, or on one reading, the rules score instead and
`scored_by` records which. See [Server/model/README.md](Server/model/README.md).

## Changing things

| To... | Edit |
|---|---|
| take a probe off or put it back | `PROBES` in `Boat/config.py`, then reflash |
| add a new kind of sensor | `SENSORS` and `processing.to_units` on the server, `PROBES` and `simulation.py` on the boat |
| calibrate | the calibration section of `Server/config.py` |
| risk thresholds, weights, levels, colours | `SENSORS` and `RISK_LEVELS` in `Server/config.py` |
| cell, hotspot, and grid sizes | the "Sizes on the water" section of `Server/config.py` |
| zoom, refresh, page sizes, replay speed | the Dashboard section of `Server/config.py` |
| line widths and marker sizes | `STYLE` in `web/dashboard.js` |
| score with the trained model | `bloom.joblib` and `model.json` in `Server/model/` |

## Tests

```text
cd Boat   && python3 -m unittest discover -s tests
cd Server && python3 -m unittest discover -s tests
cd Server && node --test
```

Model tests are skipped when XGBoost, LightGBM, or scikit-learn isn't installed.
`test_boat_contract.py` runs the real boat code against the server.
