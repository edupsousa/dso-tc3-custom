#!/usr/bin/env python3
"""Export DSO-TC3 burst frames as a sigrok session (.sr) for PulseView / sigrok-cli.

  tc3_sr.py capture -o out.sr [-n FRAMES]
  tc3_sr.py roll -o out.sr [-n SAMPLES | -t SECONDS]   # slow timebases, T/div 15..21
  tc3_sr.py selftest -o out.sr     # synthetic sine, no device needed

Each frame is a separate 600-sample acquisition at the device's own rate
(about 15 per second). The .sr format has no
notion of a gap, so with -n > 1 the frames are simply concatenated and the
joins are NOT contiguous in time. The default of one frame is the faithful
export. Samples are volts (float32), channel name "CH1".

`roll` records the gap-free sample stream of T/div 15..21 (100 ms .. 10 s per
division, 250 .. 2.5 S/s) and starts with the samples already on the device's
screen. The .sr format stores the sample rate as whole Hz, so rates such as
12.5 or 2.5 S/s are written with every sample repeated twice (25 / 5 Hz) to keep
the time axis exact; the tool prints when it does that.
"""

import argparse
import math
import struct
import sys
import time
import zipfile

SIGROK_VERSION = "0.6.0"


def write_sr(path, volts, rate_hz, channel="CH1"):
    """Write a sigrok v2 session with one analog channel."""
    if rate_hz <= 0:
        raise ValueError("rate must be positive")
    meta = (
        "[global]\n"
        f"sigrok version={SIGROK_VERSION}\n\n"
        "[device 1]\n"
        f"samplerate={int(round(rate_hz))} Hz\n"
        "total analog=1\n"
        f"analog1={channel}\n"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("version", "2")
        z.writestr("metadata", meta)
        z.writestr("analog-1-1-1", struct.pack(f"<{len(volts)}f", *volts))


def sigrok_rate(rate_hz):
    """(whole-Hz rate, repeat factor k): smallest k in 1..4 for which rate*k is within 0.5% of a whole
    number of Hz. Repeating each sample k times then keeps the time axis correct."""
    for k in (1, 2, 3, 4):
        r = rate_hz * k
        if round(r) >= 1 and abs(r - round(r)) / r <= 0.005:
            return int(round(r)), k
    raise ValueError(f"cannot represent {rate_hz} Hz as a whole-Hz sigrok rate")


def roll(args):
    import tc3 as T

    ser = T.open_conn(args.port)
    try:
        hdr, volts, _ = T.roll_collect(ser, count=args.samples, seconds=args.time)
    except T.RollError as e:
        sys.exit(f"Roll error: {e}")
    rate_hz = hdr["rate"] * 1000.0
    hz, k = sigrok_rate(rate_hz)
    if k > 1:
        print(f"Note: {rate_hz:.6g} S/s is not a whole number of Hz; each sample is repeated {k}x "
              f"and the session rate is {hz} Hz.")
        volts = [x for x in volts for _ in range(k)]
    elif abs(hz - rate_hz) > 1e-9:
        print(f"Note: rate {rate_hz:.6g} S/s written as {hz} Hz ({abs(hz - rate_hz) / rate_hz * 100:.2f}% off).")
    write_sr(args.out, volts, hz)
    print(f"Wrote {len(volts)} samples ({len(volts) // k} recorded, T/div index {hdr['tdiv']}) at {hz} Hz to {args.out}")


def capture(args):
    import tc3 as T

    ser = T.open_conn(args.port)
    volts, rate, key = [], None, None
    got = 0
    try:
        last_seq = T.frame_header(ser, 0)["latest"]   # only frames newer than this count as fresh
    except T.FrameError:
        last_seq = 0
    deadline = time.time() + max(5.0, args.frames * 3.0)
    while got < args.frames:
        if time.time() > deadline:
            sys.exit(f"Timed out: got {got} of {args.frames} frames "
                     "(is T/div in the 1 us .. 50 ms range?)")
        try:
            if T.frame_header(ser, 0)["latest"] == last_seq:
                time.sleep(0.005)
                continue
            hdr, samples = T.fetch_frame(ser, 0)
        except T.FrameError:
            time.sleep(0.01)
            continue
        last_seq = hdr["seq"]
        vdiv, _probe, tdiv = struct.unpack("<3I", T.read_mem(ser, 0x20000668 + 0x28, 12))
        if (hdr["vdiv"], hdr["tdiv"]) != (vdiv, tdiv):
            continue   # frame predates a settings change; wait for a fresh one
        v = T.frame_volts(hdr, samples)
        if v is None:
            sys.exit("No voltage conversion at this setting (T/div >= 15 or unknown probe)")
        k = (hdr["vdiv"], hdr["tdiv"], hdr["probe"], hdr["gain"])
        if key is not None and k != key:
            sys.exit("Scope settings changed during capture; aborting")
        key, rate = k, hdr["rate"] * 1000.0
        volts.extend(v)
        got += 1
    if args.frames > 1:
        print(f"Warning: {args.frames} frames concatenated; the joins are not contiguous in time.")
    write_sr(args.out, volts, rate)
    print(f"Wrote {len(volts)} samples at {rate:.6g} Hz to {args.out}")


def selftest(args):
    rate = 100000.0
    volts = [1.0 + 0.5 * math.sin(2 * math.pi * 1000.0 * i / rate) for i in range(600)]
    write_sr(args.out, volts, rate)
    print(f"Wrote synthetic 1 kHz sine, {len(volts)} samples at {rate:g} Hz to {args.out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-p", "--port", help="Serial port (auto-detects 19f5:5740)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture", help="Capture frames from the device")
    c.add_argument("-o", "--out", required=True)
    c.add_argument("-n", "--frames", type=int, default=1)
    c.set_defaults(func=capture)
    r = sub.add_parser("roll", help="Record the slow-timebase stream (T/div 15..21)")
    r.add_argument("-o", "--out", required=True)
    r.add_argument("-n", "--samples", type=int, help="Stop after N samples")
    r.add_argument("-t", "--time", type=float, help="Stop after S seconds")
    r.set_defaults(func=roll)
    s = sub.add_parser("selftest", help="Write a synthetic session (no device)")
    s.add_argument("-o", "--out", required=True)
    s.set_defaults(func=selftest)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
