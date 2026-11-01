#!/usr/bin/env python3
"""Hardware test of the SCPI SOURce commands over TCP: start `python3 host/tc3_scpi.py` first (port 5025); the
scope commands measure each generator setting. Leaves a 1 kHz 3 V sine. usage: host/tests/test_scpi_gen_hw.py"""
import socket, time
sk = socket.create_connection(("127.0.0.1", 5025), timeout=10)
f = sk.makefile("rwb", buffering=0)
def cmd(c):
    f.write((c + "\n").encode())
def q(c):
    cmd(c)
    return f.readline().decode().strip()
def measure(tdiv_s):
    cmd("TIM:SCAL %g" % tdiv_s); time.sleep(0.8)
    xinc = float(q("WAV:XINC?"))
    v = [float(x) for x in q("WAV:DATA?").split(",")]
    lo, hi = min(v), max(v); mid = (lo + hi) / 2
    cr = [i for i in range(1, len(v)) if v[i-1] < mid <= v[i]]
    fr = (len(cr) - 1) / ((cr[-1] - cr[0]) * xinc) if len(cr) > 1 else float("nan")
    return hi - lo, sum(v) / len(v), fr, 100.0 * sum(1 for x in v if x > mid) / len(v)
print("IDN:", q("*IDN?"))
cases = [("SIN", 1000, 3.0, None, 500e-6), ("SIN", 2000, 2.0, None, 200e-6), ("TRI", 500, 3.0, None, 1e-3),
         ("SQU", 1000, 3.0, None, 500e-6), ("PULS", 1000, 3.0, 25, 500e-6), ("RAMP", 1000, 3.0, 75, 500e-6), ("DC", 1000, 2.0, None, 500e-6)]
for fn, fq, amp, duty, td in cases:
    cmd("SOUR:FUNC " + fn); cmd("SOURce:FREQuency %d" % fq); cmd("SOUR:VOLT:AMPL %g" % amp)
    if duty is not None: cmd("SOUR:PULS:DCYC %d" % duty)
    rb = (q("SOUR:FUNC?"), q("SOUR:FREQ?"), q("SOUR:VOLT?"), q("SOUR:DCYC?"), q("SYST:ERR?"))
    vpp, mean, fr, hi = measure(td)
    print("%-5s set %5d Hz %.1f V d%s | readback %s | scope: Vpp %.2f mean %.2f f %.1f Hz high %.0f%%" % (fn, fq, amp, duty, rb, vpp, mean, fr, hi))
# errors
for bad in ("SOUR:FREQ 0", "SOUR:FREQ 200000", "SOUR:VOLT 5", "SOUR:DCYC 101", "SOUR:FUNC FOO", "SOUR:BOGUS 1"):
    cmd(bad); print("%-18s -> %s" % (bad, q("SYST:ERR?")))
# leave a sine 1 kHz, 3 V
cmd("SOUR:FUNC SIN"); cmd("SOUR:FREQ 1000"); cmd("SOUR:VOLT 3.0"); print("final:", q("SOUR:FUNC?"), q("SOUR:FREQ?"), q("SOUR:VOLT?"))
