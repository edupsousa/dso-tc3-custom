"""Burst-frame ring (0x54): 600-sample acquisitions delivered in 20 chunks of 30."""
import struct

from .link import request, FrameError
from .memory import read_gain
from .volts import to_volts
from .wire import *

FRAME_SIZE = 62      # cmd, chunk, 30 samples

# T/div 0..2 run both ADCs interleaved at the ADC clock, not from TIM1. The firmware stores the
# literals 10280 / 5140 kS/s; a 1.000 MHz sine measured 72/7 = 10.2857 MS/s (= 144 MHz / 14) at
# T/div 0 and 1 and half that at T/div 2 (2026-10-30), 0.06% above the literals.
INTERLEAVED_RATE_KSPS = {0: 144000.0 / 14, 1: 144000.0 / 14, 2: 144000.0 / 28}


def _request(ser, chunk, seq, size):
    r = request(ser, struct.pack("<BBI", CMD_FRAME, chunk, seq), CMD_FRAME, size, FRAME_ERRORS, FrameError)
    if r[1] != chunk:
        raise FrameError(f"bad reply {r.hex()}")
    return r


def frame_header(ser, seq=0):
    """Header of frame `seq` (0 = latest) as a dict; the rate is corrected for the interleaved timebases."""
    r = _request(ser, HEADER, seq, 24)
    hdr = {
        "seq": struct.unpack_from("<I", r, 3)[0],
        "latest": struct.unpack_from("<I", r, 7)[0],
        "vdiv": r[11], "tdiv": r[12], "probe": r[13], "mode": r[14],
        "gain": struct.unpack_from("<i", r, 15)[0],
        "rate": struct.unpack_from("<f", r, 19)[0],
        "chunks": r[23],
    }
    if hdr["tdiv"] in INTERLEAVED_RATE_KSPS:
        hdr["rate_fw"] = hdr["rate"]
        hdr["rate"] = INTERLEAVED_RATE_KSPS[hdr["tdiv"]]
    return hdr


def fetch_frame(ser, seq=0, hdr=None):
    """(header, samples) of a ring frame; pass the header when the caller has just read it. Every chunk request names the
    frame, so a slot overwritten meanwhile fails the request (FrameError): no second header read is needed."""
    if hdr is None:
        hdr = frame_header(ser, seq)
    samples = []
    for ch in range(hdr["chunks"]):
        samples.extend(struct.unpack_from("<30H", _request(ser, ch, hdr["seq"], FRAME_SIZE), 2))
    return hdr, samples


def frame_volts(hdr, samples):
    if hdr["tdiv"] >= ROLL_MIN_TDIV or hdr["probe"] > 1:
        return None
    return to_volts(samples, hdr["gain"], hdr["probe"], hdr["vdiv"])
