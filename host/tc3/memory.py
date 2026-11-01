"""Device memory through the 0x42 gate stub."""
import struct

from .link import request, Tc3Error
from .wire import *


def read_mem(ser, addr, length):
    """Read device memory (max 60 bytes per request)."""
    out = bytearray()
    while len(out) < length:
        n = min(READ_MEM_MAX, length - len(out))
        a = addr + len(out)
        try:
            out += request(ser, struct.pack("<BIH", CMD_READ_MEM, a, n), RESP_READ_MEM, n + 1, {})[1:]
        except Tc3Error as e:
            raise Tc3Error(f"reading 0x{a:08x}: {e}", e.code)
    return bytes(out)


def read_gain(ser):
    return struct.unpack("<i", read_mem(ser, GAIN_ADDR, 4))[0]
