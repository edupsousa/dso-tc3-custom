"""Signal generator (0x58): the stock generator settings, applied from the main loop."""
import struct

from .link import deferred, UiError
from .wire import *

_GET = bytes([CMD_GEN, GEN_GET])


def gen_get(ser):
    """Current generator settings: dict(wave, freq, duty, amp)."""
    ser.reset_input_buffer()
    ser.write(_GET)
    r = ser.read(14)
    if len(r) != 14 or r[0] != CMD_GEN:
        raise UiError("device did not answer the generator query (firmware without 0x58?)")
    return {"wave": r[4], "freq": struct.unpack_from("<I", r, 5)[0], "duty": r[9], "amp": struct.unpack_from("<f", r, 10)[0]}


def gen_set(ser, wave, freq, duty, amp, timeout=3.0):
    """Queue new generator settings and wait until the main loop applied them. Raises UiError."""
    deferred(ser, struct.pack("<BBBIBf", CMD_GEN, GEN_SET, wave, freq, duty, amp), CMD_GEN, _GET, 14,
             timeout=timeout, what="generator request")


def gen_log(ser, n=8):
    """Log of the stock generator apply calls, newest first: (total, caller_lr, wave, duty, freq)."""
    out = []
    for i in range(n):
        ser.reset_input_buffer()
        ser.write(bytes([CMD_GEN, GEN_LOG, i]))
        r = ser.read(17)
        if len(r) != 17 or r[0] != CMD_GEN:
            break
        total, lr, wave, duty, freq = struct.unpack_from("<IIBBI", r, 3)
        out.append((total, lr, wave, duty, freq))
    return out
