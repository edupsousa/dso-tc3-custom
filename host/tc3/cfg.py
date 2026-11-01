"""M-Tester calibration values (0x59): the six stray capacitances and the 5 V scale in the config block."""
import struct

from .link import deferred
from .memory import read_mem
from .wire import *

CFG_SAVE = 1
SLOT_NAMES = ("Probe12", "Probe13", "Probe21", "Probe23", "Probe31", "Probe32", "V5v")
_SS = 0x20000668                                   # g_ScopeState; the config sector mirrors its first 0x90 bytes
_OFFSETS = (0x54, 0x58, 0x5C, 0x60, 0x64, 0x68, 0x78)


def cfg_read(ser):
    """The seven RAM values, in SLOT_NAMES order."""
    blk = read_mem(ser, _SS, 0x7C)
    return [struct.unpack_from("<f", blk, o)[0] for o in _OFFSETS]


def cfg_write(ser, values, save=False, timeout=5.0):
    """Store the seven floats in RAM (and in flash when save is set). Raises UiError."""
    tx = struct.pack("<BB7f", CMD_CFG, CFG_SAVE if save else 0, *values)
    deferred(ser, tx, CMD_CFG, bytes([CMD_CFG, HEADER]), 4, timeout=timeout, what="calibration write")
