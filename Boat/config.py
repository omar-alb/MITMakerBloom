# --- Identity and network --------------------------------------------------

DEVICE_NAME = "boat"
WIFI_NAME = "Bloom"
WIFI_PASSWORD = None  # None for an open network
SERVER_URL = "http://10.0.0.1/readings" #"http://localhost:5000/readings"

# --- Timing ----------------------------------------------------------------

READING_INTERVAL_SECONDS = 10
HTTP_TIMEOUT_SECONDS = 8
WIFI_CONNECT_ATTEMPTS = 3  # at boot
WIFI_CONNECT_WAIT_SECONDS = 15  # per attempt, at boot
WIFI_ERROR_PAUSE_SECONDS = 5  # at boot, after a radio error
WIFI_RETRY_SECONDS = 20  # after boot, background reconnect interval

# Max share of each interval spent sending the backlog.
SEND_SHARE_OF_INTERVAL = 0.6

# --- Outbox ----------------------------------------------------------------
# Readings wait here until the server has them. About 300 bytes each; when
# full, the oldest are dropped. Rewrites go through a copy, so keep this much
# flash free again on top.

OUTBOX_FILE = "outbox.jsonl"
OUTBOX_MAX_BYTES = 512 * 1024
OUTBOX_BATCH_SIZE = 25  # readings sent or dropped at a time

# --- Probes ----------------------------------------------------------------
# name: (board index in ADC_BOARDS, input 0-3). Names must match SENSORS in
# Server/config.py. List only connected probes: a floating input reads noise.

I2C_ID = 0
I2C_SDA_PIN = 21
I2C_SCL_PIN = 22
I2C_FREQUENCY = 400000
ADC_BOARDS = (0x48, 0x4A)

# Ranges: 6.144, 4.096, 2.048, 1.024, 0.512, 0.256 V (4.096 covers a 3.3 V supply).
# Speeds: 8, 16, 32, 64, 128, 250, 475, 860 samples/s.
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
# GPS_RX_PIN takes the module's TX wire. Avoid GPIO 6-11 (flash), and 16-17
# on WROVER modules (PSRAM).

GPS_RX_PIN = 16
GPS_TX_PIN = 17
GPS_UART_ID = 1
GPS_BAUD = 9600
GPS_READ_TIMEOUT_MS = 2000
GPS_STALE_LIMIT_SECONDS = 60  # how long the last fix may stand in for a lost one

# --- Bench demo ------------------------------------------------------------
# Simulated probes and track, no hardware. Keep False in the field.

SIMULATE_HARDWARE = False
SIMULATED_TRACK_CENTER = (42.36003, -71.09413)
SIMULATED_TRACK_RADIUS_METERS = 12
