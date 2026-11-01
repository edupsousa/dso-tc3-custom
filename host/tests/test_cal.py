#!/usr/bin/env python3
"""Offline test of the volts correction (tc3.cal_volts, host/scope_cal.json) and of tc3_cal.fit. usage: host/tests/test_cal.py"""
import json, os, subprocess, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import tc3 as T
import tc3_cal as C

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "common"))
from testkit import check, finish

cal = json.load(open(os.path.join(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."), "scope_cal.json")))
check("scope_cal.json x10: indices 0..6 with plausible gain (0.9..1.05) and offset (|b| < 0.1 V)",
      sorted(map(int, cal["x10"]["ranges"])) == list(range(7)) and
      all(0.9 < v["k"] < 1.05 and abs(v["b"]) < 0.1 for v in cal["x10"]["ranges"].values()))
check("scope_cal.json x1: indices 3..9 with plausible gain (0.95..1.05) and offset (|b| < 0.5 V)",
      sorted(map(int, cal["x1"]["ranges"])) == list(range(3, 10)) and
      all(0.95 < v["k"] < 1.05 and abs(v["b"]) < 0.5 for v in cal["x1"]["ranges"].values()))
check("X1 correction inverts too (index 6)", abs(T.cal_volts(cal["x1"]["ranges"]["6"]["k"] * 2.0 + cal["x1"]["ranges"]["6"]["b"], 6, 0) - 2.0) < 1e-9)
k, b = cal["x10"]["ranges"]["3"]["k"], cal["x10"]["ranges"]["3"]["b"]
check("cal_volts inverts scope = k*true + b at index 3", abs(T.cal_volts(k * 2.0 + b, 3, 1) - 2.0) < 1e-9)
check("the two multimeter points land within 6 mV (2.845 -> 2.968, 0.9205 -> 0.984)",
      abs(T.cal_volts(2.845, 3, 1) - 2.968) < 0.006 and abs(T.cal_volts(0.9205, 3, 1) - 0.984) < 0.006,
      (T.cal_volts(2.845, 3, 1), T.cal_volts(0.9205, 3, 1)))
check("X1 index 1 (not measured) is left alone", T.cal_volts(1.234, 1, 0) == 1.234)
check("unmeasured index 8 is left alone", T.cal_volts(1.234, 8, 1) == 1.234)
check("None passes through", T.cal_volts(None, 3, 1) is None)
check("roll_volts applies it (raw 3000 at index 3, X10)",
      abs(T.roll_volts(3000, 3, 1) - T.cal_volts(T.raw_to_volts(3000, T.GAIN_BY_VDIV[3], 1), 3, 1)) < 1e-12 and
      T.roll_volts(3000, 3, 1) != T.raw_to_volts(3000, T.GAIN_BY_VDIV[3], 1))
hdr = {"tdiv": 8, "probe": 1, "gain": 4, "vdiv": 3}
check("frame_volts applies it", abs(T.frame_volts(hdr, [3000])[0] - T.roll_volts(3000, 3, 1)) < 1e-12)
out = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import tc3 as T; print(T.cal_volts(2.845, 3, 1))" % os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")],
                     env=dict(os.environ, TC3_CAL="0"), capture_output=True, text=True)
check("TC3_CAL=0 disables it", out.stdout.strip() == "2.845", out.stdout.strip() or out.stderr[-120:])
kk, bb, res = C.fit([(0.0, -0.03), (1.0, 0.94), (2.0, 1.91), (3.0, 2.88)])
check("fit recovers a straight line (k 0.97, b -0.03)", abs(kk - 0.97) < 1e-9 and abs(bb + 0.03) < 1e-9 and res < 1e-9, (kk, bb, res))
check("parse_indices", C.parse_indices("0-2,5", C.LEVELS_X10) == [0, 1, 2, 5] and C.parse_indices("9", C.LEVELS_X10) == [] and C.parse_indices("2-9", C.LEVELS_X1) == list(range(3, 10)))
finish()
