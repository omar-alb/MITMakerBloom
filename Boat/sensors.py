"""The water probes: an ADS1115 driver and one millivolt value per wired probe.

The boat sends raw millivolts only. The server converts them into pH,
degrees, NTU, and so on, so recalibrating never means reflashing the boat.
"""
import time

import config

try:
    from machine import I2C, Pin
except ImportError:  # desktop Python, for tests
    I2C = Pin = None

_boards = None  # one ADS1115 per address in config.ADC_BOARDS, opened on first use


def read_probes():
    """Millivolts from every wired probe, and "ok" or "partial".

    Each probe is read on its own, so a probe (or a whole board) that does
    not answer is left out of this reading instead of losing the others; the
    server scores whatever arrived. Raises only when no probe answered.
    """
    global _boards
    if _boards is None:
        _boards = _open_boards()

    millivolts = {}
    failed = []
    for name, (board, channel) in config.PROBES.items():
        try:
            volts = _boards[board].read_volts(channel)
        except Exception as error:
            failed.append("{} ({})".format(name, error))
            continue
        millivolts[name] = round(volts * 1000, 2)

    if failed:
        print("Probes not answering:", ", ".join(failed))
        # Reopen the bus next time, in case a board was reseated.
        _boards = None
    if not millivolts:
        raise RuntimeError("No probe answered.")
    return millivolts, "partial" if failed else "ok"


def _open_boards():
    if I2C is None:
        raise RuntimeError("The machine module is missing; this is not running on the ESP32.")
    i2c = I2C(
        config.I2C_ID,
        sda=Pin(config.I2C_SDA_PIN),
        scl=Pin(config.I2C_SCL_PIN),
        freq=config.I2C_FREQUENCY,
    )
    return [ADS1115(i2c, address) for address in config.ADC_BOARDS]


class ADS1115:
    """Single-shot, single-ended reads from a TI ADS1115 over I2C."""

    CONVERSION_REGISTER = 0x00
    CONFIG_REGISTER = 0x01
    GAIN_BITS = {6.144: 0, 4.096: 1, 2.048: 2, 1.024: 3, 0.512: 4, 0.256: 5}
    RATE_BITS = {8: 0, 16: 1, 32: 2, 64: 3, 128: 4, 250: 5, 475: 6, 860: 7}
    # Give up waiting for the ready bit after this many polls and read anyway,
    # so a bus glitch cannot hang the reading loop.
    READY_POLLS = 12

    def __init__(self, i2c, address, full_scale_volts=None, samples_per_second=None):
        self.i2c = i2c
        self.address = address
        self.full_scale_volts = full_scale_volts or config.ADC_FULL_SCALE_VOLTS
        self.samples_per_second = samples_per_second or config.ADC_SAMPLES_PER_SECOND
        self.gain_bits = self.GAIN_BITS[self.full_scale_volts]
        self.rate_bits = self.RATE_BITS[self.samples_per_second]

    def read_volts(self, channel):
        if channel not in (0, 1, 2, 3):
            raise ValueError("channel must be 0-3")

        start_single_shot = 1 << 15
        input_select = (4 + channel) << 12  # channel against ground
        single_shot_mode = 1 << 8
        comparator_off = 0x03
        self._write(
            self.CONFIG_REGISTER,
            start_single_shot | input_select | (self.gain_bits << 9) | single_shot_mode
            | (self.rate_bits << 5) | comparator_off,
        )
        # Wait out the conversion before reading. At low sample rates a fixed
        # short wait returned the previous channel's result instead.
        self._sleep_ms(1000 // self.samples_per_second + 1)
        self._wait_until_ready()

        raw = self._read(self.CONVERSION_REGISTER)
        if raw & 0x8000:
            raw -= 1 << 16
        return raw * self.full_scale_volts / 32768

    def _wait_until_ready(self):
        # Bit 15 of the config register reads 1 once the conversion is done.
        for _ in range(self.READY_POLLS):
            try:
                if self._read(self.CONFIG_REGISTER) & 0x8000:
                    return
            except OSError:
                return
            self._sleep_ms(1)

    def _write(self, register, value):
        self.i2c.writeto(self.address, bytes([register, (value >> 8) & 0xFF, value & 0xFF]))

    def _read(self, register):
        self.i2c.writeto(self.address, bytes([register]))
        data = self.i2c.readfrom(self.address, 2)
        return (data[0] << 8) | data[1]

    def _sleep_ms(self, milliseconds):
        if hasattr(time, "sleep_ms"):
            time.sleep_ms(milliseconds)
        else:
            time.sleep(milliseconds / 1000)


if __name__ == "__main__":
    # Bench check from the REPL: print the probes once a second.
    while True:
        print(read_probes())
        time.sleep(1)
