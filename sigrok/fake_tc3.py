#!/usr/bin/env python3
"""A fake DSO-TC3 (extension protocol) on a pseudo-terminal, for testing the libsigrok driver without hardware.

usage: sigrok/fake_tc3.py [--tdiv N] [--vdiv N] [--probe 0|1] [--speed X]
Prints the pty path; connect with  sigrok-cli -d fnirsi-dso-tc3:conn=<path> ...
Burst frames: a 1 kHz-ish sine on 1.5 V (50 samples per period), a new frame 15 times per second.
Roll: the same sine, one sample per T/div / 25 of simulated time (--speed multiplies the clock).
"""
import argparse, collections, math, os, struct, sys, termios, threading, time, tty

GAIN_BY_VDIV = [1, 1, 2, 4, 5, 48, 48, 96, 240, 480]
RING = 1024
ROLL_RATE_KSPS = {15: 0.25, 16: 0.125217, 17: 0.05, 18: 0.025, 19: 0.0125, 20: 0.005, 21: 0.0025}
ERR = lambda cmd, code: bytes([0x7E, cmd, code])
READ_MEM_MAX = 60                      # the stock 0x42 stub returns at most this many bytes
STATE_BASE = 0x20000668                # g_ScopeState; the fields below are offsets into it
STATE_OFFSETS = {"mode": 0x20, "pos": 0x24, "vdiv": 0x28, "probe": 0x2C, "tdiv": 0x30,
                 "dc": 0x34, "trigoff": 0x38, "trigmode": 0x3C, "edge": 0x40}


def tdiv_seconds(i):
    return (1, 2, 5)[i % 3] * 10.0 ** (-6 + i // 3)


def volts_at(i):
    return 1.5 + 1.0 * math.sin(2 * math.pi * i / 50.0)


def to_raw(volts, vdiv, probe):
    gain = GAIN_BY_VDIV[vdiv]
    raw = (volts * 7.843137 / (gain * (10 if probe else 1)) + 1.25) * 4095 / 2.5
    return max(0, min(4095, int(round(raw))))


class FakeTC3:
    def __init__(self, tdiv=8, vdiv=3, probe=1, speed=1.0):
        self.s = {"mode": 3, "vdiv": vdiv, "tdiv": tdiv, "probe": probe, "dc": 1, "edge": 0, "trigmode": 0, "pos": 35, "trigoff": 14}
        self.gen = {"wave": 1, "freq": 1000, "duty": 50, "amp": 3.0}
        self.speed = speed
        self.t0 = time.time()
        self.roll_base = 0
        self.epoch = 0
        self.lock = threading.Lock()
        self.ui_pending = None
        self.log = []
        self.count = collections.Counter()   # requests per command byte; "hdr_seq" = frame header requests for a given seq
        self.hangup = False                  # close the port at the next request, like an unplugged cable

    def now(self):
        return (time.time() - self.t0) * self.speed

    # ---- device state
    def roll_count(self):
        rate = ROLL_RATE_KSPS.get(self.s["tdiv"], 0.25) * 1000.0
        return int(self.now() * rate) + 1

    def frame_seq(self):
        return int((time.time() - self.t0) * 15) + 1

    def set_field(self, field, value):
        names = {1: "pos", 2: "vdiv", 3: "probe", 4: "tdiv", 5: "dc", 6: "trigoff", 7: "edge", 8: "trigmode"}
        if field not in names:
            return 7
        name = names[field]
        lim = {"pos": 99, "vdiv": 9, "probe": 1, "tdiv": 21, "dc": 1, "trigoff": 99, "edge": 1, "trigmode": 2}[name]
        if not (-lim if name in ("pos", "trigoff") else 0) <= value <= lim:
            return 9
        if name == "tdiv" and value != self.s["tdiv"]:
            self.roll_base = self.roll_count()
            self.epoch += 1
            self.s["tdiv"] = value
        else:
            self.s[name] = value
        self.log.append((name, value))
        return 0

    # ---- replies
    def frame_header(self, seq):
        latest = self.frame_seq()
        if seq == 0:
            seq = latest
        if latest - seq > 4:
            return ERR(0x54, 4)
        s = self.s
        rate = 25.0 / tdiv_seconds(s["tdiv"]) / 1000.0
        return (bytes([0x54, 0xFF, 0]) + struct.pack("<II", seq, latest)
                + bytes([s["vdiv"], s["tdiv"], s["probe"], 3])
                + struct.pack("<if", GAIN_BY_VDIV[s["vdiv"]], rate) + bytes([20]))

    def frame_chunk(self, chunk, seq):
        latest = self.frame_seq()
        if latest - seq > 4:
            return ERR(0x54, 4)
        if chunk >= 20:
            return ERR(0x54, 2)
        s = self.s
        vals = [to_raw(volts_at(chunk * 30 + i), s["vdiv"], s["probe"]) for i in range(30)]
        return bytes([0x54, chunk]) + struct.pack("<30H", *vals)

    def roll_header(self):
        s = self.s
        rate = ROLL_RATE_KSPS.get(s["tdiv"], 0.25)
        return (bytes([0x57, 0xFF, 0]) + struct.pack("<II", self.roll_count(), self.roll_base)
                + bytes([self.epoch & 255, s["tdiv"], s["vdiv"], s["probe"], 3])
                + struct.pack("<if", GAIN_BY_VDIV[s["vdiv"]], rate) + struct.pack("<H", RING))

    def roll_chunk(self, frm):
        count = self.roll_count()
        if frm < self.roll_base or count - frm > RING - 2:
            return ERR(0x57, 4)
        n = max(0, min(29, count - frm))
        s = self.s
        vals = [to_raw(volts_at(frm + i), s["vdiv"], s["probe"]) | (s["vdiv"] << 12) for i in range(n)]
        return bytes([0x57, 1, n]) + struct.pack("<%dH" % n, *vals)

    def mem(self, addr, n):
        """Bytes of the device RAM image (only g_ScopeState is modelled; the rest reads as zero)."""
        img = bytearray(0x100)
        for name, off in STATE_OFFSETS.items():
            struct.pack_into("<i", img, off, self.s[name])
        lo = addr - STATE_BASE
        return bytes(img[lo:lo + n]) if 0 <= lo and lo + n <= len(img) else bytes(n)

    def handle(self, buf):
        """Consume one request from buf; returns (reply, rest) or None when the request is incomplete."""
        if not buf:
            return None
        c = buf[0]
        need = {0x5F: 1, 0x51: 2, 0x42: 7, 0x55: 6, 0x54: 6, 0x57: 6}.get(c)
        if c == 0x58:
            need = 2 if len(buf) < 2 or buf[1] != 1 else 12
        if c == 0x55 and len(buf) >= 2 and buf[1] == 0xFF:
            need = 2                                    # the poll request is two bytes
        if need is None:
            return b"", buf[1:]
        if len(buf) < need:
            return None
        req, rest = buf[:need], buf[need:]
        with self.lock:
            self.count[c] += 1
            if c == 0x54 and req[1] == 0xFF and struct.unpack_from("<I", req, 2)[0]:
                self.count["hdr_seq"] += 1
            return self.reply(req), rest

    def reply(self, r):
        c, s = r[0], self.s
        if c == 0x5F:
            return bytes([0x5F, 1, 0, 0]) + b"TC3-FAKE 1.0"      # as long as the banner: the real firmware does not pad
        if c == 0x51:
            names = {1: "mode", 2: "vdiv", 3: "probe", 4: "tdiv", 5: "dc"}
            return bytes([0x51, r[1]]) + struct.pack("<I", s.get(names.get(r[1]), 0))
        if c == 0x42:
            addr, n = struct.unpack_from("<IH", r, 1)
            return bytes([0x43]) + self.mem(addr, n) if n <= READ_MEM_MAX else b""
        if c == 0x55:
            if r[1] == 0xFF:
                return bytes([0x55, 0xFF, 2, 0]) + bytes(5)
            res = self.set_field(r[1], struct.unpack_from("<i", r, 2)[0])
            return bytes([0x55, r[1], res])
        if c == 0x54:
            chunk, seq = r[1], struct.unpack_from("<I", r, 2)[0]
            return self.frame_header(seq) if chunk == 0xFF else self.frame_chunk(chunk, seq)
        if c == 0x57:
            frm = struct.unpack_from("<I", r, 2)[0]
            return self.roll_header() if r[1] == 0xFF else self.roll_chunk(frm) if r[1] == 1 else ERR(0x57, 2)
        if c == 0x58:
            g = self.gen
            if r[1] == 1:
                wave, freq, duty, amp = struct.unpack_from("<BIBf", r, 2)
                if wave > 5 or not 1 <= freq <= 100000 or duty > 100 or not 0.01 <= amp <= 3.3:
                    return bytes([0x58, 1, 9])
                self.gen = {"wave": wave, "freq": freq, "duty": duty, "amp": amp}
                self.log.append(("gen", dict(self.gen)))
                return bytes([0x58, 1, 0])
            return bytes([0x58, 0, 2, 0, g["wave"]]) + struct.pack("<IBf", g["freq"], g["duty"], g["amp"])
        return b""


def serve(dev, fd):
    buf = b""
    while True:
        try:
            data = os.read(fd, 4096)
        except OSError:
            return
        if dev.hangup:
            os.close(fd)
            return
        buf += data
        while True:
            out = dev.handle(buf)
            if out is None:
                break
            reply, buf = out
            if reply:
                os.write(fd, reply)


def open_pty(dev):
    master, slave = os.openpty()
    tty.setraw(slave)
    attrs = termios.tcgetattr(slave)
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    threading.Thread(target=serve, args=(dev, master), daemon=True).start()
    return os.ttyname(slave), slave


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tdiv", type=int, default=8)
    ap.add_argument("--vdiv", type=int, default=3)
    ap.add_argument("--probe", type=int, default=1)
    ap.add_argument("--speed", type=float, default=1.0)
    a = ap.parse_args()
    dev = FakeTC3(a.tdiv, a.vdiv, a.probe, a.speed)
    path, _slave = open_pty(dev)
    print(path, flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
