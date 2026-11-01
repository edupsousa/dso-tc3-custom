"""SCPI command interpreter for the scope and generator. handle(line) -> bytes reply, or b'' for none."""
import struct
import time

import tc3 as T
from . import source
from .device import DeviceError
from .rigol import Rigol
from .scales import DIV_MV, FRAME_TDIV_MAX, FRAME_TDIV_MIN, ROLL_WINDOW, tdiv_index, tdiv_seconds, vdiv_index, vdiv_volts

IDN = "FNIRSI,DSO-TC3,0,TC3-EXT 1.0"
TRIG_MODES = ("AUTO", "NORM", "SING")


class Scpi:
    def __init__(self, dev, rigol=False, trace=False):
        self.dev = dev
        self.trace = trace
        self.rigol = Rigol(self) if rigol else None
        self.errors = []
        self.wav_format = "ASC"      # ASC (comma separated volts) or FLO (IEEE block, float32 LE)
        self.last_seq = 0
        self.frame_timeout = 3.0
        # short-form key -> (handler(query, arg), query_only)
        self.table = {
            "*IDN": (lambda q, a: IDN, True), "*CLS": (self._cls, False), "*RST": (lambda q, a: None, False),
            "*OPC": (lambda q, a: "1" if q else None, False), "SYST:ERR": (self._syst_err, True),
            "TRIG:SWE": (self._trig_sweep, False), "TRIG:EDGE:SLOP": (self._trig_slope, False),
            "AUT": (self._autoscale, False), "AUTO": (self._autoscale, False),
            "RUN": (lambda q, a: self._run_stop(True), False), "STOP": (lambda q, a: self._run_stop(False), False),
            "CHAN1:SCAL": (self._chan_scale, False), "CHAN1:PROB": (lambda q, a: str(self.probe_mult()), True),
            "TIM:SCAL": (self._tim_scale, False), "ACQ:SRAT": (lambda q, a: self.num(self._rate_hz()), True),
            "WAV:XINC": (lambda q, a: self.num(1.0 / self._rate_hz()), True), "WAV:FORM": (self._wav_form, False),
            "WAV:PRE": (self._wav_pre, True), "WAV:DATA": (self._wav_data, True),
            "FRAM:SEQ": (lambda q, a: str(self.dev.latest_seq()), True),
        }

    # ---- helpers
    def err(self, code, msg):
        self.errors.append((code, msg))

    @staticmethod
    def num(v):
        return f"{v:.6e}"

    def probe_mult(self):
        return T.PROBE_MULT[min(self.dev.get("probe"), 1)]

    def volts(self, roll_n=ROLL_WINDOW):
        """Waveform volts for the current timebase: roll window (T/div >= 15) or the newest burst frame."""
        if self.dev.get("tdiv") >= T.ROLL_MIN_TDIV:
            return self.dev.roll_window(roll_n)
        # Burst frame: wait for one newer than the last returned (up to the timeout).
        deadline = time.time() + self.frame_timeout
        while True:
            seq = self.dev.latest_seq()
            if (seq and seq != self.last_seq) or time.time() > deadline:
                break
            time.sleep(0.005)
        hdr, samples = self.dev.frame()
        # The ring keeps the last frame forever: refuse it if the device has moved to a setting that no longer
        # produces frames (or a different range).
        live_tdiv, live_vdiv = self.dev.get("tdiv"), self.dev.get("vdiv")
        if (hdr["tdiv"], hdr["vdiv"]) != (live_tdiv, live_vdiv) or not (FRAME_TDIV_MIN <= live_tdiv <= FRAME_TDIV_MAX):
            raise DeviceError(-231, f"no new frame: ring holds T/div {hdr['tdiv']} V/div {hdr['vdiv']}, "
                                    f"device is at T/div {live_tdiv} V/div {live_vdiv}")
        self.last_seq = hdr["seq"]
        volts = T.frame_volts(hdr, samples)
        if volts is None:
            raise DeviceError(-230, "frame has no voltage conversion at this setting")
        return hdr, volts

    def _rate_hz(self):
        """Samples per second at the current timebase."""
        if self.dev.get("tdiv") >= T.ROLL_MIN_TDIV:
            return self.dev.roll_header()["rate"] * 1000.0
        return self.dev.frame()[0]["rate"] * 1000.0

    @staticmethod
    def _short(mnemonic):
        """SCPI short form: first four letters, or three if the fourth is a vowel; trailing digits are kept
        (CHANnel1 -> CHAN1, WAVeform -> WAV)."""
        if mnemonic.startswith("*"):
            return mnemonic
        base = mnemonic.rstrip("0123456789")
        digits = mnemonic[len(base):]
        if len(base) > 4:
            base = base[:3] if base[3] in "AEIOU" else base[:4]
        return base + digits

    # ---- dispatch
    def handle(self, line):
        out = []
        for cmd in line.strip().split(";"):
            cmd = cmd.strip()
            if not cmd:
                continue
            try:
                r = self._one(cmd)
            except DeviceError as e:
                self.err(e.code, e.msg)
                r = None
            except Exception as e:  # keep the connection alive on any bug
                self.err(-300, f"internal error: {e}")
                r = None
            if r is not None:
                out.append(r if isinstance(r, bytes) else (r + "\n").encode())
        return b"".join(out)

    def _one(self, cmd):
        head, _, arg = cmd.partition(" ")
        arg = arg.strip()
        key = head.upper()
        query = key.endswith("?")
        if query:
            key = key[:-1]
        k = ":".join(self._short(p) for p in key.lstrip(":").split(":"))
        if self.rigol:
            handled, reply = self.rigol.handle(k, query, arg)
            if handled:
                return reply
        entry = self.table.get(k)
        if entry and (query or not entry[1]):
            return entry[0](query, arg)
        if k.startswith("SOUR:"):
            return source.handle(self, k[5:], query, arg)
        if k.startswith("MEAS:") and query:
            stat = self._meas(k.split(":")[1])
            if stat is not None:
                return stat
        raise DeviceError(-113, f"Undefined header: {cmd}")

    # ---- commands
    def _cls(self, query, arg):
        self.errors.clear()

    def _syst_err(self, query, arg):
        if self.errors:
            c, m = self.errors.pop(0)
            return f'{c},"{m}"'
        return '0,"No error"'

    def _trig_sweep(self, query, arg):             # trigger mode: AUTO / NORMal / SINGle (stock +0x3c)
        if query:
            m = self.dev.state(T.OFF_TRIGMODE)
            return TRIG_MODES[m] if m < 3 else str(m)
        a = arg.upper()
        idx = next((i for i, n in enumerate(TRIG_MODES) if a.startswith(n)), None)
        if idx is None:
            raise DeviceError(-224, "TRIGger:SWEep must be AUTO, NORMal or SINGle")
        self.dev.ui("trigmode", idx)

    def _trig_slope(self, query, arg):
        if query:
            return "NEG" if self.dev.state(T.OFF_EDGE) else "POS"
        a = arg.upper()
        if not a.startswith(("POS", "NEG")):
            raise DeviceError(-224, "TRIGger:EDGE:SLOPe must be POSitive or NEGative")
        self.dev.ui("edge", 1 if a.startswith("NEG") else 0)

    def _autoscale(self, query, arg):              # :AUToscale (Rigol: :AUTO)
        if query:
            raise DeviceError(-113, "AUToscale has no query form")
        self.dev.autoscale()

    def _run_stop(self, running):
        if self.rigol:
            self.rigol.stat_run = True
        self.dev.ui("runstop", 0 if running else 1, "run_stop")

    def _chan_scale(self, query, arg):
        if query:
            idx = self.dev.get("vdiv")
            if idx >= len(DIV_MV):
                raise DeviceError(-241, f"V/div index {idx} out of table")
            return self.num(vdiv_volts(idx, self.probe_mult()))
        self.dev.ui("vdiv", vdiv_index(float(arg), self.probe_mult()), "vdiv")

    def _tim_scale(self, query, arg):
        if query:
            return self.num(tdiv_seconds(self.dev.get("tdiv")))
        self.dev.ui("tdiv", tdiv_index(float(arg)))

    def _wav_form(self, query, arg):
        if query:
            return self.wav_format
        f = arg.upper()[:3]
        if f not in ("ASC", "FLO"):
            raise DeviceError(-224, "WAVeform:FORMat must be ASCii or FLOat")
        self.wav_format = f

    def _wav_pre(self, query, arg):
        hdr, v = self.volts()
        fmt = 4 if self.wav_format == "FLO" else 2   # 2 = ASCII (Rigol code); 4 = float32 block (this server)
        return f"{fmt},0,{len(v)},1,{1.0 / (hdr['rate'] * 1000.0):.6e},0,0,1,0,0"

    def _wav_data(self, query, arg):
        _, v = self.volts()
        if self.wav_format == "FLO":
            payload = struct.pack(f"<{len(v)}f", *v)
            n = str(len(payload))
            return b"#" + str(len(n)).encode() + n.encode() + payload + b"\n"
        return ",".join(f"{x:.5f}" for x in v)

    def _meas(self, name):
        stats = {"VMAX": max, "VMIN": min, "VPP": lambda v: max(v) - min(v),
                 "VAV": lambda v: sum(v) / len(v), "VAVE": lambda v: sum(v) / len(v)}
        if name not in stats:
            return None
        return self.num(stats[name](self.volts()[1]))
