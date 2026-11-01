#!/usr/bin/env python3
"""Test of the libsigrok driver fnirsi-dso-tc3 with the real sigrok-cli against a fake device (fake_tc3.py).

usage: TC3_SIGROK_LIB=<libsigrok lib dir> TC3_PTYSHIM=<ptyshim.so> sigrok/tests/test_driver.py
  TC3_SIGROK_LIB  directory with the patched libsigrok.so (put first in LD_LIBRARY_PATH)
  TC3_PTYSHIM     compiled sigrok/tests/ptyshim.c (the shim lets libserialport open a pty)
sigrok/build.sh builds both and runs this test.
"""
import os, subprocess, sys, tempfile, time, zipfile, struct

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, ".."))
sys.path.insert(0, os.path.join(here, "..", "..", "host"))
os.environ["TC3_CAL"] = "1"
import fake_tc3 as F
import tc3 as T

LIB = os.environ.get("TC3_SIGROK_LIB")
SHIM = os.environ.get("TC3_PTYSHIM")
if not LIB or not SHIM:
    sys.exit(__doc__)

fails = 0
SHOW_BUDGET = 40      # requests one `--show` may make (the old per-field reads took about 120)


def check(name, cond, extra=""):
    global fails
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    fails += 0 if cond else 1


class Run:
    """A fresh fake device on a pty and a way to run sigrok-cli against it."""
    def __init__(self, **kw):
        self.dev = F.FakeTC3(**kw)
        self.path, self.slave = F.open_pty(self.dev)

    def _cmd(self, args):
        return [os.path.join(here, "with_fake_tty.sh"), self.path, SHIM, "sigrok-cli",
                "-d", "fnirsi-dso-tc3:conn=" + self.path, *args]

    def cli(self, *args, timeout=40):
        env = dict(os.environ, LD_LIBRARY_PATH=LIB)
        return subprocess.run(self._cmd(args), capture_output=True, text=True, env=env, timeout=timeout)

    def popen(self, *args):
        env = dict(os.environ, LD_LIBRARY_PATH=LIB)
        return subprocess.Popen(self._cmd(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

    def close(self):
        os.close(self.slave)


def read_sr(path):
    z = zipfile.ZipFile(path)
    meta = z.read("metadata").decode()
    rate = next(l.split("=")[1] for l in meta.splitlines() if l.startswith("samplerate="))
    data = b"".join(z.read(n) for n in sorted(z.namelist()) if n.startswith("analog"))
    return rate, list(struct.unpack("<%df" % (len(data) // 4), data))


def expected(k, vdiv, probe):
    """Volts the driver should report for fake sample index k."""
    raw = F.to_raw(F.volts_at(k), vdiv, probe)
    return T.cal_volts(T.raw_to_volts(raw, F.GAIN_BY_VDIV[vdiv], probe), vdiv, probe)


def capture(run, *args):
    out = tempfile.mktemp(suffix=".sr")
    r = run.cli(*args, "-O", "srzip", "-o", out)
    return r, (read_sr(out) if os.path.exists(out) else (None, []))


# ---- scan and options
r = Run()
res = r.cli("--scan")
check("scan finds the device", "FNIRSI DSO-TC3" in res.stdout and "TC3-FAKE 1.0" in res.stdout, res.stdout + res.stderr)
res = r.cli("--show")
check("show lists 22 timebases", all(t in res.stdout for t in ("1 us", "500 ms", "10 s", "20 us")), res.stdout)
check("show lists the sample rates", "samplerate - supported samplerates" in res.stdout and "250 Hz" in res.stdout, res.stdout)
res = r.cli("--show", "-g", "GEN")
check("show has the generator group", "GEN" in res.stdout and "output_frequency" in res.stdout, res.stdout)
r.close()

# ---- burst frames
r = Run(tdiv=8)
res, (rate, data) = capture(r, "--frames", "2")
check("burst: 2 frames of 600 samples", len(data) == 1200, f"{len(data)} {res.stderr}")
check("burst: rate 50 kHz at 500 us/div", rate == "50 kHz", rate)
bad = [i for i in range(min(600, len(data))) if abs(data[i] - expected(i, 3, 1)) > 1e-3]
check("burst: volts match the calibrated conversion", len(data) >= 600 and not bad, f"{len(bad)} bad, first {bad[:3]}")
r.close()

r = Run(tdiv=0)
res, (rate, data) = capture(r, "--frames", "1")
check("burst: interleaved T/div 0 reports 10285714 Hz", rate in ("10285714 Hz", "10.285714 MHz", "10.2857 MHz") or rate.startswith("10.28"), rate)
r.close()

# ---- roll stream
r = Run(tdiv=15)
res, (rate, data) = capture(r, "--samples", "600")
check("roll: exactly 600 samples", len(data) == 600, f"{len(data)} {res.stderr}")
check("roll: 250 Hz at T/div 15", rate == "250 Hz", rate)
if len(data) == 600:
    k0 = next((k for k in range(0, 4000) if all(abs(data[i] - expected(k + i, 3, 1)) < 1e-3 for i in range(10))), None)
    ok = k0 is not None and all(abs(data[i] - expected(k0 + i, 3, 1)) < 1e-3 for i in range(600))
    check("roll: samples are consecutive (gap-free)", ok, f"start {k0}")
r.close()

r = Run(tdiv=19, speed=20)
res, (rate, data) = capture(r, "--samples", "50")
check("roll: 12.5 S/s is sent as 25 Hz with doubled samples", rate == "25 Hz" and len(data) == 100
      and all(data[2 * i] == data[2 * i + 1] for i in range(50)), f"{rate} {len(data)}")
r.close()

r = Run(tdiv=16, speed=5)
res, (rate, data) = capture(r, "--samples", "50")
check("roll: T/div 16 reports 125 Hz", rate == "125 Hz" and len(data) == 50, f"{rate} {len(data)}")
r.close()

# ---- settings reach the device
r = Run(tdiv=8)
res = r.cli("-g", "CH1", "-c", "vdiv=2 V", "--frames", "1")
check("vdiv=2 V at X10 -> index 4 (200 mV x 10)", r.dev.s["vdiv"] == 4 and res.returncode == 0, f"{r.dev.s} {res.stderr}")
res = r.cli("-g", "CH1", "-c", "vdiv=500 mV", "--frames", "1")
check("vdiv=500 mV at X10 -> index 2 (50 mV x 10)", r.dev.s["vdiv"] == 2, f"{r.dev.s} {res.stderr}")
res = r.cli("-g", "CH1", "-c", "probe_factor=1", "--frames", "1")
check("probe_factor=1 -> X1", r.dev.s["probe"] == 0, str(r.dev.s))
res = r.cli("-g", "CH1", "-c", "coupling=AC", "--frames", "1")
check("coupling=AC", r.dev.s["dc"] == 0, str(r.dev.s))
res = r.cli("-c", "timebase=1 ms", "--frames", "1")
check("timebase=1 ms -> T/div index 9", r.dev.s["tdiv"] == 9, f"{r.dev.s} {res.stderr}")
res = r.cli("-c", "triggerslope=f", "--frames", "1")
check("triggerslope=f -> falling", r.dev.s["edge"] == 1, str(r.dev.s))
r.close()

r2 = Run(tdiv=8)
r2.cli("-c", "samplerate=250", "--time", "1")
check("samplerate=250 Hz -> T/div index 15", r2.dev.s["tdiv"] == 15, str(r2.dev.s))
r2.cli("-c", "samplerate=50k", "--time", "1")
check("samplerate=50 kHz -> T/div index 8", r2.dev.s["tdiv"] == 8, str(r2.dev.s))
res = r2.cli("-c", "triggersource=CH1", "--time", "1")
check("triggersource=CH1 is accepted", res.returncode == 0, res.stderr)
res = r2.cli("-c", "triggersource=EXT", "--time", "1")
check("triggersource=EXT is refused", res.returncode != 0, res.stderr)
r2.close()

# ---- trigger level and mode
r3 = Run(tdiv=8)
res = r3.cli("--show")
check("show lists triggermode and triggerlevel", "triggermode" in res.stdout and "triggerlevel" in res.stdout and "normal" in res.stdout, res.stdout)
want = T.cal_volts(21 / 23.0 * 1.0, 3, 1)      # default offset 14 at position 35, 1 V/div (X10)
check("triggerlevel is read back in volts", f"triggerlevel: {want:.2f}" in res.stdout or f"triggerlevel: {want:.1f}" in res.stdout, f"{want} {res.stdout}")
r3.cli("-c", "triggerlevel=1.5", "--time", "1")
volts = T.cal_volts((r3.dev.s["pos"] - r3.dev.s["trigoff"]) / 23.0 * 1.0, 3, 1)
check("triggerlevel=1.5 -> offset on the device", abs(volts - 1.5) < 0.03, f"{volts} {r3.dev.s}")
r3.cli("-c", "triggerlevel=-50", "--time", "1")
check("triggerlevel far out is clamped to the offset range", r3.dev.s["trigoff"] == 99, str(r3.dev.s))
r3.cli("-c", "triggermode=normal", "--time", "1")
check("triggermode=normal", r3.dev.s["trigmode"] == 1, str(r3.dev.s))
r3.cli("-c", "triggermode=single", "--time", "1")
check("triggermode=single", r3.dev.s["trigmode"] == 2, str(r3.dev.s))
res = r3.cli("-c", "triggermode=bogus", "--time", "1")
check("triggermode=bogus is refused", res.returncode != 0, res.stderr)
r3.close()

# ---- every timebase maps to its own device index
r = Run(tdiv=8)
labels = ["1 us", "2 us", "5 us", "10 us", "20 us", "50 us", "100 us", "200 us", "500 us", "1 ms", "2 ms", "5 ms",
          "10 ms", "20 ms", "50 ms", "100 ms", "200 ms", "500 ms", "1 s", "2 s", "5 s", "10 s"]
bad = []
for i, lab in enumerate(labels):
    r.cli("-c", "timebase=" + lab, "--time", "1")
    if r.dev.s["tdiv"] != i:
        bad.append((lab, r.dev.s["tdiv"]))
check("all 22 timebases (incl. 500 ms and 5 s) map to the right T/div", not bad, str(bad))
r.close()

# ---- generator
r = Run()
res = r.cli("-g", "GEN", "-c", "pattern=sine:output_frequency=2000:amplitude=2:output_duty_cycle=25", "--time", "1")
g = r.dev.gen
check("generator: sine 2000 Hz 2 V duty 25", g == {"wave": 0, "freq": 2000, "duty": 25, "amp": 2.0}, f"{g} {res.stderr}")
res = r.cli("-g", "GEN", "-c", "amplitude=5", "--time", "1")
check("generator: out-of-range amplitude is refused", res.returncode != 0 and r.dev.gen["amp"] == 2.0, f"{res.returncode} {r.dev.gen}")
r.close()

# ---- host-side cost: transfers and waiting
r = Run(tdiv=8)
t0 = time.time()
res = r.cli("--scan")
dt = time.time() - t0
check("scan does not wait for a longer banner", dt < 0.35 and "TC3-FAKE 1.0" in res.stdout, f"{dt:.2f} s")
r.close()

r = Run(tdiv=8)
res = r.cli("--show")
n = sum(v for k, v in r.dev.count.items() if k != "hdr_seq")
check("show reads the scope state in one transfer each time", r.dev.count[0x51] == 0 and n <= SHOW_BUDGET, f"{n} requests {dict(r.dev.count)}")
r.close()

r = Run(tdiv=8)
res = r.cli("--frames", "3")
check("frames: no header re-request per frame (chunks are checked by the device)", r.dev.count["hdr_seq"] == 0, str(dict(r.dev.count)))
r.close()

# ---- the cable is pulled during a capture: stop quickly, say why
for tdiv, what in ((8, "burst"), (15, "roll")):
    r = Run(tdiv=tdiv)
    p = r.popen("--time", "60000", "-O", "csv")
    time.sleep(1.5)
    r.dev.hangup = True
    t0 = time.time()
    try:
        out, err = p.communicate(timeout=10)
        dt = time.time() - t0
        check(f"{what}: pulled cable stops the capture within 3 s", dt < 3 and "connection" in err, f"{dt:.1f} s {err[-200:]}")
    except subprocess.TimeoutExpired:
        p.kill()
        check(f"{what}: pulled cable stops the capture within 3 s", False, "still running after 10 s")
    r.close()

# ---- refusals
r = Run()
r.dev.s["mode"] = 2
res = r.cli("--frames", "1")
check("capture is refused outside the scope view", res.returncode != 0 and "scope view" in res.stderr, res.stderr)
r.close()

print("\n%s" % ("all passed" if not fails else f"{fails} FAILED"))
sys.exit(1 if fails else 0)
