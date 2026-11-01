#!/usr/bin/env python3
"""Offline test of the tc3 library against a fake serial device that answers like the firmware: memory reads, legacy params and
snapshot, deferred UI and generator actions, frame ring with an expiring slot, error replies and timeouts.
usage: host/tests/test_client.py"""
import os, struct, sys
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, ".."))
sys.path.insert(0, os.path.join(here, "..", "..", "common"))
os.environ["TC3_CAL"] = "0"
import tc3 as T
from testkit import check, finish


class Fake:
    """Replies to one request at a time; `now` is the reply being read."""
    def __init__(self):
        self.out = b""
        self.mem = bytearray(range(256)) * 4
        self.params = {1: 3, 2: 4, 3: 1, 4: 8, 5: 1, 6: 0, 7: 0}
        self.pending = 0          # UI polls until DONE
        self.result = 0
        self.gen = [1, 1000, 50, 3.0]
        self.latest = 7
        self.expired = set()
        self.requests = []
        self.deaf = False

    def reset_input_buffer(self): self.out = b""
    def read(self, n):
        r, self.out = self.out[:n], self.out[n:]
        return r

    def write(self, d):
        d = bytes(d)
        self.requests.append(d)
        if self.deaf:
            return
        c = d[0]
        if c == T.CMD_READ_MEM:
            a, n = struct.unpack_from("<IH", d, 1)
            a = a & 0x3FF if a >= 0x20000000 else a        # SRAM aliases the small image
            self.out = b"\x43" + (bytes(self.mem[a:a + n]) if a < len(self.mem) else b"")
        elif c == T.CMD_GET_PARAM:
            self.out = bytes([c, d[1]]) + struct.pack("<I", self.params[d[1]])
        elif c == T.CMD_SET_PARAM:
            self.params[d[1]] = struct.unpack_from("<I", d, 2)[0]
            self.out = bytes([c, d[1], 0])
        elif c == T.CMD_SNAP:
            if d[1] == 0xFF:
                self.out = bytes([c, 0xFF, 0, 4, 8, 1, 0]) + struct.pack("<HH", 19, 600) + bytes([20])
            else:
                self.out = bytes([c, d[1]]) + struct.pack("<30H", *[d[1] * 30 + i for i in range(30)])
        elif c == T.CMD_FRAME:
            seq = struct.unpack_from("<I", d, 2)[0] or self.latest
            if self.latest == 0:
                self.out = bytes([0x7E, c, 5])
            elif seq in self.expired:
                self.out = bytes([0x7E, c, 4])
            elif d[1] == 0xFF:
                self.out = bytes([c, 0xFF, 0]) + struct.pack("<II", seq, self.latest) + bytes([4, 8, 1, 3]) \
                    + struct.pack("<if", 5, 50.0) + bytes([20])
            else:
                self.out = bytes([c, d[1]]) + struct.pack("<30H", *[(d[1] * 30 + i) & 0xFFF for i in range(30)])
        elif c in (T.CMD_UI, T.CMD_GEN):
            self._action(d)

    def _action(self, d):
        c = d[0]
        if (c == T.CMD_UI and d[1] != 0xFF) or (c == T.CMD_GEN and d[1] == T.GEN_SET):
            if c == T.CMD_UI and d[1] == 0x33:
                self.out = bytes([c, d[1], 7]); return
            self.pending = 2
            if c == T.CMD_GEN:
                _, _, w, f, du, a = struct.unpack("<BBBIBf", d)
                self.gen = [w, f, du, a]
            self.out = bytes([c, d[1], 0])
        elif c == T.CMD_UI:
            self.pending = max(0, self.pending - 1)
            self.out = bytes([c, 0xFF, 2 if self.pending == 0 else 1, self.result]) + bytes(5)
        else:
            self.pending = max(0, self.pending - 1)
            w, f, du, a = self.gen
            self.out = bytes([c, 0, 2 if self.pending == 0 else 1, 0, w]) + struct.pack("<I", f) + bytes([du]) + struct.pack("<f", a)


fk = Fake()

# --- memory
check("read_mem: 130 bytes in three requests", T.read_mem(fk, 0x10, 130) == bytes(fk.mem[0x10:0x10 + 130]) and sum(r[0] == 0x42 for r in fk.requests) == 3)
try: T.read_mem(fk, 0x10000, 4); check("read_mem: a short reply raises", False)
except T.Tc3Error as e: check("read_mem: a short reply raises Tc3Error and names the address", "0x00010000" in str(e), str(e))
fk.deaf = True
try: T.read_mem(fk, 0, 4); check("read_mem: timeout raises", False)
except T.Tc3Error as e: check("read_mem: timeout has code TIMEOUT", e.code == T.TIMEOUT)
fk.deaf = False

# --- legacy params
check("get_param: mode", T.get_param(fk, "mode") == 3 and T.get_param(fk, "probe") == 1)
T.set_param(fk, "tdiv", 11)
check("set_param: tdiv", fk.params[4] == 11)
check("get_mode", T.get_mode(fk) == 3)
hdr, s = T.snap(fk)
check("snap: header and 20 chunks of samples", hdr["tdiv"] == 8 and hdr["probe"] == 1 and hdr["chunks"] == 20 and len(s) == 600 and s[-1] == 599, str(hdr))

# --- frames
fk.requests.clear()
hdr, s = T.fetch_frame(fk, 0)
check("fetch_frame: latest frame, 600 samples", hdr["seq"] == 7 and hdr["latest"] == 7 and len(s) == 600 and s[31] == 31, str((hdr["seq"], len(s))))
check("fetch_frame: 1 header + 20 chunk requests, no final header", len(fk.requests) == 21, str(len(fk.requests)))
fk.requests.clear()
head = T.frame_header(fk, 0)
T.fetch_frame(fk, hdr=head)
check("fetch_frame with a known header: 20 chunk requests", len(fk.requests) == 21 and sum(r[1] == 0xFF for r in fk.requests) == 1)
check("frame_volts: scaled by the V/div gain", abs(T.frame_volts(hdr, [2048])[0] - T.raw_to_volts(2048, 5, 1)) < 1e-9)
fk.expired = {7}
try: T.fetch_frame(fk, 7); check("fetch_frame: an expired frame raises", False)
except T.FrameError as e: check("fetch_frame: expired frame -> FrameError ERR_NO_FRAME", e.code == T.ERR_NO_FRAME, str(e))
fk.expired, fk.latest = set(), 0
try: T.frame_header(fk, 0); check("frame_header: empty ring raises", False)
except T.FrameError as e: check("frame_header: empty ring -> ERR_EMPTY", e.code == T.ERR_EMPTY)
fk.latest = 7

# --- deferred actions
T.ui_apply(fk, T.UI_FIELDS["tdiv"], 9)
check("ui_apply: acknowledged, polled to DONE", fk.pending == 0)
try: T.ui_apply(fk, 0x33, 1); check("ui_apply: refusal raises", False)
except T.UiError as e: check("ui_apply: ack error text", "unknown field" in str(e), str(e))
fk.result = 9
try: T.ui_apply(fk, 2, 99); check("ui_apply: result byte checked", False)
except T.UiError as e: check("ui_apply: result error text", "out of range" in str(e), str(e))
fk.result = 0
T.gen_set(fk, 2, 500, 25, 1.5)
g = T.gen_get(fk)
check("gen_set / gen_get round trip", (g["wave"], g["freq"], g["duty"], round(g["amp"], 3)) == (2, 500, 25, 1.5), str(g))
fk.deaf = True
try: T.ui_apply(fk, 2, 1); check("ui_apply: no device raises", False)
except T.UiError as e: check("ui_apply: silent device -> UiError", "acknowledge" in str(e), str(e))

# --- volts
check("scale folds the stock conversion", abs(T.to_volts([1000], 5, 1, 4)[0] - T.raw_to_volts(1000, 5, 1)) < 1e-9)
check("roll_volts: unknown V/div -> None", T.roll_volts(100, 12, 1) is None)
finish()
