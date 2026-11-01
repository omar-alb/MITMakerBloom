# Bloom Detection

## NOTES FOR MIT, PLEASE READ:
This is my use of AI, as I wrote about in the MIT Maker form, I used Claude Code to:
 - write unit tests
 - refactor code when necessary
 - add explanatory comments
 - write 'readme' files
 - identify possible bugs.
 - write temporary placeholder values

Otherwise, the structure, design, and code is written by me.
This repo is a temporary copy of Bloom's current repo (as I do not want to release everything publicly yet).
As of now, I'm still playing around with Bloom's structure. The most recent change was completely getting rid of the drone and imagery, as it drove costs up higher than the value it gave. As such, the next steps are to begin training the machine learning model (I'm planning to begin with gradient-boosted trees, currently has a placeholder), and to fine-tune specific calibrations, which I believe will take multiple deployments to learn about.
Thank you, I hope you like my project!
##

A field system for finding and mapping possible harmful algal blooms.

A small boat carries six water-quality probes and a GPS. Every 10 seconds it
sends a reading over Wi-Fi to a Raspberry Pi, which scores the bloom risk,
stores it, and serves a live map and dashboard to any phone or laptop on its
network.

```text
  Boat (ESP32)                      Server (Raspberry Pi)                 Dashboard (browser)
  probes + GPS   -- POST /readings -->  convert, score, store   <-- GET /dashboard --  map, risk, alerts
  Boat/                             Server/                              Server/web/
```

## What is where

```text
Boat/                 MicroPython for the ESP32
  main.py             the loop: read, save to the outbox, send
  config.py           boat settings
  sensors.py          the probes (two ADS1115 boards)
  gps.py              the GPS
  outbox.py           readings waiting on flash until the server has them
  simulation.py       fake probes and GPS for bench demos
  tests/

Server/               the Raspberry Pi server
  app.py              start here
  routes.py           every URL
  processing.py       a new reading: checked, converted, scored
  analysis.py         alerts, map cells, hotspots, the headline
  database.py         bloomdata.db (training data) and missions.db (everything else)
  config.py           server and dashboard settings
  model/              the trained model goes here (see model/README.md)
  web/                the dashboard
  tests/
  data/               the databases, created on first run
```

## Run it

**On the Raspberry Pi** (creates the `Bloom` Wi-Fi network):

```text
sudo apt install network-manager python3-flask
sudo python3 Server/app.py
```

Join `Bloom` and open `http://10.0.0.1`.

**On any computer:**

```text
python3 -m pip install -r Server/requirements.txt
python3 Server/app.py --dev                        # http://localhost:5000
```

**On the boat:** copy the six `.py` files in `Boat/` to the ESP32 and power it
up. For a demo with no hardware, set `SIMULATE_HARDWARE = True` and point
`SERVER_URL` at your computer.

Wiring, flashing, calibration, and running at boot are in
[HARDWARE_SETUP.md](HARDWARE_SETUP.md).

## The dashboard

- **Map:** readings coloured by risk, 5 m risk cells, the track, hotspots, and
  numbered revisit targets, over a metre grid. Each layer can be switched off.
- **Bloom risk:** the mission's headline risk (0–100) and its spread across levels.
- **Boat:** online or offline, GPS, probes, latest reading.
- **Alerts:** bloom and sensor alerts, acknowledged with one tap.
- **Readings:** a searchable, sortable table (cards on a phone) and a CSV export.
- **Replay** steps through the mission, showing the map and panels as they were at each reading.
- **Missions** split the record into deployments; past ones open read-only.

It works on phones, follows light or dark mode, and needs no internet.

**Cells vs. hotspots:** cells are a fixed 5 m grid over all the water crossed,
showing the whole pond. Hotspots are clusters of High and Severe readings,
ranked by how worth revisiting they are.

## Settings

Every tunable number is in `Boat/config.py` or `Server/config.py`. Sizes are
set for small ponds: GPS is good to about 2–5 m, so nothing is finer than 5 m.

## How the risk is worked out

Each reading gets one risk from 0 to 100 when it arrives. Cells, the headline,
and alerts are all built from those stored risks.

| Risk | Level | |
|---|---|---|
| 0–25 | Low | routine monitoring |
| 26–50 | Moderate | monitor soon |
| 51–75 | High | alert, can form a hotspot |
| 76–100 | Severe | alert: sample here immediately |

Until a model is trained, built-in rules score each probe by how far it sits
into a risky range and take a weighted average. A missing probe is left out
rather than counted as clean water. The thresholds and calibration in
`Server/config.py` are placeholders until checked against field data.

The real model will be a **gradient-boosted tree classifier** (XGBoost,
LightGBM, or scikit-learn) trained on `Server/data/bloomdata.db`, which holds
each reading's time, position, and probe values in their units. Drop it in
`Server/model/` with a `model.json` and restart; see
[Server/model/README.md](Server/model/README.md).

## Tests

These run on a computer in a few seconds, with no hardware or network:

```text
cd Boat   && python3 -m unittest discover -s tests
cd Server && python3 -m unittest discover -s tests
cd Server && node --test
```

## More

- [HARDWARE_SETUP.md](HARDWARE_SETUP.md): wiring, flashing, calibrating, deploying.
- [CODEBASE_SUMMARY.md](CODEBASE_SUMMARY.md): how the code works, the API, the database.
- [Server/model/README.md](Server/model/README.md): training and plugging in the model.
- [project-structure.html](project-structure.html): an interactive map of every file.
