"""Raw ADC value to volts: the device conversion plus the measured
correction per V/div index and probe setting (host/scope_cal.json; TC3_CAL=0 turns the correction off)."""
import json
import os
from functools import lru_cache

PROBE_MULT = (1, 10)
GAIN_BY_VDIV = [1, 1, 2, 4, 5, 48, 48, 96, 240, 480]
_PKG_CAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scope_cal.json")
_TREE_CAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scope_cal.json")
_CAL_FILE = _PKG_CAL if os.path.exists(_PKG_CAL) else _TREE_CAL
_CAL = None


def raw_to_volts(raw, gain, probe_idx):
    return ((raw * 2.5 / 4095.0) - 1.25) * gain / 7.843137 * PROBE_MULT[probe_idx]


def _load_cal():
    global _CAL
    if _CAL is None:
        _CAL = {}
        if os.environ.get("TC3_CAL", "1") != "0" and os.path.exists(_CAL_FILE):
            try:
                d = json.load(open(_CAL_FILE))
                # probe selector 1 = X10 ("x10" section), 0 = X1 ("x1" section)
                _CAL = {p: {int(k): (v["k"], v["b"]) for k, v in d[name]["ranges"].items()}
                        for p, name in ((1, "x10"), (0, "x1")) if name in d}
            except (OSError, ValueError, KeyError):
                _CAL = {}
    return _CAL


def cal_volts(volts, vdiv, probe_idx):
    """true = (scope - b) / k for the measured V/div indices of the probe setting; unchanged otherwise (also when volts is None)."""
    rng = _load_cal().get(probe_idx)
    if volts is None or not rng or vdiv not in rng:
        return volts
    k, b = rng[vdiv]
    return (volts - b) / k


@lru_cache(maxsize=None)
def scale(gain, probe_idx, vdiv):
    """(a, b) with volts = raw * a + b, the stock conversion and the correction folded together."""
    a = 2.5 / 4095.0 * gain / 7.843137 * PROBE_MULT[probe_idx]
    b = -1.25 * gain / 7.843137 * PROBE_MULT[probe_idx]
    return cal_volts(a + b, vdiv, probe_idx) - cal_volts(b, vdiv, probe_idx), cal_volts(b, vdiv, probe_idx)


def to_volts(samples, gain, probe_idx, vdiv):
    a, b = scale(gain, probe_idx, vdiv)
    return [s * a + b for s in samples]


def roll_volts(raw, vdiv, probe):
    if vdiv >= len(GAIN_BY_VDIV) or probe > 1:
        return None
    a, b = scale(GAIN_BY_VDIV[vdiv], probe, vdiv)
    return raw * a + b
