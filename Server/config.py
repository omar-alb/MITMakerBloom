"""Every server and dashboard setting. The dashboard gets its share as /config.js.

The Wi-Fi password can come from the BLOOM_WIFI_PASSWORD environment variable.

IMPORTANT NOTE:
These are all placeholder calibrations and rules. They are not based on real data,
and they are not a substitute for field testing.
The boat's readings are not reliable until the probes have been calibrated against known standards.
This is temporary data
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SERVER_DIR = Path(__file__).resolve().parent
WEB_DIR = SERVER_DIR / "web"


# --- Data --------------------------------------------------------------------

READINGS_DATABASE = SERVER_DIR / "data" / "bloomdata.db"  # the readings: training data
MISSIONS_DATABASE = SERVER_DIR / "data" / "missions.db"  # missions, risks, details, alerts
DATABASE_WAIT_SECONDS = 15  # wait for another write instead of "database is locked"


# --- Web server -------------------------------------------------------------

HOST = "0.0.0.0"
PORT = 80
DEV_PORT = 5000  # with --dev

# True: every server start opens a new mission. False: resume the active one.
NEW_MISSION_ON_START = True

SHUTDOWN_DELAY_SECONDS = 0.5  # lets the Shut down answer reach the browser first


# --- Wi-Fi access point (Raspberry Pi only) ---------------------------------

WIFI_NAME = "Bloom"
WIFI_PASSWORD = os.environ.get("BLOOM_WIFI_PASSWORD") or None  # None: open network; else 8+ characters
WIFI_INTERFACE = "wlan0"
ADDRESS = "10.0.0.1"
ADDRESS_PREFIX_LENGTH = 24
HOSTNAME = "rp"  # also http://rp.local where mDNS works
ACCESS_POINT_CONNECTION = "BloomAP"  # NetworkManager connection name


# --- Input limits ------------------------------------------------------------

READING_MAX_AGE_SECONDS = 7 * 24 * 3600  # older claimed ages use the arrival time
TEXT_MAX_LENGTH = 128  # device name, sample_id
MISSION_NAME_MAX_LENGTH = 120


# --- Sensors ----------------------------------------------------------------


@dataclass(frozen=True)
class Sensor:
    key: str  # used by the boat, database, dashboard, and CSV
    label: str
    unit: str
    digits: int  # decimals stored and shown
    weight: float  # share of the risk score
    low: float  # no risk at or below
    high: float  # full risk at or above
    driver: str  # named in alerts when it drives the score
    alert_at: Optional[float] = None  # sensor alert at or above


SENSORS = (
    Sensor("temperature", "Temperature", "°C", 1, 0.24, 18.0, 28.0, "warm water"),
    Sensor("ph", "pH", "", 2, 0.16, 7.8, 9.1, "high pH"),
    Sensor("turbidity", "Turbidity", "NTU", 1, 0.20, 5.0, 35.0, "high turbidity", alert_at=20.0),
    Sensor("conductivity", "Conductivity", "µS/cm", 0, 0.10, 450.0, 1000.0, "high conductivity"),
    Sensor("nitrate", "Nitrate", "mg/L", 2, 0.15, 0.5, 5.0, "high nitrate", alert_at=10.0),
    Sensor("ammonium", "Ammonium", "mg/L", 2, 0.15, 0.05, 1.0, "high ammonium", alert_at=1.0),
)


# --- Calibration: millivolts to units ---------------------------------------
# Raw millivolts are kept in missions.db, so readings can be re-converted later.

TEMPERATURE_SENSOR = "tmp36"  # "tmp36" (500 mV at 0 °C) or "lm35" (0 mV at 0 °C)
TEMPERATURE_RANGE_C = (-10.0, 60.0)

PH_REFERENCE_MV = 1500.0  # reading in a PH_REFERENCE_PH buffer
PH_REFERENCE_PH = 7.0
PH_MV_PER_PH = -59.16

TURBIDITY_CLEAR_MV = 3000.0  # 0 NTU
TURBIDITY_DIRTY_MV = 1000.0  # in a TURBIDITY_DIRTY_NTU standard
TURBIDITY_DIRTY_NTU = 100.0

CONDUCTIVITY_ZERO_MV = 450.0  # pure water
CONDUCTIVITY_CAL_MV = 2200.0  # in a CONDUCTIVITY_CAL_US_CM standard
CONDUCTIVITY_CAL_US_CM = 2000.0
CONDUCTIVITY_PER_DEGREE = 0.019  # compensation to 25 °C

NITRATE_REFERENCE_MV = 1500.0  # in a NITRATE_REFERENCE_MG_L standard
NITRATE_REFERENCE_MG_L = 10.0
NITRATE_MV_PER_DECADE = -59.16

AMMONIUM_REFERENCE_MV = 1500.0  # in an AMMONIUM_REFERENCE_MG_L standard
AMMONIUM_REFERENCE_MG_L = 1.0
AMMONIUM_MV_PER_DECADE = -59.16

# Nitrate and ammonium: trusted this many tenfold steps from the standard, capped at this.
ION_SELECTIVE_MAX_DECADES = 3
ION_SELECTIVE_MAX_MG_L = 1000.0


# --- Bloom risk -------------------------------------------------------------

# Each level runs from its min up to the next one's.
RISK_LEVELS = (
    {"label": "Low", "min": 0, "color": "#6fae7e", "advice": "Routine monitoring."},
    {"label": "Moderate", "min": 26, "color": "#d0a44c", "advice": "Monitor soon."},
    {"label": "High", "min": 51, "color": "#d4834a", "advice": "Sample here again soon."},
    {"label": "Severe", "min": 76, "color": "#c85a55", "advice": "Sample here immediately."},
)
ALERT_LEVELS = ("High", "Severe")  # raise a bloom alert and can form a hotspot
SENSOR_ALERT_LEVEL = "Moderate"  # colour of sensor alerts

# A score from probes carrying less than this share of the total weight is
# scaled down, so one elevated probe alone can't raise an alert.
FULL_EVIDENCE_SHARE = 0.5

DRIVER_THRESHOLD = 60  # a probe's part of the score (0-100) that names it as a driver
CONFIDENCE_RANGE = (30, 90)  # no probes reporting to all probes reporting

# Cell and headline scores: the mean pulled this far toward the peak.
ROLLUP_PEAK_SHARE = 0.45


# --- The trained model (see model/README.md) --------------------------------

MODEL_DIR = SERVER_DIR / "model"

# Tried on the model when it loads, so a broken model fails at startup.
MODEL_TRIAL_READING = {
    "temperature": 22.0, "ph": 7.6, "turbidity": 9.0, "conductivity": 520.0, "nitrate": 1.0, "ammonium": 0.1,
}


# --- Sizes on the water -----------------------------------------------------
# GPS is good to about 2-5 m, so nothing here is finer than 5 m.

CELL_SIZE_METERS = 5

# Grid spacings, finest first; the map uses the finest readable one. Keep
# CELL_SIZE_METERS in the list so lines fall on cell edges.
MAP_GRID_METERS = (5, 10, 25, 50, 100, 250, 500, 1000)

HOTSPOT_JOIN_METERS = 10  # alert readings this close to a hotspot's centre join it
HOTSPOT_MARGIN_METERS = 3
HOTSPOT_MIN_RADIUS_METERS = 5
HOTSPOT_MAX_RADIUS_METERS = 20

# Revisit priority (weights add up to 1). Distance and age count in full from
# HOTSPOT_FAR_METERS and HOTSPOT_OLD_SECONDS.
HOTSPOT_PRIORITY_WEIGHTS = {"risk": 0.5, "uncertainty": 0.25, "distance": 0.15, "age": 0.1}
HOTSPOT_FAR_METERS = 25
HOTSPOT_OLD_SECONDS = 15 * 60

REVISIT_TARGETS = 5  # hotspots numbered on the map
SHADING_RADIUS_METERS = 3


# --- Dashboard --------------------------------------------------------------

MAP_CENTER = (42.36003, -71.09413)  # before the mission's first GPS fix

MAP_MAX_ZOOM = 22
MAP_FIT_ZOOM = 21  # cap for "Fit to mission"
MAP_OPEN_ZOOM = 19  # on MAP_CENTER
MAP_FOCUS_ZOOM = 20  # when a reading is picked in the table
MAP_GRID_MIN_PIXELS = 20  # closer grid lines switch to the next spacing

DASHBOARD_POLL_SECONDS = 9
DASHBOARD_TIMEOUT_SECONDS = 12
BOAT_OFFLINE_AFTER_SECONDS = 30

# Newest readings sent for markers, table, and replay. Cells, hotspots, and
# the headline use up to MISSION_READING_LIMIT.
DASHBOARD_READINGS = 500
MISSION_READING_LIMIT = 20000

DASHBOARD_ALERTS = 100
ALERT_PAGE_SIZE = 5
TABLE_PAGE_SIZE = 10
TABLE_PAGE_SIZE_PHONE = 10
REPLAY_STEP_SECONDS = 0.6
CSV_BATCH_READINGS = 500
