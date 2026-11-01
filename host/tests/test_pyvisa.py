#!/usr/bin/env python3
"""PyVISA drives the SCPI server (SOURce, scope and error queue) over a raw TCP socket resource.

usage: uv run --with pyvisa --with pyvisa-py python host/tests/test_pyvisa.py [--hw [PORT]]

Without --hw it starts the server in this process against the demo device (no hardware). With --hw it connects to a
running `python3 host/tc3_scpi.py` (default port 5025) and so drives the real generator: it ends on a 1 kHz 3 V sine.
"""
import os, socketserver, sys, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import pyvisa
import tc3scpi as S

hw = "--hw" in sys.argv
port = 5025
if hw and sys.argv.index("--hw") + 1 < len(sys.argv):
    port = int(sys.argv[sys.argv.index("--hw") + 1])
if not hw:
    port = 5028
    scpi = S.Scpi(S.DemoDevice())

    class H(socketserver.StreamRequestHandler):
        def handle(self):
            for raw in self.rfile:
                r = scpi.handle(raw.decode("ascii", "replace"))
                if r:
                    self.wfile.write(r)

    class Srv(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    srv = Srv(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "common"))
from testkit import check, finish

rm = pyvisa.ResourceManager("@py")
inst = rm.open_resource(f"TCPIP::127.0.0.1::{port}::SOCKET")
inst.read_termination = "\n"
inst.write_termination = "\n"
inst.timeout = 10000

idn = inst.query("*IDN?")
check("*IDN? answers", "," in idn, idn)
inst.write("*CLS")

# SOURce: set, then query back, in long and short forms
inst.write("SOURce:FUNCtion SQUare")
check("FUNC query follows the set (long form)", inst.query("SOUR:FUNC?").strip() == "SQU", inst.query("SOUR:FUNC?"))
inst.write("SOUR:FREQ 2500")
check("FREQ round trip", float(inst.query("SOUR:FREQ?")) == 2500.0)
inst.write("SOUR:VOLT:AMPL 1.5")
check("VOLT round trip", abs(float(inst.query("SOUR:VOLT?")) - 1.5) < 1e-6)
inst.write("SOUR:PULS:DCYC 30")
check("DCYC round trip", int(float(inst.query("SOUR:DCYC?"))) == 30)
for fn in ("SIN", "SQU", "PULS", "TRI", "RAMP", "DC"):
    inst.write("SOUR:FUNC " + fn)
    check(f"FUNC {fn} round trip", inst.query("SOUR:FUNC?").strip() == fn)

# errors: out of range is -222, a bad function -224, and the queue is read with SYST:ERR?
inst.write("SOUR:FREQ 0")
e = inst.query("SYST:ERR?")
check("FREQ 0 -> error -222", e.startswith("-222"), e)
inst.write("SOUR:FUNC BANANA")
e = inst.query("SYST:ERR?")
check("bad function -> error -224", e.startswith("-224"), e)
e = inst.query("SYST:ERR?")
check("queue empty afterwards", e.startswith("0"), e)

# scope side through the same session
v = inst.query("WAV:DATA?")
check("WAV:DATA? returns a comma list of volts", len(v.split(",")) > 50 and float(v.split(",")[0]) == float(v.split(",")[0]), len(v.split(",")))
check("ACQ:SRAT? is a number", float(inst.query("ACQ:SRAT?")) > 0)

# :AUToscale is the scope's own Auto routine (demo: settles on 500 us/div, 1 V/div); on hardware it needs the scope view
if not hw:
    inst.write("TIM:SCAL 0.01")
    inst.write(":AUToscale")
    e = inst.query("SYST:ERR?")
    check(":AUToscale accepted", e.startswith("0"), e)
    check("AUToscale settled the timebase", abs(float(inst.query("TIM:SCAL?")) - 5e-4) < 1e-9, inst.query("TIM:SCAL?"))

# trigger mode and slope (demo: stored in the fake state block; hardware: the stock trigger mode and edge)
for want in ("NORM", "SING", "AUTO"):
    inst.write("TRIG:SWE " + want)
    check(f"TRIG:SWE {want} round trip", inst.query("TRIG:SWE?").strip() == want)
inst.write("TRIG:EDGE:SLOP NEG")
check("TRIG:EDGE:SLOP NEG round trip", inst.query("TRIG:EDGE:SLOP?").strip() == "NEG")
inst.write("TRIG:EDGE:SLOP POS")
check("TRIG:EDGE:SLOP POS round trip", inst.query("TRIG:EDGE:SLOP?").strip() == "POS")
inst.write("TRIG:SWE BANANA")
e = inst.query("SYST:ERR?")
check("TRIG:SWE BANANA -> -224", e.startswith("-224"), e)

# leave a known output (1 kHz 3 V sine) when talking to the real device
inst.write("SOUR:FUNC SIN"); inst.write("SOUR:FREQ 1000"); inst.write("SOUR:VOLT 3.0")
check("final state sine 1 kHz 3 V", inst.query("SOUR:FUNC?").strip() == "SIN" and float(inst.query("SOUR:FREQ?")) == 1000.0)
inst.close()
finish()
