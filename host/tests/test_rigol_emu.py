#!/usr/bin/env python3
"""Offline test of the Rigol DS1052E emulation (tc3scpi/rigol.py) (--rigol): command answers, the
8-bit block encoding, the timebase mapping, and (when sigrok-cli is installed) a real libsigrok
rigol-ds acquisition against a demo device over TCP.
usage: host/tests/test_rigol_emu.py
"""
import math, os, shutil, struct, subprocess, sys, tempfile, threading, time, zipfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ["TC3_CAL"] = "0"   # these tests check the stock conversion; host/tests/test_cal.py checks the correction
import tc3 as T
import tc3scpi as S

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "common"))
from testkit import check, finish

def ask(sc, cmd):
    return sc.handle(cmd).decode("latin-1")

dev = S.DemoDevice()
sc = S.Scpi(dev, rigol=True)

# ---- identity and configuration queries (everything the driver asks at open)
check("IDN is a DS1052E the driver accepts", ask(sc, "*IDN?").startswith("Rigol Technologies,DS1052E,"))
check("CH1 on, CH2 off", ask(sc, ":CHAN1:DISP?") == "1\n" and ask(sc, ":CHAN2:DISP?") == "0\n")
check("probe string ends in X", ask(sc, ":CHAN1:PROB?") == "10X\n")
check("coupling, trigger source/slope", ask(sc, ":CHAN1:COUP?") == "DC\n" and ask(sc, ":TRIG:EDGE:SOUR?") == "CHAN1\n"
      and ask(sc, ":TRIG:EDGE:SLOP?") == "POS\n")
check("trigger status starts with A (auto)", ask(sc, ":TRIG:STAT?").startswith("A"))
check("scale is V/div x probe", abs(float(ask(sc, ":CHAN1:SCAL?")) - 1.0) < 1e-9)

# ---- timebase mapping: the driver derives sample_rate = 50 / timebase
for idx in range(22):
    tb = S.tbrep_seconds(idx)
    true_rate = (T.INTERLEAVED_RATE_KSPS[idx] * 1000.0) if idx in T.INTERLEAVED_RATE_KSPS else 25.0 / S.tdiv_seconds(idx)
    if abs(50.0 / tb - true_rate) / true_rate > 1e-9 or abs(S.tbrep_seconds(S.tbrep_to_index(tb)) - tb) > 1e-15:   # T/div 0 and 1 acquire identically
        check(f"timebase round trip idx {idx}", False, f"tb={tb} rate={50/tb} want {true_rate}")
        break
else:
    check("timebase: 50/tb equals the true sample rate and maps back to an equivalent index, all 22", True)
dev.params["tdiv"] = 7
check("TIM:SCAL? at T/div 200 us is 400 us", abs(float(ask(sc, ":TIM:SCAL?")) - 400e-6) < 1e-12)
ask(sc, ":TIM:SCAL 0.002")
check("TIM:SCAL 2 ms selects T/div 1 ms (index 9)", dev.params["tdiv"] == 9)
ask(sc, ":TIM:SCAL 0.0000002")
check("a timebase below the table picks the fastest index", dev.params["tdiv"] == 0)
dev.params["tdiv"] = 7

# ---- data block
blk = sc.handle(":WAV:DATA? CHAN1")
check("block header #9 + 600", blk[:11] == b"#9000000600" and len(blk) == 611, str(blk[:12]))
ask(sc, ":CHAN1:SCAL?")                       # driver caches the scale
inc = 1.0 / 25.6
dec = [(128 - b) * inc for b in blk[11:]]
want = [1.5 + 1.0 * math.sin(2 * math.pi * 5 * i / 600.0) for i in range(600)]
err = max(abs(a - b) for a, b in zip(dec, want))
check("decoded volts match the signal within one 8-bit step", err < inc, f"max err {err:.4f} step {inc:.4f}")
c2 = sc.handle(":WAV:DATA? CHAN2")
check("channel 2 is a flat 0 V block", c2[:11] == b"#9000000600" and set(c2[11:]) == {128})
# the driver's cached scale decides the encoding, even if the scope's V/div changes afterwards
dev.params["vdiv"] = 4                         # scope now at 2 V/div; driver still believes 1 V/div
blk2 = sc.handle(":WAV:DATA? CHAN1")
dec2 = [(128 - b) * inc for b in blk2[11:]]
check("encoding keeps the scale the driver cached", max(abs(a - b) for a, b in zip(dec2, want)) < inc)
ask(sc, ":CHAN1:SCAL 2.0")
check("CHAN1:SCAL set picks V/div and re-bases the encoding", dev.params["vdiv"] == 4 and abs(sc.rigol.reported_vdiv - 2.0) < 1e-9)
dev.params["vdiv"] = 3; sc.rigol.reported_vdiv = 1.0
# roll window shorter than 600 is padded on the left with the first sample
dev.params["tdiv"] = 16
blk3 = sc.handle(":WAV:DATA? CHAN1")
check("roll mode: 600-point block, short history padded", len(blk3) == 611 and blk3[11] == blk3[12])
dev.params["tdiv"] = 7

# ---- real libsigrok driver
if shutil.which("sigrok-cli"):
    srv = None
    import socketserver
    scpi = S.Scpi(S.DemoDevice(), rigol=True)
    class H(socketserver.StreamRequestHandler):
        def handle(self):
            for raw in self.rfile:
                r = scpi.handle(raw.decode("ascii", "replace"))
                if r: self.wfile.write(r)
    class Srv(socketserver.ThreadingTCPServer):
        allow_reuse_address = True; daemon_threads = True
    srv = Srv(("127.0.0.1", 5027), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    d = "rigol-ds:conn=tcp-raw/127.0.0.1/5027"
    r = subprocess.run(["sigrok-cli", "-d", d, "--scan"], capture_output=True, text=True, timeout=60)
    check("sigrok-cli --scan finds a Rigol DS1052E", "Rigol DS1052E" in r.stdout, r.stdout.strip()[-80:])
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "a.sr")
        r = subprocess.run(["sigrok-cli", "-d", d, "-C", "CH1", "--frames", "8", "-O", "srzip", "-o", out],
                           capture_output=True, text=True, timeout=90)
        z = zipfile.ZipFile(out)
        n = len(z.read("analog-1-1-1")) // 4
        meta = z.read("metadata").decode()
        check("sigrok-cli acquires 8 frames x 600 samples", r.returncode == 0 and n == 4800, f"rc={r.returncode} n={n}")
        check("sigrok sees 125 kHz at T/div 200 us", "samplerate=125 kHz" in meta, meta.split("samplerate=")[1].split()[0] if "samplerate=" in meta else meta)
    # timebases of 50 ms/div and up make the driver poll :TRIG:STAT? (until it is not T/A, then until it is); a constant
    # AUTO made it hang for ever. The demo device accepts any T/div index, so this exercises that path.
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "b.sr")
        try:
            r = subprocess.run(["sigrok-cli", "-d", d, "-C", "CH1", "-c", "timebase=200ms", "--frames", "2", "-O", "srzip", "-o", out],
                               capture_output=True, text=True, timeout=40)
            n = len(zipfile.ZipFile(out).read("analog-1-1-1")) // 4
            check("slow timebase (200 ms reported): the TRIG:STAT? polling completes, 2 frames x 600 samples", r.returncode == 0 and n == 1200,
                  f"rc={r.returncode} n={n} {r.stderr.strip()[-120:]}")
        except subprocess.TimeoutExpired:
            check("slow timebase (200 ms reported): the TRIG:STAT? polling completes", False, "sigrok-cli timed out")
else:
    print("SKIP sigrok-cli integration (sigrok-cli not installed)")
finish()
