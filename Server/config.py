"""Every server and dashboard setting, in one place.

Every number the server or the dashboard works with is here, so nothing has
to be hunted for in the code. (What stays in the code are facts rather than
choices: the size of the Earth, the pH scale, a sensor chip's datasheet.)
The dashboard's settings reach the browser as /config.js.

The Wi-Fi password can also come from the environment (BLOOM_WIFI_PASSWORD),
which keeps it out of the repository.

Sizes are set for small ponds: 5 m risk cells, hotspots a few metres across.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SERVER_DIR = Path(__file__).resolve().parent
WEB_DIR = SERVER_DIR / "web"


# --- Data --------------------------------------------------------------------

# The readings themselves (when, where, and each probe's value in its unit),
# kept on their own as the model's training data...
READINGS_DATABASE = SERVER_DIR / "data" / "bloomdata.db"
# ...and everything around them: missions, each reading's risk and details, alerts.
MISSIONS_DATABASE = SERVER_DIR / "data" / "missions.db"

# A request waits this long for another one's write to finish, rather than
# failing with "database is locked".
DATABASE_WAIT_SECONDS = 15


# --- Web server -------------------------------------------------------------

HOST = "0.0.0.0"
PORT = 80  # in the field: http://10.0.0.1
DEV_PORT = 5000  # with --dev, on any computer: http://localhost:5000

# Each start of the server ends the active mission and opens a new one, so a
# restart never adds to the previous deployment's records. False resumes it.
NEW_MISSION_ON_START = True

# The Shut down button waits this long before stopping the server, so its
# answer reaches the browser first.
SHUTDOWN_DELAY_SECONDS = 0.5


# --- Wi-Fi access point (on the Raspberry Pi; not with --dev) ---------------

WIFI_NAME = "Bloom"
# None makes an open network. A password must be at least 8 characters.
WIFI_PASSWORD = os.environ.get("BLOOM_WIFI_PASSWORD") or None
WIFI_INTERFACE = "wlan0"
ADDRESS = "10.0.0.1"
ADDRESS_PREFIX_LENGTH = 24  # a /24 network: devices get 10.0.0.2 to 10.0.0.254
HOSTNAME = "rp"  # also http://rp.local on devices that support mDNS
ACCESS_POINT_CONNECTION = "BloomAP"  # NetworkManager's name for the network


# --- What the boat and the dashboard may send --------------------------------

# A queued reading claiming to be older than this gets its arrival time
# instead. (The boat itself never claims more than about six days.)
READING_MAX_AGE_SECONDS = 7 * 24 * 3600

# The longest device name or sample_id a reading may carry, and the longest
# mission name.
TEXT_MAX_LENGTH = 128
MISSION_NAME_MAX_LENGTH = 120


# --- Sensors ----------------------------------------------------------------


@dataclass(frozen=True)
class Sensor:
    key: str  # the one name the boat, database, dashboard, and CSV all use
    label: str
    unit: str
    digits: int  # decimals stored and shown
    weight: float  # share of the bloom risk score
    low: float  # at or below this the reading adds no risk,
    high: float  # at or above this it adds its full weight
    driver: str  # how a high value is named when it drives the score
    alert_at: Optional[float] = None  # raise a sensor alert at or above this


# Provisional thresholds and weights: calibrate them against field data.
SENSORS = (
    Sensor("temperature", "Temperature", "°C", 1, 0.24, 18.0, 28.0, "warm water"),
    Sensor("ph", "pH", "", 2, 0.16, 7.8, 9.1, "high pH"),
    Sensor("turbidity", "Turbidity", "NTU", 1, 0.20, 5.0, 35.0, "high turbidity", alert_at=20.0),
    Sensor("conductivity", "Conductivity", "µS/cm", 0, 0.10, 450.0, 1000.0, "high conductivity"),
    Sensor("nitrate", "Nitrate", "mg/L", 2, 0.15, 0.5, 5.0, "high nitrate", alert_at=10.0),
    Sensor("ammonium", "Ammonium", "mg/L", 2, 0.15, 0.05, 1.0, "high ammonium", alert_at=1.0),
)


# --- Calibration: raw millivolts to units ----------------------------------
# Starting values, not measurements. Calibrate each probe against known
# standards and enter what it actually reads. Every value stored in
# bloomdata.db has been through these; the raw millivolts are kept in
# missions.db, so readings can be re-converted after a recalibration.

TEMPERATURE_SENSOR = "tmp36"  # "tmp36" (500 mV at 0 °C) or "lm35" (0 mV at 0 °C), both 10 mV/°C
TEMPERATURE_RANGE_C = (-10.0, 60.0)  # anything outside is a probe out of the water or unplugged

PH_REFERENCE_MV = 1500.0  # the probe's reading in a pH PH_REFERENCE_PH buffer
PH_REFERENCE_PH = 7.0
PH_MV_PER_PH = -59.16

TURBIDITY_CLEAR_MV = 3000.0  # in clear water (0 NTU)
TURBIDITY_DIRTY_MV = 1000.0  # in a TURBIDITY_DIRTY_NTU standard
TURBIDITY_DIRTY_NTU = 100.0

CONDUCTIVITY_ZERO_MV = 450.0  # in pure water
CONDUCTIVITY_CAL_MV = 2200.0  # in a CONDUCTIVITY_CAL_US_CM standard
CONDUCTIVITY_CAL_US_CM = 2000.0
CONDUCTIVITY_PER_DEGREE = 0.019  # temperature compensation to 25 °C

NITRATE_REFERENCE_MV = 1500.0  # in a NITRATE_REFERENCE_MG_L standard
NITRATE_REFERENCE_MG_L = 10.0
NITRATE_MV_PER_DECADE = -59.16

AMMONIUM_REFERENCE_MV = 1500.0  # in an AMMONIUM_REFERENCE_MG_L standard
AMMONIUM_REFERENCE_MG_L = 1.0
AMMONIUM_MV_PER_DECADE = -59.16

# Ion-selective probes (nitrate, ammonium) are trusted this many tenfold
# steps either side of their standard, and never above this concentration.
ION_SELECTIVE_MAX_DECADES = 3
ION_SELECTIVE_MAX_MG_L = 1000.0


# --- Bloom risk -------------------------------------------------------------

# The one ladder every score (0-100) is read against. A level starts at `min`
# and runs up to the next level's min. The colours are used on the map.
RISK_LEVELS = (
    {"label": "Low", "min": 0, "color": "#6fae7e", "advice": "Routine monitoring."},
    {"label": "Moderate", "min": 26, "color": "#d0a44c", "advice": "Monitor soon."},
    {"label": "High", "min": 51, "color": "#d4834a", "advice": "Sample here again soon."},
    {"label": "Severe", "min": 76, "color": "#c85a55", "advice": "Sample here immediately."},
)

# Readings at these levels raise a bloom alert, get a ring on the map, and
# can form a hotspot.
ALERT_LEVELS = ("High", "Severe")

# Sensor alerts (a sensor reaching its `alert_at`) are shown in this level's colour.
SENSOR_ALERT_LEVEL = "Moderate"

# A score needs probes carrying this share of the total weight to count in
# full. With less it is scaled down in proportion, so one elevated probe on
# its own cannot raise an alert. The four probes wired today carry 0.70.
FULL_EVIDENCE_SHARE = 0.5

# A probe whose part of the score reaches this (out of 100) is named as one
# of the reasons for it.
DRIVER_THRESHOLD = 60

# Confidence in a score (0-100): this with no probes reporting, rising to the
# second with every probe. It stops short of 100: even a full set of probes
# gives an estimate.
CONFIDENCE_RANGE = (30, 90)

# A cell's score, and the mission's headline score, blend the mean of the
# scores inside with the peak, so one hot reading is neither averaged away
# nor allowed to speak for its whole area.
ROLLUP_PEAK_SHARE = 0.45


# --- The trained model (see model/README.md) ----------------------------------

MODEL_DIR = SERVER_DIR / "model"

# The model is tried on this reading when it loads (sensors not listed are
# missing), so a broken model shows up at startup, not in the field.
MODEL_TRIAL_READING = {"temperature": 22.0, "ph": 7.6, "turbidity": 9.0, "conductivity": 520.0}


# --- Sizes on the water -----------------------------------------------------
# Set for small ponds. A consumer GPS is good to about 2-5 m, so cells
# smaller than 5 m would add scatter rather than detail.

# Risk cells: the coloured squares on the map. Each is this many metres on a side.
CELL_SIZE_METERS = 5

# The background grid's line spacings, finest first. The dashboard uses the
# finest one that is still readable at the current zoom: 5 m right in close
# (GPS is only good to a few metres, so finer lines would imply precision the
# readings do not have), coarser as you zoom out. Keep CELL_SIZE_METERS in the list, and the others
# dividing it or multiples of it, so grid lines fall on the cells' edges.
MAP_GRID_METERS = (5, 10, 25, 50, 100, 250, 500, 1000)

# Hotspots: the circles around clusters of High and Severe readings.
# Alert-level readings within this distance of a hotspot's centre join it.
HOTSPOT_JOIN_METERS = 10

# A hotspot's circle covers its readings plus this margin, but its radius is
# never smaller than the minimum (so a single reading still shows clearly)
# or larger than the maximum.
HOTSPOT_MARGIN_METERS = 3
HOTSPOT_MIN_RADIUS_METERS = 5
HOTSPOT_MAX_RADIUS_METERS = 20

# Which hotspot to go back to first. Its priority is made of these (they add
# up to 1): its risk, how unsure that risk is, how far the boat is from it
# (counting in full from HOTSPOT_FAR_METERS), and how long since it was
# sampled (in full from HOTSPOT_OLD_SECONDS).
HOTSPOT_PRIORITY_WEIGHTS = {"risk": 0.52, "uncertainty": 0.23, "distance": 0.14, "age": 0.11}
HOTSPOT_FAR_METERS = 25
HOTSPOT_OLD_SECONDS = 15 * 60

# The hotspots most worth going back to are numbered on the map, up to this many.
REVISIT_TARGETS = 5

# "Risk shading" on the map: a coloured halo this wide around each reading.
SHADING_RADIUS_METERS = 3


# --- Dashboard --------------------------------------------------------------

# Where the map opens before the mission has a GPS fix: (latitude, longitude).
MAP_CENTER = (42.28084, -83.74304)

# Map zoom levels (each step in is twice as close; at 21 a 5 m cell is about
# 90 px across). MAP_FIT_ZOOM caps "Fit to mission", so a single reading is
# not shown absurdly close; MAP_OPEN_ZOOM is used on MAP_CENTER before the
# first fix; MAP_FOCUS_ZOOM is the least a reading picked in the table zooms to.
MAP_MAX_ZOOM = 22
MAP_FIT_ZOOM = 21
MAP_OPEN_ZOOM = 19
MAP_FOCUS_ZOOM = 20

# Grid lines closer than this on screen switch to the next spacing up.
MAP_GRID_MIN_PIXELS = 20

# How often an open dashboard asks for news, and how long it waits for an answer.
DASHBOARD_POLL_SECONDS = 9
DASHBOARD_TIMEOUT_SECONDS = 12

# The boat counts as online while it was heard from this recently. It sends
# every 10 s, so this allows two missed readings.
BOAT_OFFLINE_AFTER_SECONDS = 30

# The dashboard gets this many of the newest readings for its markers, table,
# and replay. Cells, hotspots, and the headline score always use the whole
# mission, up to MISSION_READING_LIMIT readings (a day at 10 s is about 8,600).
DASHBOARD_READINGS = 500
MISSION_READING_LIMIT = 20000

# Alerts shown at a time, newest first.
DASHBOARD_ALERTS = 100

# Alerts per page of the Alerts panel.
ALERT_PAGE_SIZE = 5

# Readings per page of the table, on a computer and on a phone.
TABLE_PAGE_SIZE = 10
TABLE_PAGE_SIZE_PHONE = 10

# Replay steps from one reading to the next this often.
REPLAY_STEP_SECONDS = 0.6

# The CSV export reads this many readings at a time, so a long mission
# streams out without being held in memory.
CSV_BATCH_READINGS = 500
