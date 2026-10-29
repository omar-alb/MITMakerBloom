# Hardware setup

Building, wiring, flashing, calibrating, and deploying the two devices:

1. **The boat:** a 3D-printed twin-hull boat carrying an ESP32, two ADS1115
   analog-to-digital boards, a GPS module, and water-quality probes.
2. **The server:** a Raspberry Pi 4 that runs its own Wi-Fi network called
   `Bloom`, receives the boat's readings, and serves the dashboard.

```text
Boat (ESP32)  -- Wi-Fi "Bloom" -->  Raspberry Pi 4 at 10.0.0.1  <-- Wi-Fi "Bloom" --  phone or laptop
```

The boat only measures. It sends raw probe voltages and its GPS position; the
Pi converts them into units, scores the bloom risk, and stores everything.
That is why calibrating a probe never means reflashing the boat.

---

## The boat

Keep the electronics, battery, and exposed connections dry, and route the
probes so they reach the water without fouling the hulls or propulsion.

### Wiring

The probes connect to two ADS1115 boards on the ESP32's I2C bus (SDA on
GPIO 21, SCL on GPIO 22). Tie the second board's ADDR pin so it answers at
`0x4A`; the first stays at `0x48`.

| Probe | Board | Input |
|---|---|---|
| pH electrode | 0x48 | A0
| Temperature (TMP36, or LM35) | 0x48 | A1
| Turbidity | 0x48 | A2
| Conductivity | 0x48 | A3
| Nitrate ion-selective electrode | 0x4A | A0
| Ammonium ion-selective electrode | 0x4A | A1

`PROBES` in `Boat/config.py` says which probe is on which input. **List only
probes that are actually connected:** an unconnected input floats, and the
server would turn that noise into a real-looking reading and raise alerts on
it. Nitrate and ammonium are commented out until the `0x4A` board is wired;
uncomment each one as it goes in. The dashboard adds their columns by itself
once readings arrive.

### GPS

The GPS module talks NMEA over a UART. **Set `GPS_RX_PIN` and `GPS_TX_PIN` in
`Boat/config.py`:** `GPS_RX_PIN` is the ESP32 pin the GPS module's TX wire is
connected to, and `GPS_TX_PIN` can be any free pin (nothing is sent to the
GPS). Avoid GPIO 6–11, and 16–17 on WROVER modules. They ship unset on
purpose: the UART's default pins, GPIO 9 and 10, belong to the flash chip on
most ESP32 modules. Until they are set, readings arrive without a position
and the dashboard shows "No GPS".

What the dashboard shows for GPS:

| GPS | Meaning | On the map? |
|---|---|---|
| Fix | a satellite lock just now | yes |
| Last known position | lock lost under a minute ago; the last fix is reused | yes |
| No fix | the GPS works but has no lock | no |
| No GPS | pins not set, or the UART failed | no |

A reading without a position still counts: its water chemistry is in the
table, the alerts, and the CSV; it just has no place on the map.

### Flashing

1. Install a current MicroPython release on the ESP32, erasing the flash first
   as [micropython.org](https://micropython.org/download/ESP32_GENERIC/)
   describes (the outbox needs a clean filesystem).
2. Copy the six program files. With the board on `COM3` (on Linux, something
   like `/dev/ttyUSB0`):

   ```text
   cd Boat
   mpremote connect COM3 cp main.py config.py sensors.py gps.py outbox.py simulation.py :
   mpremote connect COM3 reset
   ```

`main.py` runs automatically at boot. Nothing else in `Boat/` goes on the board.

To watch the boat's log or poke at it, open its REPL with
`mpremote connect COM3 repl` and press Ctrl-C to stop the loop:

```python
import sensors; sensors.read_probes()   # millivolts from every wired probe
import gps; gps.read_position()         # what the GPS reports right now
import outbox; outbox.count()           # readings waiting to be sent
import main; main.main()                # start the loop again
```

### Boat settings (`Boat/config.py`)

| Setting | |
|---|---|
| `DEVICE_NAME` | the boat's name, recorded with every reading |
| `WIFI_NAME`, `WIFI_PASSWORD` | the server's network (`Bloom`, open by default) |
| `SERVER_URL` | where readings go: `http://10.0.0.1/readings` |
| `READING_INTERVAL_SECONDS` | how often to take a reading (10). At a slow 0.5 m/s that is one every 5 m, about one per risk cell; lower it for denser coverage |
| `PROBES`, `ADC_BOARDS`, `I2C_*` | the wiring above |
| `ADC_FULL_SCALE_VOLTS`, `ADC_SAMPLES_PER_SECOND` | the ADS1115's input range (4.096 V covers its 3.3 V supply) and speed |
| `GPS_RX_PIN`, `GPS_TX_PIN` | required for a position |
| `OUTBOX_MAX_BYTES` | how much can wait on flash (512 KB, a few hours) |
| `SIMULATE_HARDWARE` | bench demo with no hardware; keep it `False` in the field |
| `SIMULATED_TRACK_CENTER`, `SIMULATED_TRACK_RADIUS_METERS` | where the bench demo's simulated boat wanders: a pond about 24 m across |

Every other number the boat uses (Wi-Fi timeouts, how much of each interval
may go on sending a backlog, the outbox's batch size) is in the same file,
each with a comment.

### When the server is out of range

Nothing is lost. Every reading is saved to the outbox on the boat's flash
before it is sent, and sent oldest-first whenever the server answers:

- Readings keep their 10-second pace whether or not the server is reachable;
  Wi-Fi reconnects in the background.
- A reading sent late keeps the time it was **taken**, not the time it arrived.
- A reading sent twice (a reply lost on a weak link) is only stored once.
- A long backlog is sent a little each cycle, so it never delays new readings.
- If the outbox fills (`OUTBOX_MAX_BYTES`), the oldest readings are dropped first.
- A power cut in the middle of tidying the outbox is repaired at the next boot.

If one probe stops answering, the boat sends the others and the dashboard shows
"Some probes not answering"; the risk is then worked out from what arrived.

---

## Calibration

The boat sends millivolts; `Server/config.py` turns them into units. The
values there are starting points, not measurements. Calibrate each probe with
standard solutions and enter what it actually reads. To see a probe's
millivolts, use `sensors.read_probes()` on the boat's REPL, or the "mV"
columns of the dashboard's CSV export.

| Probe | Measure | Set in `Server/config.py` |
|---|---|---|
| pH | pH 7.00 buffer, then pH 4.00 (or 10.00) | `PH_REFERENCE_MV` = the pH 7 reading, `PH_REFERENCE_PH` = 7.0, `PH_MV_PER_PH` = (reading at 4 − reading at 7) ÷ (4 − 7) |
| Temperature | — | `TEMPERATURE_SENSOR` = `"tmp36"` or `"lm35"` |
| Turbidity | clear water, then a turbidity standard | `TURBIDITY_CLEAR_MV`, `TURBIDITY_DIRTY_MV`, `TURBIDITY_DIRTY_NTU` |
| Conductivity | pure water, then a conductivity standard (e.g. 1413 µS/cm) | `CONDUCTIVITY_ZERO_MV`, `CONDUCTIVITY_CAL_MV`, `CONDUCTIVITY_CAL_US_CM` |
| Nitrate, ammonium | a standard, then one ten times stronger | `*_REFERENCE_MV`, `*_REFERENCE_MG_L`, `*_MV_PER_DECADE` = the difference between the two readings |

Restart the server after editing. Every reading keeps its raw millivolts
(they are in the CSV too), so readings taken before calibration can be
re-converted afterwards.

The risk thresholds and weights in the same file (`SENSORS`, `RISK_LEVELS`)
are provisional too; tune them against field observations.

---

## The server: Raspberry Pi 4

### Install

Use Raspberry Pi OS Bookworm, which already manages Wi-Fi with
NetworkManager. Then:

```text
sudo apt update
sudo apt install network-manager python3-flask
git clone <this repository>
```

(On another Linux, or for a newer Flask: `python3 -m pip install -r Server/requirements.txt`.)
The server needs Python 3.9 or newer and nothing else, until it scores with a
trained gradient-boosted tree model: then its library (`xgboost`,
`lightgbm`, or `scikit-learn`) is installed as
[Server/model/README.md](Server/model/README.md) describes.

### Start it

```text
sudo python3 Server/app.py
```

It needs `sudo` because it changes the Pi's network settings. It names the Pi
`rp`, creates the `Bloom` Wi-Fi network at `10.0.0.1`, and starts the web
server. The dashboard is then at `http://10.0.0.1` (and `http://rp.local` on
devices that support mDNS). Stopping it — Ctrl-C, `systemctl stop`, or the
dashboard's **Shut down server** button — takes the network down again.

**Every start opens a new mission**, so a restart never adds to the previous
deployment's records. Earlier missions stay in the database and open
read-only from the dashboard's mission picker. To carry on with the active
mission instead, set `NEW_MISSION_ON_START = False` in `Server/config.py`.

### Settings

Everything for the server and the dashboard is in `Server/config.py`, each
setting with a comment: the Wi-Fi name and address, where the map opens
(`MAP_CENTER`), the sizes on the water (5 m risk cells and small hotspots, set
for ponds), zoom levels, how often the dashboard refreshes, when the boat
counts as offline, and the rest. Restart the server after editing it; open
dashboards pick up the change on their next reload. The Wi-Fi password can
also come from the environment, which keeps it out of the repository:
`BLOOM_WIFI_PASSWORD` (at least 8 characters; unset means an open network).
`--port` serves on another port than 80.

The network is open by default, and anyone on it can end missions or shut the
server down. Set `BLOOM_WIFI_PASSWORD` if anyone other than the crew could
join it.

### Run at boot

Save this as `/etc/systemd/system/bloom.service`, with the path to your copy
of the repository:

```text
[Unit]
Description=Bloom Detection server and Wi-Fi network
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
ExecStart=/usr/bin/python3 /home/pi/Bloom-Detection/Server/app.py
# Environment=BLOOM_WIFI_PASSWORD=choose-one
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Once the trained model is in use, its library lives in a virtual
environment, so start the server with that environment's Python instead:
`ExecStart=/home/pi/bloom-env/bin/python3 /home/pi/Bloom-Detection/Server/app.py`.

Then:

```text
sudo systemctl daemon-reload
sudo systemctl enable --now bloom     # start now, and at every boot
sudo systemctl disable bloom          # stop starting at boot
journalctl -u bloom -f                # follow the log
```

The **Shut down server** button stops it cleanly, and systemd leaves it
stopped until the next boot.

### The data

Everything is in two SQLite files in `Server/data/`:

- `bloomdata.db`: the readings alone (id, mission, time taken, position, and
  each probe's value). This is the training data for the model: copy it off
  the Pi and load it as it is (see [Server/model/README.md](Server/model/README.md)).
- `missions.db`: everything else, pointing at readings by their id: missions,
  each reading's risk and delivery details, and alerts.

The two belong together: to back them up, stop the server and copy the whole
`Server/data/` folder. To start from scratch, stop the server and delete the
folder. The dashboard's **Download CSV** gives any mission as a spreadsheet.

### No internet needed

Nothing the dashboard shows comes from the internet: there is no street map
underneath, so the Pi's network never needs an internet connection and no
data is spent on map downloads. Every mission layer (readings, cells, track,
hotspots, revisit targets) is drawn over a metre grid, 5 m apart right in
close and coarser as you zoom out, with a scale bar for distances.

---

## Bench demo without hardware

Set `SIMULATE_HARDWARE = True` in `Boat/config.py` and the boat runs its normal
loop with simulated probe voltages and a simulated track around a pond about
24 m across (`SIMULATED_TRACK_CENTER`, `SIMULATED_TRACK_RADIUS_METERS`), marked
"Simulated" on the dashboard. It runs on a computer too, which needs no ESP32
at all:

```text
python3 Server/app.py --dev                        # in one terminal: http://localhost:5000
cd Boat && python3 main.py                         # in another, with SIMULATE_HARDWARE = True
                                                   # and SERVER_URL = "http://localhost:5000/readings"
```

Set it back to `False` before the boat goes in the water: with it off,
nothing invented can end up in the field record.
