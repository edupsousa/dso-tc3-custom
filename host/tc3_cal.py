#!/usr/bin/env python3
"""Re-measure host/scope_cal.json: the scope's gain and offset per V/div index, against the generator's DC output.

usage: host/tc3_cal.py [--gen-gain 0.992] [--gen-offset -0.008] [--indices 0-6] [--write] [--out host/scope_cal.json]

Wire the scope probe (X10) to the generator output and put the device in the scope view; nothing else may use the serial
port. For each V/div index the tool sets generator DC levels that fit inside the range, reads the scope's average
(through the volts conversion WITHOUT the stored correction), and fits `scope = k * true + b`, where `true` is the generator's
setting converted with its own line `true = gen_gain * setting + gen_offset` (measure that line with a multimeter at two
levels, e.g. 3.0 V and 1.0 V; the defaults are the values measured on 2026-10-31). Without --write only prints.
The V/div index and the generator are put back afterwards. Takes about 5 minutes.
"""
import argparse, json, os, statistics, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tc3scpi as S

# DC levels that fit inside each range (window = +-4 divisions; the label is the X10 value, or a tenth of it at X1)
LEVELS_X10 = {0: [0.05, 0.15, 0.25, 0.35], 1: [0.1, 0.3, 0.5, 0.7], 2: [0.1, 0.5, 1.0, 1.5, 1.9], 3: [0.1, 0.5, 1.0, 2.0, 3.0, 3.3],
              4: [0.1, 0.5, 1.0, 2.0, 3.0, 3.3], 5: [0.5, 1.0, 2.0, 3.0, 3.3], 6: [1.0, 2.0, 3.0, 3.3]}
LEVELS_X1 = {3: [0.05, 0.15, 0.25, 0.35], 4: [0.1, 0.3, 0.5, 0.7], 5: [0.1, 0.5, 1.0, 1.5, 1.9], 6: [0.1, 0.5, 1.0, 2.0, 3.0, 3.3],
             7: [0.5, 1.0, 2.0, 3.0, 3.3], 8: [0.5, 1.0, 2.0, 3.0, 3.3], 9: [1.0, 2.0, 3.0, 3.3]}


def fit(pts):
    n = len(pts); sx = sum(x for x, _ in pts); sy = sum(y for _, y in pts)
    sxx = sum(x * x for x, _ in pts); sxy = sum(x * y for x, y in pts)
    k = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    b = (sy - k * sx) / n
    return k, b, max(abs(y - (k * x + b)) for x, y in pts)


def parse_indices(text, levels):
    out = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        out += range(int(a), int(b or a) + 1)
    return [i for i in out if i in levels]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--gen-gain", type=float, default=0.992)
    ap.add_argument("--gen-offset", type=float, default=-0.008)
    ap.add_argument("--indices", default="0-9", help="indices to measure (only those with levels for the probe setting are used)")
    ap.add_argument("--write", action="store_true", help="write the result to --out")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "scope_cal.json"))
    a = ap.parse_args(argv)
    os.environ["TC3_CAL"] = "0"          # measure the raw conversion (read lazily by tc3.volts on first use)
    dev = S.Device(); sc = S.Scpi(dev)

    def q(c):
        r = sc.handle(c + "\n")
        return r.decode().strip() if r else ""
    if dev.get("mode") != 3:
        sys.exit("the scope view is required (tc3_client.py goto scope)")
    probe = dev.get("probe")              # the scope's probe selector: 1 = X10, 0 = X1
    levels, section = (LEVELS_X10, "x10") if probe == 1 else (LEVELS_X1, "x1")
    print(f"probe selector {'X10' if probe == 1 else 'X1'}: the physical probe switch must match")
    orig = dev.get("vdiv"); res = {}
    try:
        q("SOUR:FUNC DC")
        for idx in parse_indices(a.indices, levels):
            dev.ui("vdiv", idx); time.sleep(1.0)
            pts = []
            for lvl in levels[idx]:
                q(f"SOUR:VOLT {lvl}"); time.sleep(0.9)
                pts.append((a.gen_gain * lvl + a.gen_offset, statistics.mean(float(q("MEAS:VAV?")) for _ in range(4))))
            k, b, r = fit(pts)
            res[idx] = {"k": round(k, 4), "b": round(b, 4)}
            print(f"V/div index {idx}: scope = {k:.4f} * true {b:+.4f} V (max residual {r * 1000:.1f} mV, {len(pts)} points)", flush=True)
    finally:
        dev.ui("vdiv", orig)
        q("SOUR:FUNC SIN"); q("SOUR:FREQ 1000"); q("SOUR:VOLT 3.0")
    if a.write and res:
        try:
            d = json.load(open(a.out))
        except (OSError, ValueError):
            d = {}
        d.setdefault(section, {}).setdefault("ranges", {}).update({str(i): v for i, v in res.items()})
        d["generator"] = {"gain": a.gen_gain, "offset": a.gen_offset}
        json.dump(d, open(a.out, "w"), indent=1)
        print("written to", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
