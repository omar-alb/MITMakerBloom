# Bloom Detection

A field system for finding and mapping possible harmful algal blooms.

A small boat carries water-quality probes and a GPS. Every 10 seconds it
measures the water and sends the reading over Wi-Fi to a Raspberry Pi, which
scores the bloom risk, stores everything, and serves a live map and dashboard
to any phone or laptop on its Wi-Fi network.

```text
  Boat (ESP32)                      Server (Raspberry Pi)                 Dashboard (browser)
  probes + GPS   -- POST /readings -->  convert, score, store   <-- GET /dashboard --  map, risk, alerts
  Boat/                             Server/                              Server/web/
```

## What is where

```text
Boat/                 the boat's MicroPython program; copy these files to the ESP32
  main.py             the loop: take a reading, keep it in the outbox, send it
  config.py           every boat setting: Wi-Fi, server address, timing, wiring, bench demo
  sensors.py          the probes (ADS1115 analog boards), in millivolts
  gps.py              the GPS position
  outbox.py           readings waiting on flash until the server has them
  simulation.py       fake probes and GPS, for bench demos
  tests/              the boat's tests (run on a computer, not the board)

Server/               the Raspberry Pi server
  app.py              start here: runs the server (and, on the Pi, its Wi-Fi network)
  routes.py           every URL the boat and the dashboard use, and what each answers
  processing.py       a reading arrives: checked, converted to units, scored
  analysis.py         what the readings add up to: alerts, map cells, hotspots, the headline
  database.py         SQLite: bloomdata.db (the readings: training data) and missions.db (the rest)
  config.py           every server and dashboard setting: sensors, calibration, risk, sizes, map, Wi-Fi
  model/              the trained gradient-boosted tree model goes here (see model/README.md)
  web/                the dashboard: index.html, dashboard.js, style.css
  tests/              the server's and the dashboard's tests
  data/               bloomdata.db and missions.db, created when the server first runs
```

## Run it

**On the Raspberry Pi** (creates the `Bloom` Wi-Fi network, then serves the dashboard):

```text
sudo apt install network-manager python3-flask
sudo python3 Server/app.py
```

Join the `Bloom` network and open `http://10.0.0.1`.

**On any computer** (no Wi-Fi changes; good for development):

```text
python3 -m pip install -r Server/requirements.txt
python3 Server/app.py --dev                        # then open http://localhost:5000
```

**On the boat:** set the GPS pins in `Boat/config.py`, copy the six `.py` files
in `Boat/` to the ESP32, and power it up — `main.py` runs at boot. To see the
whole system work without any hardware, set `SIMULATE_HARDWARE = True` and
point `SERVER_URL` at your computer.

Wiring, flashing, calibration, and running the Pi at boot are in
[HARDWARE_SETUP.md](HARDWARE_SETUP.md).

## The dashboard

- **Map:** every reading coloured by its risk, 5 m risk cells, the boat's
  track, hotspot areas, and numbered revisit targets, over a metre grid (5 m
  apart right in close, coarser as you zoom out) and a scale bar. Each layer
  can be switched off.
- **Bloom risk:** the mission's headline risk (0–100) and how its cells split
  across the levels.
- **Boat:** online or offline, GPS, probes, and the latest reading.
- **Alerts:** bloom and sensor alerts, each acknowledged with one tap, shown a
  few to a page (`ALERT_PAGE_SIZE`).
- **Readings:** a searchable table (cards on a phone) whose columns sort on click, with a
  third click clearing the sort, and a CSV of the whole mission.
- **Replay** steps back through the mission, and the map's cells, hotspots,
  and revisit targets, the bloom risk panel, and the alerts all show the
  mission as it stood at each reading. **Missions** split the record into
  deployments: the picker shows the running mission's name, and past ones
  open read-only.

It works on a phone, follows the system's light or dark mode, and needs no
internet at all: there is no street map underneath, so nothing is downloaded,
and the mission is drawn over its grid with a scale bar.

**Risk cells and hotspots** answer two different questions. The cells are a
fixed grid of 5 m squares covering every bit of water the boat has crossed,
each coloured by the readings inside it: *what does the whole pond look
like?* Hotspots are
only the High and Severe readings, gathered into circles wherever they
actually bunch up (not cut along grid lines), and ranked by how worth going
back to they are, the first five numbered on the map: *where should we sample
next?*

## Settings

Every number the system works with is a setting, with a comment saying what
it does, in one of two files:

- `Boat/config.py`: the boat (Wi-Fi, server address, timing, outbox, wiring,
  the ADC's range, GPS pins, the bench demo). Copied to the board with the rest.
- `Server/config.py`: the server and the dashboard (sensors, calibration,
  risk levels, sizes on the water, map zoom, timings, the Wi-Fi network). The
  dashboard gets its share from the server as `/config.js`.

Sizes are set for small ponds: 5 m risk cells, and hotspots 5 to 20 m in
radius. A consumer GPS is good to about 2 to 5 m, so cells smaller than 5 m
would add scatter rather than detail.

## How the risk is worked out

Each reading gets **one** bloom risk from 0 to 100, once, when it arrives.
Every number on the dashboard is built from those stored risks — a map cell is
the roll-up of the readings inside it, the headline is the roll-up of the
cells, and an alert quotes the reading that raised it — so no two parts of
the dashboard can disagree.

| Risk | Level | |
|---|---|---|
| 0–25 | Low | routine monitoring |
| 26–50 | Moderate | monitor soon |
| 51–75 | High | alert, can form a hotspot |
| 76–100 | Severe | alert: sample here immediately |

The built-in rules score each probe by how far its value sits into a risky
range (warm water, high pH, high turbidity, high conductivity, high nitrate or
ammonium) and take a weighted average. A probe that did not report is left
out rather than counted as clean water, and a risk built on too little
evidence is scaled down, so one odd probe cannot raise an alert by itself.
Every threshold and weight is one line in `Server/config.py`, and they are
provisional until calibrated against field data.

**The rules are a stand-in for the real model: a gradient-boosted tree
classifier** (XGBoost, LightGBM, or scikit-learn), to be trained on the
readings from past missions. Those are kept on their own for exactly that, in
`Server/data/bloomdata.db`: just the id, mission, time, position, and each
probe's value already converted to its unit (°C, pH, NTU, µS/cm, mg/L).
Everything else, from risks to alerts and the raw millivolts, is in
`missions.db`. Once the model is trained, put it in `Server/model/` (saved
with joblib) with a short `model.json` saying which inputs it takes, and
restart. Everything — markers, cells, alerts, the headline — follows it. See
[Server/model/README.md](Server/model/README.md).

All of this happens in two files: `Server/processing.py` scores each reading
once, as it arrives, and `Server/analysis.py` builds the alerts, cells,
hotspots, and headline from the stored risks.

## Tests

Automatic checks that run the real code against fake hardware, fake data, and
temporary databases, in a few seconds. None of them need hardware, a network,
or a running server. Run them after changing anything, and before copying
code to the Pi or flashing the boat. What they cannot check is the physical
side (real probes, real GPS, the Pi's Wi-Fi): that needs a bench or field test.

```text
cd Boat   && python3 -m unittest discover -s tests     # the boat
cd Server && python3 -m unittest discover -s tests     # the server
cd Server && node --test                               # the dashboard
```

## More

- [HARDWARE_SETUP.md](HARDWARE_SETUP.md): building, wiring, flashing, calibrating, and deploying.
- [CODEBASE_SUMMARY.md](CODEBASE_SUMMARY.md): how the code works, the API, and the database.
- [Server/model/README.md](Server/model/README.md): training, saving, and plugging in the gradient-boosted tree model.
- [project-structure.html](project-structure.html): an interactive map of every file (open it in a browser).
