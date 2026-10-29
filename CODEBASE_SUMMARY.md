# How the code works

A tour of the code: how a reading travels through the system, what each file
does, the API, the database, and how the bloom risk is worked out. For setting
up the hardware see [HARDWARE_SETUP.md](HARDWARE_SETUP.md); for running it, the
[README](README.md).

## The rules the code follows

- **One name per thing.** A reading is a *reading* everywhere: on the boat, in
  the API, in the database, and on the dashboard. Each sensor has one key
  (`ph`, `temperature`, ...) used from the boat's payload to the CSV header.
- **One definition per thing.** Each sensor — label, unit, decimals, how it
  counts towards the risk, and its alert threshold — is one line of
  `SENSORS` in `Server/config.py`. The risk levels are `RISK_LEVELS` in the
  same file. The dashboard receives both from the server instead of keeping
  its own copies.
- **Scored once.** A reading's risk is worked out when it arrives and stored.
  Everything else (cells, hotspots, alerts, the headline) is built from stored
  risks and never scores again, so no two views can disagree.
- **The boat measures; the server interprets.** The boat sends raw millivolts.
  Units, calibration, and risk all live on the server.
- **The server sends values; the browser formats them.** Times, units, and
  labels are formatted in `dashboard.js`, in the viewer's own time zone.
- **Every setting in a config file.** Each number the system works with is a
  commented setting in `Boat/config.py` (the boat) or `Server/config.py` (the
  server and the dashboard, which gets its share as `/config.js`). What stays
  in the code are facts, not choices, as named constants: the size of the
  Earth, the pH scale, the sensors' datasheets, the ADS1115's registers, NMEA's
  layout, HTTP status codes. The dashboard's drawing details (line widths,
  marker sizes, opacities) are one `STYLE` block at the top of `dashboard.js`,
  beside the colours in `style.css`.

## The life of a reading

```text
Boat/sensors.py   reads each wired probe in millivolts          {"ph": 1450.0, ...}
Boat/gps.py       reads the position                            {"status": "fix", "latitude": ..., ...}
Boat/main.py      stamps it with a sample_id, saves it to the outbox (Boat/outbox.py),
                  and POSTs the outbox oldest-first to /readings, adding age_seconds
        |
        v   Wi-Fi
Server/routes.py      POST /readings
Server/processing.py  checks the payload, converts millivolts to units, works out when it
                      was taken (now - age_seconds), and scores it, once: with the trained
                      model in Server/model/ if there is one, else with the built-in rules
Server/analysis.py    the alerts it raises
Server/database.py    stores the reading in bloomdata.db, and its details and alerts in
                      missions.db, in the active mission
        |             (a sample_id already stored is ignored, so a resend is harmless)
        v
Server/web/dashboard.js   polls GET /dashboard every 9 s
Server/routes.py          builds the document from the stored readings, with the cells,
                          hotspots, headline risk, and boat status from analysis.py
Server/web/dashboard.js   draws the map, the panels, and the table
```

`processing.py` is the only place a reading is scored, and `analysis.py` the
only place stored risks are turned into anything else.

## The boat (`Boat/`, MicroPython on an ESP32)

| File | What it does |
|---|---|
| `main.py` | The loop, run at boot. Every `READING_INTERVAL_SECONDS`: take a reading, give it a unique `sample_id` (`<device>-<boot id>-<count>`), save it to the outbox, and send what Wi-Fi allows. Wi-Fi reconnects in the background, so the pace holds while offline. `send()` turns the HTTP answer into SENT (2xx), REJECTED (4xx except 408/429: dropped, or it would block the outbox forever) or FAILED (anything else: kept for later). |
| `config.py` | Every boat setting: name, network, timing, outbox size, which probe is on which ADC input, the ADC's range and speed, GPS pins, and the bench demo (its switch and its pond-sized simulated track). |
| `sensors.py` | An ADS1115 driver and `read_probes()`: one millivolt value per wired probe. Each probe is read on its own, so one that does not answer is left out rather than losing the whole reading; status is `ok` or `partial`. |
| `gps.py` | `read_position()`: throws away stale UART data, reassembles lines split across reads, checks each NMEA checksum, and accepts only GGA/RMC sentences that report a satellite lock and in-range coordinates. Reuses the last fix as `stale` for up to `GPS_STALE_LIMIT_SECONDS`. |
| `outbox.py` | The on-flash queue (`outbox.jsonl`, one reading per line). `send_all()` sends oldest-first, a batch of 25 at a time, within a time budget; rewrites the file only after progress; caps it at `OUTBOX_MAX_BYTES`; `recover()` repairs a rewrite cut short by a power cut. |
| `simulation.py` | Simulated millivolts and a circling track, used only with `SIMULATE_HARDWARE = True`. |

The ESP32 has no clock, so a reading carries `_taken_ms` (its tick count) in
the outbox and is sent with `age_seconds`; the server subtracts that from its
own clock. Fields starting with `_` never leave the boat. After a reboot the
tick counter restarts, so leftover readings are sent without an age and are
stamped with their arrival time.

## The server (`Server/`, Python and Flask on a Raspberry Pi)

| File | What it does |
|---|---|
| `app.py` | Start here. `sudo python3 app.py` on the Pi sets the hostname, creates the `Bloom` access point with one `nmcli` command, opens the database, loads the model, opens this run's mission, and serves; it takes the access point down when it stops (SIGTERM becomes a clean exit). `--dev` leaves the network alone and serves on port 5000, for any computer; `--port` picks another port. |
| `routes.py` | Every URL (below) and what it answers: JSON errors, the dashboard document (`dashboard_document`), and the CSV export (`csv_lines`, streamed). |
| `processing.py` | Everything that happens to a new reading. `process(payload)` validates it (400 with a reason if it cannot be used), converts millivolts to units (`to_units`), sets `taken_at`, and scores it (`score`): with the trained gradient-boosted tree model when one is loaded (`load_model`), else with the built-in rules (`score_with_rules`). |
| `analysis.py` | Everything built from stored risks: `level()`, `is_alert()`, `rollup()` (mean blended with peak), `alerts_for(reading)`, `build_cells()` (5 m squares), `build_hotspots()` (alert-level clusters in revisit order), and `overview()` (cells, hotspots, headline, and boat status for the dashboard). |
| `database.py` | SQLite in two files: `bloomdata.db` (the readings alone: the training data) and `missions.db` (missions, each reading's details and risk, alerts). Each call opens a short connection to both that commits or rolls back both. |
| `config.py` | Every server and dashboard setting: the two database files, web server, Wi-Fi, input limits, `SENSORS`, calibration, `RISK_LEVELS` and the risk rules, the model's trial reading, sizes on the water (cells, grid, hotspots, shading), and the dashboard's zoom levels, timings, and page sizes. |
| `model/` | `README.md`: training, saving, and plugging in the gradient-boosted tree model. The model file (`bloom.joblib`) and its `model.json` go here. |
| `web/` | `index.html`, `dashboard.js`, `style.css`, and Leaflet 1.9.4 in `vendor/leaflet/`; the page also loads `/config.js`, its settings from `config.py`. No build step, and nothing loaded from the internet: the map has no street-map tiles, just the mission's layers over a metre grid with a scale bar. |

## Reading payload (`POST /readings`)

```json
{
  "device": "boat",
  "sample_id": "boat-3f9a1c0d7b2e-17",
  "age_seconds": 12.5,
  "hardware": "ok",
  "millivolts": {"ph": 1450.0, "temperature": 720.0, "turbidity": 2600.0, "conductivity": 2100.0},
  "gps": {"status": "fix", "latitude": 42.28084, "longitude": -83.74304}
}
```

- `device` is required. `sample_id` is optional; a repeat is answered
  `{"status": "duplicate"}` with a 200 so the boat stops resending it.
- `millivolts` must hold at least one sensor key from `SENSORS`. Unknown keys,
  non-numbers, NaN, and infinity are skipped.
- `gps.status` is one of `fix`, `stale`, `simulated` (these need a valid
  latitude and longitude) or `no_fix`, `unavailable` (no position is stored).
- `age_seconds` from 0 to 7 days moves `taken_at` back; anything else is ignored.
- `hardware` is `ok`, `partial`, or `simulated`.

## HTTP API

| Request | |
|---|---|
| `GET /config.js` | the dashboard's settings from `config.py` (zoom levels, timings, page sizes, ...), loaded by the page before `dashboard.js` |
| `POST /readings` | the boat sends one reading → `{"status": "saved" or "duplicate", "id": ...}` |
| `GET /dashboard` | everything the dashboard shows, for the active mission |
| `GET /missions/<id>/dashboard` | the same for any mission (an ended one includes acknowledged alerts) |
| `GET /missions` | every mission, newest first |
| `POST /missions` | start a new mission, ending the active one; body `{"name": ...}` optional (409 if taken) |
| `POST /missions/end` | end the active mission (404 if none) |
| `GET /missions/<id>/readings.csv` | every reading of a mission, with its cell and raw millivolts |
| `GET /missions/<id>/replay/<reading>` | the cells, hotspots, headline, and alerts as they stood when that reading arrived (alerts acknowledged later count as open); replay asks for it at each step |
| `POST /alerts/<id>/acknowledge` | acknowledge one alert (404 if unknown) |
| `POST /alerts/acknowledge` | acknowledge every open alert of the active mission |
| `GET /model` | which model is scoring and why: `{model, scoredBy, message}`, plus `features` and `failures` while the trained model is scoring |
| `POST /model/reload` | load the model in `Server/model/` again, without a restart |
| `POST /shutdown` | stop the server (and the Wi-Fi network with it) |

Every error is JSON: `{"error": "what went wrong"}`. Nothing is authenticated;
see the note on Wi-Fi passwords in HARDWARE_SETUP.md.

## The dashboard document (`GET /dashboard`)

```text
mission      {id, name, active, startedAt, endedAt, readingCount}, or null
mapCenter    {lat, lng}: where the map opens before the first fix
grid         {steps, cellSize, latitude}: the background grid's spacings (MAP_GRID_METERS),
             lined up with the cells
riskLevels   [{label, min, color}]: the ladder, from config
sensors      [{key, label, unit, digits}]: from config
readings     the newest 500, oldest first: {id, takenAt, lat, lng, gps, <sensor values>,
             risk, level, confidence, drivers, alert, cell, scoredBy}
cells        {id, bounds, risk, peak, level, confidence, readings, averages}
hotspots     most worth revisiting first: {id, lat, lng, radius, risk, level, confidence,
             readings, lastTakenAt, distance, priority, reason, revisit}
summary      {risk, level, basis, counted, peak, distribution, alertCells, hotspots}
boat         {online, lastHeard, gps, hardware}
alerts       {id, kind, level, title, message, raisedAt, acknowledged}
model        {model, scoredBy}
```

Cells, hotspots, and the summary always cover the whole mission (up to
`MISSION_READING_LIMIT`), even though only the newest 500 readings are sent.

## The databases (`Server/data/`)

Two SQLite files. **`bloomdata.db` is the training data**: the readings and
nothing else, so it can be copied off and loaded as it is.

```text
bloomdata.db
  readings          id, mission_id, taken_at (when the boat took it),
                    latitude, longitude (NULL without a fix),
                    one column per sensor, in its unit (NULL if that probe did not report)

missions.db: everything around the readings, pointing at them by reading id
  missions          id, name (unique, ignoring case), started_at, ended_at (NULL while
                    active), grid_latitude (the first GPS fix; the cells are sized for it)
  reading_details   reading_id, sample_id (unique), device, received_at, gps (status),
                    hardware (ok, partial, simulated), millivolts (JSON, as sent),
                    risk, confidence, drivers (JSON list), scored_by
  alerts            id, mission_id, reading_id, kind ("bloom" or a sensor key), level,
                    title, message, raised_at, acknowledged_at; one per (reading_id, kind)
```

Every connection opens `missions.db` with `bloomdata.db` attached, so a query
can join them and one transaction writes both: a reading lands in both files
or in neither. That is why both use SQLite's rollback journal rather than WAL,
whose commits are only atomic one file at a time. The rest of the server
never sees the split: `database.mission_readings()` returns each reading with
its details joined.

Only the risk is stored; its level, colour, and cell are worked out from it
when needed, so they cannot fall out of step. Exactly one mission is active at
a time, and a reading that arrives when none is active starts one. Times are
ISO 8601 in the Pi's local time zone, with the offset. A sensor added to
`SENSORS` gets its column when the server next starts.

## The bloom risk in detail

**The rules** (`processing.score_with_rules`). Each sensor with a value scores 0
at or below its `low` threshold, 100 at or above its `high`, and in
proportion between. The risk is the weighted average of those, using each
sensor's `weight`. Two safeguards:

- A sensor with no value is left out and the others are re-weighted, so the
  unwired nutrient probes do not count as clean water.
- If the sensors that reported carry less than `FULL_EVIDENCE_SHARE` (half)
  of the total weight, the average is scaled down in proportion. One elevated
  probe on its own therefore cannot reach an alert level. The four probes
  wired today carry 0.70, so they are never scaled down.

**Drivers** are the sensors whose part reached `DRIVER_THRESHOLD` (60),
strongest first; they appear in popups, alert messages, and the CSV.

**Confidence** (30–90) grows with the share of the scoring weight that
reported: `30 + 60 × share`. It stops short of 100 because even a full set of
probes gives an estimate. A cell's confidence is the average of its readings'.

**Levels** (`analysis.level`) come from `RISK_LEVELS`. The risk is rounded before it is placed on
the ladder, exactly as it is shown. Readings at `ALERT_LEVELS` (High, Severe)
raise a bloom alert and can form a hotspot.

**Roll-ups** blend the mean with the peak (`ROLLUP_PEAK_SHARE` = 0.45), so one
hot reading is neither averaged away nor allowed to speak for its whole area.
A cell rolls up its readings; the headline rolls up the cells (or, with no GPS
fix at all, the readings).

Every size below is set for small ponds, in `Server/config.py`.

**Cells** are 5 m squares (`CELL_SIZE_METERS`), sized for the latitude of the
mission's first fix. Each mission is scored on its own readings only;
nothing is compared with earlier missions yet.

**Hotspots** are circles around alert-level readings within 10 m
(`HOTSPOT_JOIN_METERS`) of a hotspot's centre. A circle covers its readings
plus 3 m (`HOTSPOT_MARGIN_METERS`), with a radius of at least 5 m and at most
20 m (`HOTSPOT_MIN_RADIUS_METERS`, `HOTSPOT_MAX_RADIUS_METERS`). Their revisit
priority (`HOTSPOT_PRIORITY_WEIGHTS`) weighs risk (0.52), uncertainty (0.23),
distance from the boat (0.14, in full from 25 m, `HOTSPOT_FAR_METERS`), and
time since sampled (0.11, in full from 15 min, `HOTSPOT_OLD_SECONDS`).

**Cells and hotspots** answer different questions. Cells are a fixed grid
over every bit of water crossed, coloured by the readings inside: the state
of the whole pond, comparable square by square across missions. Hotspots are
only the alert-level readings, gathered wherever they actually bunch up (not
cut along grid lines) and ranked for going back: where to sample next.

**The map** has no street map underneath (nothing is downloaded). A
background grid gives a sense of distance: the dashboard picks the finest of
`MAP_GRID_METERS` (5, 10, 25 m, ...) whose lines are still at least
`MAP_GRID_MIN_PIXELS` (20 px) apart, so it is 5 m right in close and coarser
as you zoom out. Its lines fall on the cells' edges, which are drawn
stronger, and the current spacing is shown above the scale bar. The map zooms
in as far as `MAP_MAX_ZOOM` (22, where a 5 m cell is about 180 px across);
"Fit to mission" stops at `MAP_FIT_ZOOM`. "Risk shading" draws a halo of
`SHADING_RADIUS_METERS` (3 m) around each reading.

**The trained model.** The project's model is a gradient-boosted tree
classifier (XGBoost, LightGBM, or scikit-learn), saved with joblib in
`Server/model/` beside a `model.json` that lists its inputs in training order
(any sensor, `latitude`, `longitude`, `hour`, `day_of_year`). Once loaded it
replaces the rules for new readings: each reading's inputs go to
`predict_proba()`, missing values as NaN (which the trees handle), and the
bloom probability × 100 is the risk. Confidence is worked out the same way,
and drivers are empty. If the model was trained on named columns, the names
must match `model.json`. A model that cannot load leaves the rules scoring,
and a reading it fails on is scored by the rules, with `scored_by` recording
which. See [Server/model/README.md](Server/model/README.md).

## Changing things

| To... | Edit |
|---|---|
| wire a new probe that already has a conversion (nitrate, ammonium) | uncomment it in `PROBES` in `Boat/config.py` and reflash |
| add a new kind of sensor | a `Sensor(...)` line in `SENSORS` and its conversion in `processing.to_units` (both on the server), its millivolt profile in `Boat/simulation.py`, and its input in `PROBES` on the boat |
| calibrate | the calibration section of `Server/config.py` |
| move the risk thresholds or weights | `SENSORS` in `Server/config.py` |
| move the level boundaries or colours | `RISK_LEVELS` in `Server/config.py` (the badge text colours are in `web/style.css`) |
| resize the risk cells | `CELL_SIZE_METERS` in `Server/config.py` (and keep it in `MAP_GRID_METERS`) |
| resize hotspots | `HOTSPOT_JOIN_METERS`, `HOTSPOT_MARGIN_METERS`, `HOTSPOT_MIN_RADIUS_METERS`, `HOTSPOT_MAX_RADIUS_METERS` in `Server/config.py` |
| change the map grid's spacings | `MAP_GRID_METERS` in `Server/config.py` |
| zoom levels, "Risk shading" size | `MAP_*_ZOOM` and `SHADING_RADIUS_METERS` in `Server/config.py` |
| how often the dashboard refreshes, alert and table page sizes, replay speed | the Dashboard section of `Server/config.py` |
| line widths, marker sizes, opacities on the map | the `STYLE` block at the top of `web/dashboard.js` |
| the ADC's input range or speed | `ADC_FULL_SCALE_VOLTS`, `ADC_SAMPLES_PER_SECOND` in `Boat/config.py` |
| the bench demo's simulated pond | `SIMULATED_TRACK_CENTER`, `SIMULATED_TRACK_RADIUS_METERS` in `Boat/config.py` |
| score with the trained model | `bloom.joblib` and `model.json` in `Server/model/` |

`Server/tests/test_boat_contract.py` checks that every probe the boat sends is
a sensor the server knows, and runs the real boat code to make sure the server
accepts what it sends.

## Tests

```text
cd Boat   && python3 -m unittest discover -s tests     # 48 tests
cd Server && python3 -m unittest discover -s tests     # 122 tests
cd Server && node --test                               # 28 tests
```

Some server tests train small real models and are skipped when their library
is not installed (XGBoost, LightGBM, scikit-learn).

| File | Covers |
|---|---|
| `Boat/tests/test_gps.py` | NMEA parsing, checksums, locks, split lines, stale fixes, missing GPS |
| `Boat/tests/test_outbox.py` | order, outages, refused readings, corrupt lines, time budget, size cap, power-cut recovery |
| `Boat/tests/test_boat.py` | config, probes failing one at a time, the ADS1115 timing, request bodies, send outcomes, the loop |
| `Server/tests/test_processing.py` | every conversion and validation rule, the rules, and the model plug-in: `model.json` mistakes, column order, missing values, failures, and real XGBoost, LightGBM, and scikit-learn gradient-boosted tree models |
| `Server/tests/test_analysis.py` | levels, roll-ups, alerts, cells, hotspots, the headline, boat status |
| `Server/tests/test_routes.py` | the HTTP API end to end, the dashboard document, the CSV, that every view reads the one stored risk, that `/config.js` sends every setting `dashboard.js` uses, and that the dashboard never reaches the internet |
| `Server/tests/test_database.py` | missions, resends, alerts, the grid latitude, new sensor columns, the two files (bloomdata.db holds only measurements, in their units; a reading lands in both or neither) |
| `Server/tests/test_app.py` | starting up: `--dev`, the root check, the Wi-Fi network up and down |
| `Server/tests/test_boat_contract.py` | the boat's probes and payload against the server |
| `Server/tests/dashboard.test.js` | `dashboard.js` in a fake page with a fake Leaflet: the map layers, the grid, the table, alerts, missions, and what happens without settings |
