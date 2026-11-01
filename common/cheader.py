"""NAME -> int for the constants of a C header, so a copy of the wire protocol can be checked against firmware_ext/include/protocol.h."""
import re


def values(path):
    """Every `#define NAME value` and every enum member `NAME = value` (several may share a line)."""
    out = {}
    for line in open(path):
        line = re.sub(r"/\*.*?\*/", "", line)
        m = re.match(r"\s*#define\s+(\w+)\s+\(?(-?\w+)\)?", line)
        pairs = [m.groups()] if m else re.findall(r"(\w+)\s*=\s*(0x[0-9a-fA-F]+|\d+)", line)
        for name, val in pairs:
            try:
                out[name] = int(val, 0)
            except ValueError:
                pass
    return out
