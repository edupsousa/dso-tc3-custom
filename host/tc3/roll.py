"""Roll-mode stream (0x57), T/div 15..21: the device records every ADC sample (raw 12 bits | V/div index << 12) in a ring with an
absolute sample counter; we read by index, so nothing is lost while we keep up and gaps are counted exactly when we do not."""
import struct
import time

from .link import request, RollError
from .volts import roll_volts
from .wire import *

RING_MARGIN = 4      # the newest samples may be overwritten while we read


def _request(ser, op, frm, size):
    return request(ser, struct.pack("<BBI", CMD_ROLL, op, frm), CMD_ROLL, size, ROLL_ERRORS, RollError)


def roll_header(ser):
    r = _request(ser, HEADER, 0, 26)
    if r[1] != HEADER:
        raise RollError(f"bad header {r.hex()}")
    return {
        "count": struct.unpack_from("<I", r, 3)[0], "base": struct.unpack_from("<I", r, 7)[0],
        "epoch": r[11], "tdiv": r[12], "vdiv": r[13], "probe": r[14], "mode": r[15],
        "gain": struct.unpack_from("<i", r, 16)[0], "rate": struct.unpack_from("<f", r, 20)[0],
        "ring": struct.unpack_from("<H", r, 24)[0],
    }


def roll_read(ser, frm):
    """Up to 29 samples starting at absolute index `frm`: list of (raw12, vdiv)."""
    head = _request(ser, ROLL_OP_READ, frm, 3)
    if head[1] != ROLL_OP_READ:
        raise RollError(f"bad chunk header {head.hex()}")
    n = head[2]
    body = ser.read(2 * n)
    if len(body) != 2 * n:
        raise RollError("short chunk")
    return [(v & 0xFFF, v >> 12) for v in struct.unpack(f"<{n}H", body)]


def _need_roll(hdr):
    if hdr["tdiv"] < ROLL_MIN_TDIV:
        raise RollError(f"T/div index {hdr['tdiv']} is not a roll timebase (15..21)")


def roll_last(ser, n=300):
    """Header and volts of the newest up to `n` samples of the current T/div (a roll-mode 'screen')."""
    for _ in range(4):
        hdr = roll_header(ser)
        _need_roll(hdr)
        frm = max(hdr["base"], hdr["count"] - n, hdr["count"] - (hdr["ring"] - RING_MARGIN))
        raws = []
        try:
            while frm + len(raws) < hdr["count"]:
                chunk = roll_read(ser, frm + len(raws))
                if not chunk:
                    break
                raws += chunk
        except RollError as e:
            if e.code == ERR_NO_FRAME:
                continue
            raise
        if roll_header(ser)["epoch"] != hdr["epoch"]:
            continue                                   # T/div changed while reading
        raws = raws[:hdr["count"] - frm]               # a chunk may include samples that arrived after the header
        if not raws:
            raise RollError("no samples recorded yet at this T/div")
        volts = [roll_volts(r, v, hdr["probe"]) for r, v in raws]
        if None in volts:
            raise RollError("no voltage conversion (V/div index or probe out of range)")
        return hdr, volts
    raise RollError("could not read a stable window")


def roll_collect(ser, count=None, seconds=None, history=True):
    """Read a gap-free run of roll samples at one T/div. Starts from the ring history (the samples
    already on the 'screen') when `history`, else from the next new sample. Stops after `count`
    samples or `seconds`. Returns (hdr, volts, raws) with hdr from the start. Raises RollError if
    T/div or the probe changes, or if samples are lost."""
    hdr = roll_header(ser)
    _need_roll(hdr)
    nxt = max(hdr["base"], hdr["count"] - (hdr["ring"] - RING_MARGIN)) if history else hdr["count"]
    volts, raws = [], []
    t0 = time.time()
    while True:
        cur = roll_header(ser)
        if cur["epoch"] != hdr["epoch"] or cur["tdiv"] != hdr["tdiv"] or cur["probe"] != hdr["probe"]:
            raise RollError("T/div or probe changed during the recording")
        if cur["count"] - nxt > cur["ring"] - RING_MARGIN:
            raise RollError("fell behind the sample ring (samples lost)")
        while nxt < cur["count"] and (count is None or len(raws) < count):
            chunk = roll_read(ser, nxt)
            if not chunk:
                break
            if count is not None:
                chunk = chunk[:count - len(raws)]
            for r, v in chunk:
                x = roll_volts(r, v, hdr["probe"])
                if x is None:
                    raise RollError("no voltage conversion (V/div index or probe out of range)")
                raws.append((r, v))
                volts.append(x)
            nxt += len(chunk)
        if (count is not None and len(raws) >= count) or (seconds and time.time() - t0 >= seconds):
            return hdr, volts, raws
        if count is None and not seconds:
            return hdr, volts, raws                    # nothing to wait for: return the history
        time.sleep(0.05)
