"""Legacy parameter get/set (0x51/0x52) and the live DMA buffer snapshot (0x50)."""
import struct

from .link import request, Tc3Error
from .memory import read_gain
from .wire import *

SNAP_CHUNK = 62      # cmd, chunk, 30 samples


def get_param(ser, name):
    pid = PARAMS[name]
    r = request(ser, bytes([CMD_GET_PARAM, pid]), CMD_GET_PARAM, 6, {})
    if r[1] != pid:
        raise Tc3Error(f"bad reply {r.hex()}")
    return struct.unpack_from("<I", r, 2)[0]


def set_param(ser, name, value, ok=(0,)):
    """Returns the status byte; raises Tc3Error unless it is in `ok`."""
    pid = PARAMS[name]
    r = request(ser, struct.pack("<BBI", CMD_SET_PARAM, pid, value), CMD_SET_PARAM, 3, {})
    if r[1] != pid or r[2] not in ok:
        raise Tc3Error(f"device rejected SET {name}={value} (status {r[2]})")
    return r[2]


def snap(ser):
    """The live DMA buffer (may tear): (header dict, samples)."""
    r = request(ser, bytes([CMD_SNAP, HEADER]), CMD_SNAP, 12, {})
    hdr = {"run_stop": r[2], "vdiv": r[3], "tdiv": r[4], "probe": r[5], "edge": r[6],   # byte 5 is the probe selector (0 = X1, 1 = X10)
           "trig_pos": struct.unpack_from("<H", r, 7)[0], "total": struct.unpack_from("<H", r, 9)[0], "chunks": r[11],
           "gain": read_gain(ser)}
    samples = []
    for ch in range(hdr["chunks"]):
        c = request(ser, bytes([CMD_SNAP, ch]), CMD_SNAP, SNAP_CHUNK, {})
        if c[1] != ch:
            raise Tc3Error(f"bad chunk {ch}: {c.hex()}")
        samples.extend(struct.unpack_from("<30H", c, 2))
    return hdr, samples
