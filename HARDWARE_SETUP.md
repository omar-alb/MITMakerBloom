# Hardware setup

1. **The boat:** a 3D-printed twin-hull boat with an ESP32, two ADS1115
   analog-to-digital boards, a GPS module, and six water-quality probes.
2. **The server:** a Raspberry Pi 4 that runs its own Wi-Fi network, `Bloom`,
   receives the readings, and serves the dashboard.

```text
Boat (ESP32)  -- Wi-Fi "Bloom" -->  Raspberry Pi 4 at 10.0.0.1  <-- Wi-Fi "Bloom" --  phone or laptop
```

The boat sends raw probe voltages; the Pi converts and scores them, so
calibrating never needs a reflash.

---

## The boat

### Wiring

Two ADS1115 boards on the ESP32's I2C bus (SDA GPIO 21, SCL GPIO 22). The
second board's ADDR pin is tied so it answers at `0x4A`; the first is `0x48`.

| Probe | Board | Input |
|---|---|---|
| pH electrode | 0x48 | A0
| Temperature (TMP36, or LM35) | 0x48 | A1
| Turbidity | 0x48 | A2
| Conductivity | 0x48 | A3
| Nitrate ion-selective electrode | 0x4A | A0
| Ammonium ion-selective electrode | 0x4A | A1

`PROBES` in `Boat/config.py` maps each probe to its input. If a probe is taken
off, remove it from `PROBES`: a floating input reads noise.

### GPS

NMEA over a UART: the module's TX wire goes to GPIO 16 (`GPS_RX_PIN`), and
`GPS_TX_PIN` is GPIO 17. If you move them, avoid GPIO 6–11, and 16–17 on a
WROVER module.

| GPS on the dashboard | Meaning | On the map? |
|---|---|---|
| Fix | a satellite lock just now | yes |
| Last known position | lock lost under a minute ago; the last fix is reused | yes |
| No fix | no lock | no |
| No GPS | pins not set, or the UART failed | no |

Readings without a position still appear in the table, alerts, and CSV.

### Flashing

1. Install MicroPython on the ESP32, erasing the flash first
   ([micropython.org](https://micropython.org/download/ESP32_GENERIC/)).
2. Copy the six files (the port is `COM3` here; on Linux, something like `/dev/ttyUSB0`):

   ```text
   cd Boat
   mpremote connect COM3 cp main.py config.py sensors.py gps.py outbox.py simulation.py :
   mpremote connect COM3 reset
   ```

`main.py` runs at boot. To poke at the boat, open the REPL with
`mpremote connect COM3 repl` and press Ctrl-C:

```python
import sensors; sensors.read_probes()   # millivolts from every probe
import gps; gps.read_position()         # the GPS right now
import outbox; outbox.count()           # readings waiting to be sent
import main; main.main()                # start the loop again
```

### Boat settings (`Boat/config.py`)

| Setting | |
|---|---|
| `DEVICE_NAME` | recorded with every reading |
| `WIFI_NAME`, `WIFI_PASSWORD` | the server's network |
| `SERVER_URL` | `http://10.0.0.1/readings` |
| `READING_INTERVAL_SECONDS` | 10; at 0.5 m/s that's one reading every 5 m |
| `PROBES`, `ADC_BOARDS`, `I2C_*` | the wiring above |
| `GPS_RX_PIN`, `GPS_TX_PIN` | the GPS wiring |
| `OUTBOX_MAX_BYTES` | how much can wait on flash (512 KB, a few hours) |
| `SIMULATE_HARDWARE` | bench demo; keep `False` in the field |

### When the server is out of range

Every reading is saved to the outbox before it is sent, and sent oldest-first
once the server answers. Readings keep their pace and their original time; a
reading sent twice is stored once; a backlog is sent a little each cycle; a
full outbox drops its oldest readings. If a probe stops answering, the others
are still sent.

---

## Calibration

The values in `Server/config.py` are placeholders. Measure each probe in
standard solutions (see its millivolts with `sensors.read_probes()` or the CSV's
"mV" columns) and enter them:

| Probe | Measure | Set in `Server/config.py` |
|---|---|---|
| pH | pH 7.00 buffer, then pH 4.00 (or 10.00) | `PH_REFERENCE_MV` = the pH 7 reading, `PH_MV_PER_PH` = (reading at 4 − reading at 7) ÷ (4 − 7) |
| Temperature | — | `TEMPERATURE_SENSOR` = `"tmp36"` or `"lm35"` |
| Turbidity | clear water, then a turbidity standard | `TURBIDITY_CLEAR_MV`, `TURBIDITY_DIRTY_MV`, `TURBIDITY_DIRTY_NTU` |
| Conductivity | pure water, then a standard (e.g. 1413 µS/cm) | `CONDUCTIVITY_ZERO_MV`, `CONDUCTIVITY_CAL_MV`, `CONDUCTIVITY_CAL_US_CM` |
| Nitrate, ammonium | a standard, then one ten times stronger | `*_REFERENCE_MV`, `*_REFERENCE_MG_L`, `*_MV_PER_DECADE` = second reading − first (its sign matters) |

Restart the server after editing. Raw millivolts are kept, so older readings
can be re-converted. The risk thresholds and weights (`SENSORS`) need tuning
against field data too.

---

## The server: Raspberry Pi 4

### Install

On Raspberry Pi OS Bookworm (Python 3.9+):

```text
sudo apt update
sudo apt install network-manager python3-flask
git clone <this repository>
```

A trained model also needs its library; see [Server/model/README.md](Server/model/README.md).

### Start it

```text
sudo python3 Server/app.py
```

`sudo` is needed to create the `Bloom` network at `10.0.0.1`. The dashboard is
at `http://10.0.0.1` (or `http://rp.local`). Stopping the server (Ctrl-C,
`systemctl stop`, or the **Shut down server** button) takes the network down.

Every start opens a new mission; set `NEW_MISSION_ON_START = False` in
`Server/config.py` to continue the active one instead.

### Settings

Everything is in `Server/config.py`; restart after editing. The network is
open unless `BLOOM_WIFI_PASSWORD` is set (8+ characters), and anyone on it can
end missions or shut the server down.

### Run at boot

Save as `/etc/systemd/system/bloom.service`:

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

With a trained model, use the virtual environment's Python:
`ExecStart=/home/pi/bloom-env/bin/python3 /home/pi/Bloom-Detection/Server/app.py`.

```text
sudo systemctl daemon-reload
sudo systemctl enable --now bloom     # start now and at every boot
sudo systemctl disable bloom          # stop starting at boot
journalctl -u bloom -f                # follow the log
```

### The data

Two SQLite files in `Server/data/`: `bloomdata.db` (the readings, the model's
training data) and `missions.db` (missions, risks, alerts). To back up, stop
the server and copy the folder; to start fresh, delete it. **Download CSV**
exports any mission.

---

## Bench demo without hardware

Set `SIMULATE_HARDWARE = True` for simulated probes and a track around a pond
about 24 m across. It also runs on a computer:

```text
python3 Server/app.py --dev       # one terminal: http://localhost:5000
cd Boat && python3 main.py        # another, with SIMULATE_HARDWARE = True
                                  # and SERVER_URL = "http://localhost:5000/readings"
```

Set it back to `False` before the boat goes in the water.
