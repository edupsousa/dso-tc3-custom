"""The device as the SCPI layer sees it: a locked wrapper over the tc3 library, and a synthetic one for tests."""
import math
import struct
import threading
import time

import tc3 as T
from .scales import ROLL_WINDOW, tdiv_seconds


class DeviceError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


class Device:
    """Thin, locked wrapper over the extension protocol. Every method takes the lock for one exchange."""

    def __init__(self, port=None):
        self.ser = T.open_conn(port, timeout=0.5)
        self.lock = threading.Lock()

    def _param(self, name):
        try:
            return T.get_param(self.ser, name)
        except T.Tc3Error:
            raise DeviceError(-314, f"device did not answer GET {name}")

    def get(self, name):
        with self.lock:
            return self._param(name)

    def set(self, name, value):
        with self.lock:
            try:
                T.set_param(self.ser, name, value, ok=(0, 4))
            except T.Tc3Error:
                raise DeviceError(-314, f"device rejected SET {name}={value}")

    def autoscale(self, timeout=15.0):
        """The scope's own Auto routine: a virtual `select` press in the scope view (stock key 2), then wait until
        V/div and T/div stop changing. Raises DeviceError outside the scope view."""
        with self.lock:
            try:
                if T.get_mode(self.ser) != 3:
                    raise DeviceError(-221, "AUToscale needs the scope view (use tc3_client.py goto scope)")
                T.press(self.ser, "select", settle=0.3)
                t0, last, stable_since = time.time(), None, time.time()
                while time.time() - t0 < timeout:
                    cur = (self._param("vdiv"), self._param("tdiv"))
                    if cur != last:
                        last, stable_since = cur, time.time()
                    elif time.time() - stable_since > 2.0:
                        return
                    time.sleep(0.2)
            except T.UiError as e:
                raise DeviceError(-221, f"autoscale: {e}")

    def gen_get(self):
        with self.lock:
            try:
                return T.gen_get(self.ser)
            except T.UiError as e:
                raise DeviceError(-314, str(e))

    def gen_set(self, **changes):
        """Change some generator fields (wave, freq, duty, amp); the others keep their value."""
        with self.lock:
            try:
                cur = T.gen_get(self.ser)
                cur.update(changes)
                T.gen_set(self.ser, cur["wave"], cur["freq"], cur["duty"], cur["amp"])
            except T.UiError as e:
                raise DeviceError(-221, f"generator: {e}")

    def ui(self, field, value, fallback_param=None):
        """Change a setting like a button press (0x55). On firmware without the UI hook, or when the device is not in
        the scope view, fall back to the raw parameter write (state changes, label may lag)."""
        with self.lock:
            try:
                T.ui_apply(self.ser, T.UI_FIELDS[field], value)
                return
            except T.UiError as e:
                err = e
        if fallback_param is None:
            raise DeviceError(-200, f"UI action {field}: {err}")
        self.set(fallback_param, value)

    def frame(self):
        """Latest ring frame as (header, samples)."""
        with self.lock:
            try:
                return T.fetch_frame(self.ser, 0)
            except T.FrameError as e:
                raise DeviceError(-230, f"no frame: {e}")

    def roll_window(self, n=ROLL_WINDOW):
        """Newest n roll-mode samples as (header, volts)."""
        with self.lock:
            try:
                return T.roll_last(self.ser, n)
            except T.RollError as e:
                raise DeviceError(-230, f"no roll data: {e}")

    def roll_header(self):
        with self.lock:
            try:
                return T.roll_header(self.ser)
            except T.RollError as e:
                raise DeviceError(-230, f"no roll data: {e}")

    def read_u32(self, addr):
        """One 32-bit word of device memory (extension 0x42)."""
        with self.lock:
            try:
                return struct.unpack("<I", T.read_mem(self.ser, addr, 4))[0]
            except T.Tc3Error as e:
                raise DeviceError(-314, str(e))

    def state(self, offset):
        """A 32-bit field of g_ScopeState (T.OFF_* offsets)."""
        return self.read_u32(T.SCOPE_STATE + offset)

    def latest_seq(self):
        with self.lock:
            try:
                return T.frame_header(self.ser, 0)["latest"]
            except T.FrameError:
                return 0


class DemoDevice(Device):
    """Synthetic scope for testing the SCPI/Rigol layers without hardware: a 1.5 V + 1 V sine,
    about 5 cycles per 600-sample frame, new frame ~15 times per second."""

    def __init__(self):
        self.lock = threading.Lock()
        self.params = {"mode": 3, "vdiv": 3, "probe": 1, "tdiv": 7, "run_stop": 0}
        self.fields = {T.OFF_COUPLING: 1, T.OFF_EDGE: 0, T.OFF_TRIGMODE: 0}
        self.t0 = time.time()
        self.gen = {"wave": 1, "freq": 1000, "duty": 50, "amp": 1.0}

    def get(self, name):
        return self.params[name]

    def set(self, name, value):
        self.params[name] = value

    def autoscale(self, timeout=15.0):
        self.params["vdiv"], self.params["tdiv"] = 3, 8      # what the real Auto settles on for 1 kHz, 3 V

    def state(self, offset):
        return self.fields[offset]

    def ui(self, field, value, fallback_param=None):
        offsets = {"coupling": T.OFF_COUPLING, "edge": T.OFF_EDGE, "trigmode": T.OFF_TRIGMODE}
        if field in offsets:
            self.fields[offsets[field]] = value
        else:
            self.params[{"runstop": "run_stop"}.get(field, field)] = value

    def gen_get(self):
        return dict(self.gen)

    def gen_set(self, **changes):
        self.gen.update(changes)

    def latest_seq(self):
        return int((time.time() - self.t0) * 15) + 1

    def _rate_ksps(self):
        t = self.params["tdiv"]
        return T.INTERLEAVED_RATE_KSPS.get(t, 25.0 / tdiv_seconds(t) / 1000.0)

    def _volt(self, i):
        return 1.5 + 1.0 * math.sin(2 * math.pi * 5 * i / 600.0)

    def frame(self):
        t, v, p = self.params["tdiv"], self.params["vdiv"], min(self.params["probe"], 1)
        gain = T.GAIN_BY_VDIV[v]
        seq = self.latest_seq()
        raws = [int(max(0, min(4095, round((self._volt(i) * 7.843137 / (gain * T.PROBE_MULT[p]) + 1.25) * 4095 / 2.5))))
                for i in range(600)]
        hdr = {"seq": seq, "latest": seq, "vdiv": v, "tdiv": t, "probe": p, "mode": 3, "gain": gain,
               "rate": self._rate_ksps(), "chunks": 20}
        return hdr, raws

    def roll_header(self):
        return {"rate": self._rate_ksps(), "tdiv": self.params["tdiv"], "vdiv": self.params["vdiv"],
                "probe": min(self.params["probe"], 1)}

    def roll_window(self, n=ROLL_WINDOW):
        hdr = self.roll_header()
        return hdr, [self._volt(i) for i in range(min(n, 400))]   # fewer than asked: exercises padding
