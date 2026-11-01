#!/usr/bin/env python3
"""Offline test of the host side of roll mode (0x57): tc3 helpers, the SCPI
roll path and the sigrok export, against a fake serial device that follows the
firmware's ring semantics (firmware_ext/src/roll.c, handle_roll).
usage: host/tests/test_roll_host.py
"""
import os, struct, sys, tempfile, zipfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ["TC3_CAL"] = "0"   # these tests check the stock conversion; host/tests/test_cal.py checks the correction
import tc3 as T
import tc3scpi as S
import tc3_sr as SR

RING = 1024


class FakeRoll:
    """Serial-like object; `push` plays the ADC ISR."""
    def __init__(self, tdiv=15, vdiv=3, probe=1, rate_ks=0.25):
        self.count = self.base = self.epoch = 0
        self.ring = [0] * RING
        self.tdiv, self.vdiv, self.probe, self.rate = tdiv, vdiv, probe, rate_ks
        self.out = b""
        self.on_header = None             # hook run before each header reply

    def push(self, raw, vdiv=None):
        self.ring[self.count % RING] = raw | ((self.vdiv if vdiv is None else vdiv) << 12)
        self.count += 1

    def set_tdiv(self, tdiv):
        self.tdiv, self.base, self.epoch = tdiv, self.count, self.epoch + 1

    def reset_input_buffer(self): self.out = b""

    def write(self, data):
        cmd, op = data[0], data[1]
        assert cmd == T.CMD_ROLL
        frm = struct.unpack_from("<I", data, 2)[0]
        if op == 0xFF:
            if self.on_header:
                self.on_header(self)
            self.out = (bytes([0x57, 0xFF, 0]) + struct.pack("<II", self.count, self.base)
                        + bytes([self.epoch & 255, self.tdiv, self.vdiv, self.probe, 3])
                        + struct.pack("<if", 4, self.rate) + struct.pack("<H", RING))
        elif op == 1:
            if frm - self.base < 0 or self.count - frm > RING - 2:
                self.out = bytes([0x7E, 0x57, 4])
            else:
                n = max(0, min(29, self.count - frm))
                self.out = bytes([0x57, 1, n]) + b"".join(struct.pack("<H", self.ring[(frm + i) % RING]) for i in range(n))
        else:
            self.out = bytes([0x7E, 0x57, 2])

    def read(self, n):
        r, self.out = self.out[:n], self.out[n:]
        return r


import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "common"))
from testkit import check, finish

def volts(raw, vdiv, probe=1):
    return ((raw * 2.5 / 4095.0) - 1.25) * T.GAIN_BY_VDIV[vdiv] / 7.843137 * T.PROBE_MULT[probe]

# ---- client helpers
f = FakeRoll()
for i in range(700): f.push(1000 + i % 500, vdiv=3 if i < 650 else 4)
hdr, v = T.roll_last(f, 300)
check("roll_last returns the newest 300 samples", len(v) == 300 and hdr["count"] == 700)
exp = [volts(1000 + i % 500, 3 if i < 650 else 4) for i in range(400, 700)]
check("per-sample gain follows the V/div index", all(abs(a - b) < 1e-9 for a, b in zip(v, exp)))
f2 = FakeRoll(); [f2.push(2000) for _ in range(10)]
check("roll_last returns fewer when the epoch is young", len(T.roll_last(f2, 300)[1]) == 10)
f3 = FakeRoll(tdiv=7)
try: T.roll_last(f3); ok = False
except T.RollError: ok = True
check("roll_last refuses a burst timebase", ok)
f4 = FakeRoll(); f4.set_tdiv(16)
try: T.roll_last(f4); ok = False
except T.RollError as e: ok = "no samples" in str(e)
check("roll_last with an empty epoch reports it", ok)

hdr, vs, raws = T.roll_collect(f, count=100)
check("roll_collect count=100 starts at the oldest history sample", len(vs) == 100 and raws[0][0] == 1000 and raws[99][0] == 1099)
f5 = FakeRoll()
for i in range(50): f5.push(2048 + i)
def feeder(dev):                       # the 'ISR' adds samples while the host waits
    for _ in range(10): dev.push(3000)
f5.on_header = feeder
hdr, vs, raws = T.roll_collect(f5, count=200)
check("roll_collect waits for new samples, gap-free", len(raws) == 200 and [r for r, _ in raws[:50]] == [2048 + i for i in range(50)])
f6 = FakeRoll(); [f6.push(2048) for _ in range(20)]
def change(dev):
    dev.set_tdiv(17)
f6.on_header = lambda d: (setattr(f6, "on_header", change))
try: T.roll_collect(f6, count=500); ok = False
except T.RollError as e: ok = "changed" in str(e)
check("roll_collect aborts when T/div changes", ok)
f7 = FakeRoll(); [f7.push(2048) for _ in range(100)]
f7.on_header = lambda d: [d.push(1) for _ in range(1100)]
try: T.roll_collect(f7, count=5000); ok = False
except T.RollError as e: ok = "lost" in str(e)
check("roll_collect reports lost samples", ok)

# a sample arriving between the header and the chunk reads must not enlarge the window
fo = FakeRoll(); [fo.push(2048) for _ in range(500)]
orig_write = fo.write
def late(data, _w=orig_write, _st={"n": 0}):
    _w(data)
    if data[1] == 0xFF and _st["n"] == 0:
        _st["n"] = 1; fo.push(2100)                    # arrives right after the first header reply
fo.write = late
check("roll_last stays at 300 when a sample arrives mid-read", len(T.roll_last(fo, 300)[1]) == 300)

# ---- SCPI
class FakeDevice(S.Device):
    def __init__(self, fake, params):
        self.ser, self.lock, self.params = fake, __import__("threading").Lock(), params
    def get(self, name): return self.params[name]

fr = FakeRoll(); [fr.push(2048 + i) for i in range(400)]
sc = S.Scpi(FakeDevice(fr, {"tdiv": 15, "vdiv": 3, "probe": 1}))
d = sc.handle("WAV:DATA?").decode().strip().split(",")
check("SCPI WAV:DATA? in roll mode returns the 300-sample window", len(d) == 300 and abs(float(d[-1]) - volts(2048 + 399, 3)) < 1e-4, f"n={len(d)}")
check("SCPI WAV:XINC? is 1/rate", abs(float(sc.handle("WAV:XINC?")) - 0.004) < 1e-9)
check("SCPI ACQ:SRAT? is the roll rate", abs(float(sc.handle("ACQ:SRAT?")) - 250.0) < 1e-6)
pre = sc.handle("WAV:PRE?").decode().strip().split(",")
check("SCPI WAV:PRE? points and x-increment", pre[2] == "300" and abs(float(pre[4]) - 0.004) < 1e-9, str(pre))
mx = float(sc.handle("MEAS:VMAX?"))
check("SCPI MEAS:VMAX? over the window", abs(mx - volts(2048 + 399, 3)) < 1e-4)
sc.handle("WAV:FORM FLO")
r = sc.handle("WAV:DATA?")
check("SCPI float block in roll mode", r.startswith(b"#4") and len(r) == 6 + 1200 + 1, str(r[:8]))
fe = FakeRoll(); sc2 = S.Scpi(S.Device.__new__(FakeDevice)); sc2.dev.ser, sc2.dev.lock, sc2.dev.params = fe, __import__("threading").Lock(), {"tdiv": 15, "vdiv": 3, "probe": 1}
sc2.handle("WAV:DATA?")
check("SCPI roll window with no samples -> error -230", sc2.handle("SYST:ERR?").decode().startswith("-230"))

# ---- sigrok
check("rate 250 -> 250 Hz x1", SR.sigrok_rate(250.0) == (250, 1))
check("rate 125.217 -> 125 Hz x1 (0.17% off)", SR.sigrok_rate(14400 / 115) == (125, 1))
check("rate 12.5 -> 25 Hz x2", SR.sigrok_rate(12.5) == (25, 2))
check("rate 2.5 -> 5 Hz x2", SR.sigrok_rate(2.5) == (5, 2))
check("rate 5 -> 5 Hz x1", SR.sigrok_rate(5.0) == (5, 1))
with tempfile.TemporaryDirectory() as td:
    import types
    fk = FakeRoll(tdiv=21, rate_ks=0.0025); [fk.push(2048 + i) for i in range(30)]
    T.open_conn = lambda port=None, timeout=1.0: fk
    SR.roll(types.SimpleNamespace(port=None, out=os.path.join(td, "r.sr"), samples=30, time=None))
    z = zipfile.ZipFile(os.path.join(td, "r.sr"))
    meta = z.read("metadata").decode()
    n = len(z.read("analog-1-1-1")) // 4
    check("roll .sr: 30 samples doubled at 5 Hz", "samplerate=5 Hz" in meta and n == 60, f"n={n}")
finish()
