# --- Identity and network --------------------------------------------------

DEVICE_NAME = "boat"
WIFI_NAME = "Bloom"
WIFI_PASSWORD = None  # None for the server's default open network
SERVER_URL = "http://10.0.0.1/readings" #"http://localhost:5000/readings"

# --- Timing ----------------------------------------------------------------

READING_INTERVAL_SECONDS = 10
HTTP_TIMEOUT_SECONDS = 8
WIFI_CONNECT_ATTEMPTS = 3  # at boot
WIFI_CONNECT_WAIT_SECONDS = 15  # per attempt, at boot
WIFI_ERROR_PAUSE_SECONDS = 5  # at boot, after the radio reports an error
WIFI_RETRY_SECONDS = 20  # while running, reconnects in the background this often

# Sending a backlog may take up to this share of each interval, so the next
# reading is always taken on time.
SEND_SHARE_OF_INTERVAL = 0.6

# --- Outbox ----------------------------------------------------------------
# Every reading is saved here before it is sent, so nothing is lost while the
# server is out of range. A reading is about 300 bytes, so the cap holds a few
# hours of readings; past it the oldest are dropped first. Removing sent
# readings rewrites the file through a copy, so leave this much flash free
# again on top of it.

OUTBOX_FILE = "outbox.jsonl"
OUTBOX_MAX_BYTES = 512 * 1024

# Readings are sent, and dropped when the outbox is full, this many at a
# time. It also bounds how many are ever held in RAM.
OUTBOX_BATCH_SIZE = 25

# --- Probes ----------------------------------------------------------------
# Each probe is wired to one input of an ADS1115 board, written as
# (board index in ADC_BOARDS, input 0-3). The names must match the server's
# SENSORS in Server/config.py.
#
# Only list probes that are actually connected. An unconnected input floats,
# and the server would turn that noise into a real-looking reading.

I2C_ID = 0
I2C_SDA_PIN = 21
I2C_SCL_PIN = 22
I2C_FREQUENCY = 400000
ADC_BOARDS = (0x48, 0x4A) 

# The ADS1115's input range and speed. Powered from 3.3 V it cannot measure
# above that, so 4.096 V is the finest range that still covers every probe.
# Ranges: 6.144, 4.096, 2.048, 1.024, 0.512, or 0.256 V.
# Speeds: 8, 16, 32, 64, 128, 250, 475, or 860 samples a second.
ADC_FULL_SCALE_VOLTS = 4.096
ADC_SAMPLES_PER_SECOND = 128

PROBES = {
    "ph": (0, 0),
    "temperature": (0, 1),
    "turbidity": (0, 2),
    "conductivity": (0, 3),
    "nitrate": (1, 0),
    "ammonium": (1, 1),
}

# --- GPS -------------------------------------------------------------------
# NMEA over UART. GPS_RX_PIN is the ESP32 pin the GPS module's TX wire is
# connected to; GPS_TX_PIN can be any free pin (nothing is sent to the GPS).
# Avoid GPIO 6-11 (flash) and, on WROVER modules, 16-17 (PSRAM). The UART's
# default pins, GPIO 9 and 10, belong to the flash chip, which is why there
# is no default here.

GPS_RX_PIN = 16
GPS_TX_PIN = 17
GPS_UART_ID = 1
GPS_BAUD = 9600
GPS_READ_TIMEOUT_MS = 2000  # long enough to catch the receiver's next 1 s burst
GPS_STALE_LIMIT_SECONDS = 60  # how long the last fix may stand in for a lost one

# --- Bench demo ------------------------------------------------------------
# True runs the boat with no hardware at all: simulated probe voltages and a
# simulated track, marked as simulated on the dashboard. Keep it False for
# anything that will be recorded as field data.

SIMULATE_HARDWARE = False

# The simulated boat wanders around this spot, up to this far north, south,
# east, and west of it: a pond about 24 m across.
SIMULATED_TRACK_CENTER = (42.36003, -71.09413)
SIMULATED_TRACK_RADIUS_METERS = 12
