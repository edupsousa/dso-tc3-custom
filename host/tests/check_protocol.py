#!/usr/bin/env python3
"""Check that the host library's wire constants (host/tc3/wire.py) equal the firmware's (firmware_ext/include/protocol.h).
usage: host/tests/check_protocol.py"""
import os, sys
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, ".."))
sys.path.insert(0, os.path.join(here, "..", "..", "common"))
from cheader import values
from testkit import check, finish
from tc3 import wire as W

fw = values(os.path.join(here, "..", "..", "firmware_ext", "include", "protocol.h"))

# wire.py name -> firmware name
SCALARS = {n: n for n in ("CMD_READ_MEM", "CMD_SNAP", "CMD_GET_PARAM", "CMD_SET_PARAM", "CMD_FRAME", "CMD_UI", "CMD_SCREEN", "CMD_ROLL",
                          "CMD_GEN", "CMD_CFG", "CMD_EXT_INFO", "RESP_READ_MEM", "RESP_ERROR", "ERR_BAD_CHUNK", "ERR_NO_FRAME", "ERR_EMPTY",
                          "GEN_GET", "GEN_SET", "GEN_LOG", "UI_KEY")}
SCALARS.update({"HEADER": "UI_POLL", "ACTION_DONE": "UI_DONE"})
for mine, theirs in SCALARS.items():
    check(f"{mine} = {getattr(W, mine)}", getattr(W, mine) == fw.get(theirs), f"({theirs} = {fw.get(theirs)})")

# tables: key -> firmware name
def table(label, mine, names):
    for k, fwname in names.items():
        check(f"{label}[{k}] = {mine[k]}", mine[k] == fw.get(fwname), f"({fwname} = {fw.get(fwname)})")

table("UI_FIELDS", W.UI_FIELDS, {"position": "UI_POSITION", "vdiv": "UI_VDIV", "probe": "UI_PROBE", "tdiv": "UI_TDIV", "coupling": "UI_COUPLING",
                                 "trigger": "UI_TRIGGER", "edge": "UI_EDGE", "trigmode": "UI_TRIGMODE", "runstop": "UI_RUNSTOP"})
table("PARAMS", W.PARAMS, {"mode": "PARAM_MODE", "vdiv": "PARAM_VDIV", "probe": "PARAM_PROBE", "tdiv": "PARAM_TDIV",
                           "coupling": "PARAM_COUPLING", "level": "PARAM_TRIGGER_LEVEL", "run_stop": "PARAM_RUN_STOP"})
table("GEN_WAVES", W.GEN_WAVES, {"sine": "GEN_SINE", "square": "GEN_SQUARE", "pulse": "GEN_PULSE", "triangle": "GEN_TRIANGLE",
                                 "ramp": "GEN_RAMP", "dc": "GEN_DC"})
table("KEYS", W.KEYS, {"hold-select": "UI_KEY_HOLD_SELECT"})
for code, fwname in {6: "UI_ERR_BUSY", 7: "UI_ERR_FIELD", 8: "UI_ERR_NOT_SCOPE", 9: "UI_ERR_RANGE", 10: "UI_ERR_KEY_BUSY"}.items():
    check(f"UI_RESULTS has {fwname} = {code}", code in W.UI_RESULTS and fw.get(fwname) == code)
check("GEN_FREQ_RANGE max", W.GEN_FREQ_RANGE[1] == fw.get("GEN_FREQ_MAX"), str(fw.get("GEN_FREQ_MAX")))
finish()
