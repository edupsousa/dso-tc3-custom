"""Serial link: finding the device and the request/reply patterns every command shares."""
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial required: pip install pyserial")

from .wire import *


class Tc3Error(Exception):
    """A request failed. `code` is the 0x7e error code, TIMEOUT, or None (bad reply, refusal)."""
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


class UiError(Tc3Error):
    pass


class FrameError(Tc3Error):
    pass


class RollError(Tc3Error):
    pass


def find_device_port():
    for p in list_ports.comports():
        if p.vid == VID and p.pid == PID:
            return p.device
    return None


def candidate_ports():
    """Serial ports, the device's (VID:PID) first."""
    ports = list_ports.comports()
    pref = [p.device for p in ports if p.vid == VID and p.pid == PID]
    return pref + [p.device for p in ports if p.device not in pref]


def open_conn(port=None, timeout=1.0):
    p = port or find_device_port()
    if not p:
        sys.exit(f"No DSO-TC3 device found (VID:PID {VID:04x}:{PID:04x}). Connect USB and power on.")
    return serial.Serial(p, baudrate=115200, timeout=timeout)


def request(ser, tx, cmd, size, errors, exc=Tc3Error):
    """Send `tx`, return the first `size` bytes of the reply to `cmd`. An error reply is 3 bytes (0x7e cmd code), not
    `size`, so the first byte is read alone. Raises `exc` on an error reply, a timeout or a short or foreign reply."""
    ser.reset_input_buffer()
    ser.write(tx)
    first = ser.read(1)
    if not first:
        raise exc("timeout", TIMEOUT)
    if first[0] == RESP_ERROR:
        rest = ser.read(2)
        code = rest[1] if len(rest) == 2 else -1
        raise exc(errors.get(code, f"error {code}"), code)
    resp = first + ser.read(size - 1)
    if len(resp) != size or resp[0] != cmd:
        raise exc(f"bad reply {resp.hex()}")
    return resp


def deferred(ser, tx, cmd, poll_tx, poll_size, errors=UI_RESULTS, timeout=2.0, what="UI request"):
    """A deferred action: the request is acknowledged at once (byte 2 = result), the main loop runs it later; poll until the
    state byte (2) is DONE, then check the result byte (3). Raises UiError."""
    ser.reset_input_buffer()
    ser.write(tx)
    r = ser.read(3)
    if len(r) != 3 or r[0] != cmd:
        raise UiError(f"device did not acknowledge the {what} (firmware without 0x{cmd:02x}?)")
    if r[2] != 0:
        raise UiError(errors.get(r[2], f"error {r[2]}"))
    t0 = time.time()
    while time.time() - t0 < timeout:
        ser.write(poll_tx)
        r = ser.read(poll_size)
        if len(r) == poll_size and r[0] == cmd and r[2] == ACTION_DONE:
            if r[3] != 0:
                raise UiError(errors.get(r[3], f"error {r[3]}"))
            return r
        time.sleep(0.005)
    raise UiError("timed out waiting for the main loop (is the device blocked?)")
