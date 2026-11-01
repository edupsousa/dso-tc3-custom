#!/usr/bin/env python3
"""Check that the driver's wire constants (fnirsi-dso-tc3/protocol.h) equal the firmware's (firmware_ext/include/protocol.h).

usage: sigrok/tests/check_protocol.py     (no build needed; run.sh runs it first)
The driver is built from sigrok/ alone (a Nix path input), so it cannot include the firmware header; this keeps the copies honest.
"""
import os, sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "..", "common"))
from cheader import values
DRV = os.path.join(here, "..", "fnirsi-dso-tc3", "protocol.h")
FW = os.path.join(here, "..", "..", "firmware_ext", "include", "protocol.h")

# driver name -> firmware name
PAIRS = {
    "TC3_CMD_READ_MEM": "CMD_READ_MEM", "TC3_CMD_FRAME": "CMD_FRAME", "TC3_CMD_UI": "CMD_UI", "TC3_CMD_ROLL": "CMD_ROLL",
    "TC3_CMD_GEN": "CMD_GEN", "TC3_CMD_INFO": "CMD_EXT_INFO",
    "TC3_RESP_READ_MEM": "RESP_READ_MEM", "TC3_RESP_ERROR": "RESP_ERROR",
    "TC3_ERR_BAD_CHUNK": "ERR_BAD_CHUNK", "TC3_ERR_NO_FRAME": "ERR_NO_FRAME", "TC3_ERR_EMPTY": "ERR_EMPTY",
    "TC3_HEADER": "UI_POLL",
    "TC3_ACTION_DONE": "UI_DONE",
    "TC3_UI_VDIV": "UI_VDIV", "TC3_UI_PROBE": "UI_PROBE", "TC3_UI_TDIV": "UI_TDIV", "TC3_UI_COUPLING": "UI_COUPLING",
    "TC3_UI_TRIGLEVEL": "UI_TRIGGER", "TC3_UI_EDGE": "UI_EDGE", "TC3_UI_TRIGMODE": "UI_TRIGMODE",
    "TC3_GEN_GET": "GEN_GET", "TC3_GEN_SET": "GEN_SET", "TC3_GEN_FREQ_MAX": "GEN_FREQ_MAX",
    "TC3_NUM_WAVES": ("GEN_DC", 1),             # waves are numbered 0..GEN_DC
}


drv, fw = values(DRV), values(FW)
bad = 0
for d, f in PAIRS.items():
    f, plus = (f, 0) if isinstance(f, str) else f
    ok = d in drv and f in fw and drv[d] == fw[f] + plus
    print(("ok   " if ok else "FAIL ") + f"{d} = {drv.get(d)}  ({f} = {fw.get(f)}{'+%d' % plus if plus else ''})")
    bad += not ok
sys.exit(1 if bad else 0)
