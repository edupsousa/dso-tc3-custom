"""Screen capture (0x56 flush hook + 0x42 reads)."""
import struct
import time
import zlib

from .link import UiError
from .memory import read_mem
from .wire import *


def rgb565_to_rgb(buf):
    out = bytearray()
    for v in struct.unpack(f"<{len(buf) // 2}H", buf):
        r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
        out += bytes(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)))
    return bytes(out)


def write_png(path, w, h, rgb):
    raw = b"".join(b"\x00" + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def screen_ctl(ser, op, arg=0, reply=3):
    ser.reset_input_buffer()
    ser.write(bytes([CMD_SCREEN, op, arg]))
    r = ser.read(reply)
    if len(r) != reply or r[0] != CMD_SCREEN:
        raise UiError("no answer to the screen command (firmware without 0x56?)")
    return r


def capture_screen(ser, timeout=30.0, full=True):
    """Arm the flush hook, force a full redraw, collect every strip. Returns (w*h*3 RGB bytes, strips)."""
    img = bytearray(SCR_W * SCR_H * 3)
    rows_done = set()
    screen_ctl(ser, 1, 1 if full else 0)
    t0 = time.time(); last_seq = None; strips = []
    try:
        while time.time() - t0 < timeout and len(rows_done) < SCR_H:
            r = screen_ctl(ser, 2, reply=21)
            ready, armed, seq = r[2], r[3], struct.unpack_from("<I", r, 4)[0]
            x1, y1, x2, y2 = struct.unpack_from("<hhhh", r, 8)
            px = struct.unpack_from("<I", r, 16)[0]
            if not armed and r[20]:
                raise UiError("device timed out waiting for the host and disarmed capture")
            if not ready or seq == last_seq:
                time.sleep(0.01)
                continue
            w, h = x2 - x1 + 1, y2 - y1 + 1
            if w <= 0 or h <= 0 or w > SCR_W or h > SCR_H or not (0x20000000 <= px < 0x20018000):
                raise UiError(f"implausible strip area ({x1},{y1})-({x2},{y2}) px=0x{px:08x}")
            rgb = rgb565_to_rgb(read_mem(ser, px, w * h * 2))
            for row in range(h):
                o = ((y1 + row) * SCR_W + x1) * 3
                img[o:o + w * 3] = rgb[row * w * 3:(row + 1) * w * 3]
                rows_done.add(y1 + row)
            strips.append((x1, y1, x2, y2))
            last_seq = seq
            screen_ctl(ser, 3)
    finally:
        screen_ctl(ser, 0)
    return bytes(img), strips
