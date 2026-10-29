"""Readings saved on flash until the server has them: one JSON reading per line, oldest first."""
import json
import os
import time

import config

# What happened when one reading was sent.
SENT = "sent"
REJECTED = "rejected"  # refused by the server: drop it
FAILED = "failed"  # network or server trouble: keep it for later

COPY_CHUNK_BYTES = 1024  # the file can be larger than free RAM

ticks_ms = getattr(time, "ticks_ms", lambda: int(time.time() * 1000))
ticks_diff = getattr(time, "ticks_diff", lambda end, start: end - start)


def add(reading):
    """Append a reading. Returns False when it could not be saved."""
    try:
        if _size() >= config.OUTBOX_MAX_BYTES:
            print("Outbox is full; dropping the oldest {} readings.".format(config.OUTBOX_BATCH_SIZE))
            _drop_oldest(config.OUTBOX_BATCH_SIZE)
        with open(config.OUTBOX_FILE, "a") as outbox:
            outbox.write(json.dumps(reading))
            outbox.write("\n")
        return True
    except Exception as error:
        print("Could not save the reading to the outbox:", error)
        return False


def send_all(send, budget_ms=None):
    """Send waiting readings oldest-first. True once the outbox is empty.

    Stops at the first FAILED, or once budget_ms has passed. REJECTED readings
    are removed like sent ones so they can't block the rest.
    """
    started = ticks_ms()
    sent = 0
    while True:
        batch = _oldest(config.OUTBOX_BATCH_SIZE)
        if not batch:
            if sent:
                print("Sent {} waiting reading(s).".format(sent))
            return True

        handled = 0
        out_of_time = False
        for line in batch:
            outcome = _send_line(send, line)
            if outcome == FAILED:
                break
            handled += 1
            if outcome == SENT:
                sent += 1
            if budget_ms is not None and ticks_diff(ticks_ms(), started) >= budget_ms:
                out_of_time = True
                break

        if handled:
            _drop_oldest(handled)
        if out_of_time:
            return False
        if handled < len(batch):
            print("Server unreachable after {} sent; the rest stay in the outbox.".format(sent))
            return False


def count():
    """How many readings are waiting (handy from the REPL)."""
    total = 0
    try:
        with open(config.OUTBOX_FILE, "r") as outbox:
            for line in outbox:
                if line.strip():
                    total += 1
    except OSError:
        pass
    return total


def recover():
    """At boot: finish or undo a rewrite a power cut interrupted.

    Copy present without the outbox: the copy is the outbox. Both present:
    the outbox is whole and the copy may be partial.
    """
    if not _exists(_copy_file()):
        return
    if _exists(config.OUTBOX_FILE):
        _remove(_copy_file())
    else:
        os.rename(_copy_file(), config.OUTBOX_FILE)
        print("Recovered the outbox after an interrupted rewrite.")


def _send_line(send, line):
    try:
        reading = json.loads(line)
    except ValueError as error:
        print("Skipping a corrupt outbox line:", error)
        return REJECTED
    if not isinstance(reading, dict):
        return REJECTED
    return send(reading)


def _oldest(limit):
    lines = []
    try:
        with open(config.OUTBOX_FILE, "r") as outbox:
            for line in outbox:
                line = line.strip()
                if line:
                    lines.append(line)
                    if len(lines) >= limit:
                        break
    except OSError:
        return []
    return lines


def _drop_oldest(count):
    """Remove the first `count` readings by copying the rest to a new file."""
    kept = 0
    skipped = 0
    try:
        with open(config.OUTBOX_FILE, "r") as source:
            with open(_copy_file(), "w") as target:
                for line in source:
                    line = line.strip()
                    if not line:
                        continue
                    if skipped < count:
                        skipped += 1
                        continue
                    target.write(line)
                    target.write("\n")
                    kept += 1
    except OSError:
        _remove(_copy_file())
        return

    if kept:
        _replace(_copy_file(), config.OUTBOX_FILE)
    else:
        _remove(_copy_file())
        _remove(config.OUTBOX_FILE)


def _replace(source, destination):
    # FAT can't rename over a file; recover() covers a power cut in between.
    _remove(destination)
    try:
        os.rename(source, destination)
    except OSError:
        with open(source, "rb") as source_file:
            with open(destination, "wb") as destination_file:
                while True:
                    chunk = source_file.read(COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    destination_file.write(chunk)
        _remove(source)


def _copy_file():
    return config.OUTBOX_FILE + ".tmp"


def _size():
    try:
        return os.stat(config.OUTBOX_FILE)[6]
    except OSError:
        return 0


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
