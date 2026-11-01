"""Rigol DS1052E emulation for PulseView / sigrok-cli (libsigrok "rigol-ds", PROTOCOL_V2, IEEE 488.2 block format).
The server then identifies itself as a DS1052E, answers the driver's configuration queries and sends 600-point, 8-bit frames."""
import math

import tc3 as T
from .device import DeviceError
from .scales import DIV_MV, tdiv_index, tdiv_seconds, vdiv_index, vdiv_volts

RIGOL_IDN = "Rigol Technologies,DS1052E,TC3EMU0001,0.2.5"
RIGOL_POINTS = 600
# Commands the driver sends that need no action.
IGNORED = {"KEY:LOCK", "WAV:POIN:MODE", "WAV:FORM", "WAV:MODE", "WAV:SOUR", "WAV:BEG", "WAV:END", "WAV:RES", "WAV:STAR", "WAV:STOP"}


# The DS1000 driver assumes a live frame of 600 points spans 12 divisions, so it computes sample_rate = 50 / timebase.
# Our 600-sample frame spans 24 divisions (25 samples per division), so we report the timebase the driver needs to get
# the real sample rate back.
def tbrep_seconds(idx):
    """Timebase reported to the Rigol driver for T/div index idx (= 50 / real sample rate)."""
    if idx in T.INTERLEAVED_RATE_KSPS:
        return 50.0 / (T.INTERLEAVED_RATE_KSPS[idx] * 1000.0)
    return 2.0 * tdiv_seconds(idx)


def tbrep_to_index(value):
    return min(range(22), key=lambda i: abs(math.log(tbrep_seconds(i) / value)))


class Rigol:
    def __init__(self, scpi):
        self.s = scpi
        self.stat_run = False     # TRIG:STAT? answers "RUN" once after :RUN / each data block, then "AUTO"
        self.ch_on = {1: True, 2: False}
        self.reported_vdiv = None  # V/div (volts) last given to the driver; data is encoded with it

    def _vdiv(self):
        idx = self.s.dev.get("vdiv")
        if idx >= len(DIV_MV):
            raise DeviceError(-241, f"V/div index {idx} out of table")
        return vdiv_volts(idx, self.s.probe_mult())

    def block(self, ch):
        if ch == 1 and self.ch_on[1]:
            _, v = self.s.volts(roll_n=RIGOL_POINTS)
            if len(v) < RIGOL_POINTS:                  # young roll epoch: hold the first sample on the left
                v = [v[0]] * (RIGOL_POINTS - len(v)) + v
            v = v[-RIGOL_POINTS:]
            inc = (self.reported_vdiv or self._vdiv()) / 25.6   # driver: volts = (128 - byte) * inc - offset
            data = bytes(max(0, min(255, int(round(128 - x / inc)))) for x in v)
        else:
            data = bytes([128]) * RIGOL_POINTS         # channel 2 does not exist: 0 V
        # No trailing newline: for protocol V2 the driver does not discard it, and it would corrupt the next block header.
        # (libsigrok then logs "Read should have been completed" once per frame; its tcp-raw transport only counts a read
        # as complete when it comes back short. Harmless.)
        return b"#9" + f"{len(data):09d}".encode() + data

    def _channel(self, ch, name, query, arg):
        dev, num = self.s.dev, self.s.num
        if name == "DISP":
            if query:
                return "1" if self.ch_on[ch] else "0"
            self.ch_on[ch] = arg.upper() in ("ON", "1")
            return None
        if name == "SCAL":
            if query:
                v = self._vdiv() if ch == 1 else 1.0
                if ch == 1:
                    self.reported_vdiv = v
                return num(v)
            if ch == 1:
                idx = vdiv_index(float(arg), self.s.probe_mult())
                dev.ui("vdiv", idx, "vdiv")
                self.reported_vdiv = vdiv_volts(idx, self.s.probe_mult())
            return None
        if name == "OFFS" and query:
            return num(0.0)
        if name == "PROB":
            if query:
                return ("10X" if self.s.probe_mult() == 10 else "1X") if ch == 1 else "1X"
            if ch == 1:
                dev.ui("probe", 1 if float(arg.upper().rstrip("X")) >= 10 else 0)
            return None
        if name == "COUP":
            if query:
                return "DC" if ch != 1 or dev.state(T.OFF_COUPLING) == 1 else "AC"
            if ch == 1 and arg.upper() in ("AC", "DC"):
                dev.ui("coupling", 1 if arg.upper() == "DC" else 0)
            return None
        raise _Unhandled

    def handle(self, k, query, arg):
        """(handled, reply) for the commands the rigol-ds driver sends; the others fall through to the plain SCPI table."""
        m = k.split(":")
        dev, num = self.s.dev, self.s.num
        try:
            if k == "*IDN" and query:
                return True, RIGOL_IDN
            if k == "*OPC" and query:
                return True, "1"
            if m[0] in ("CHAN1", "CHAN2") and len(m) == 2:
                return True, self._channel(int(m[0][-1]), m[1], query, arg)
            if k == "TIM:SCAL":
                if query:
                    return True, num(tbrep_seconds(dev.get("tdiv")))
                dev.ui("tdiv", tbrep_to_index(float(arg)))
                return True, None
            if k == "TIM:OFFS" and query:
                return True, num(0.0)
            if k == "TRIG:EDGE:SOUR" and query:
                return True, "CHAN1"
            if k == "TRIG:EDGE:LEV" and query:
                return True, num(0.0)
            if k == "TRIG:STAT" and query:
                # libsigrok polls this for timebases >= 50 ms/div: first until the status is not T/A, then until it is.
                # A constant AUTO never leaves the first phase, so the driver times out forever.
                run, self.stat_run = self.stat_run, False
                return True, "RUN" if run else "AUTO"
            if k == "WAV:DATA" and query:
                chan = arg.upper().replace(" ", "")
                if chan not in ("CHAN1", "CHAN2"):
                    raise DeviceError(-224, "WAVeform:DATA? needs CHAN1 or CHAN2 in Rigol mode")
                self.stat_run = True
                return True, self.block(int(chan[-1]))
            if k in IGNORED and not query:
                return True, None
        except _Unhandled:
            pass
        return False, None


class _Unhandled(Exception):
    pass
