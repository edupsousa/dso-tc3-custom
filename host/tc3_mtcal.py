#!/usr/bin/env python3
"""Show or write the M-Tester calibration values (0x59).

usage: host/tc3_mtcal.py                       show the values in RAM
       host/tc3_mtcal.py --from-dump FILE      write the values of a config-sector dump to RAM
       host/tc3_mtcal.py --from-dump FILE --save   ... and to flash (erases and rewrites the config sector)
Without --save only RAM changes; a power cycle brings the flash values back."""
import argparse, os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tc3.cfg import cfg_read, cfg_write, SLOT_NAMES, _OFFSETS
from tc3.link import open_conn, UiError


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("-p", "--port")
    ap.add_argument("--from-dump", help="config-sector dump to take the seven values from")
    ap.add_argument("--save", action="store_true", help="also write them to flash")
    a = ap.parse_args(argv)
    ser = open_conn(a.port)
    cur = cfg_read(ser)
    print("RAM now:  ", "  ".join(f"{n}={v:.5g}" for n, v in zip(SLOT_NAMES, cur)))
    if a.from_dump:
        d = open(a.from_dump, "rb").read()
        new = [struct.unpack_from("<f", d, o)[0] for o in _OFFSETS]
        print("dump:     ", "  ".join(f"{n}={v:.5g}" for n, v in zip(SLOT_NAMES, new)))
        try:
            cfg_write(ser, new, save=a.save)
        except UiError as e:
            sys.exit(f"write failed: {e}")
        print("written to RAM" + (" and flash" if a.save else ""))
        print("RAM after:", "  ".join(f"{n}={v:.5g}" for n, v in zip(SLOT_NAMES, cfg_read(ser))))


if __name__ == "__main__":
    main()
