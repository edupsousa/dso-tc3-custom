#!/usr/bin/env python3
"""Dump flash/memory from a DSO-TC3 running the custom extension firmware.

Protocol:
  request : 0x42 | addr LE32 | n LE16          (1 <= n <= 60)
  reply   : 0x43 | n raw bytes read from addr
Everything else passes to the stock vendor console unchanged.

Device flash map:
  0x08000000-0x08003FFF  vendor bootloader (update protocol: Cmd_Beat/Cmd_Run,
                         magic + FileSize + per-packet checksum, XOR-0x7f writer)
  0x08004000-0x0805D54B  application; VTOR = 0x08004000. NOTE: the update file's
                         32-byte header and the payload's first 32 bytes are NOT
                         written; file[0x40:] lands at 0x08004000. Static-analysis
                         addresses (file offset k -> 0x08000000+k) are 0x3FE0 below
                         the real device addresses.
  0x0805D2B4-0x08075DDB  boot copy table + resource partition (blobs at
                         0x0805D2D4 -> 0x20000000 and 0x0805D54C -> 0x20000668)
  0x08075DDC-0x0807EFFF  erased (verified 0xFF)
  0x0807F000-0x0807FFFF  config/calibration (magic 0x12345678, 0x90-byte record)

CAUTION: reads of addresses outside the verified ranges above (and known
peripheral/core registers) can still bus-fault the CPU (recoverable by
power-cycle + stock re-flash, but avoid it).

Throughput note (2026-10-25, A3): SRAM reads sustain ~360 KB/s unpaced
(0.23 ms per 60-byte request). Long FLASH reads at full rate can wedge the
device (flash-bus contention starves the USB stack; observed once at ~8.7k
requests: USB disconnect + shutdown). Use --delay 0.0005 for long flash
reads (~191 KB/s, stable).

Examples:
  python3 firmware_ext/tools/dump_flash.py --preset config      -o config.bin
  python3 firmware_ext/tools/dump_flash.py --preset bootloader  -o bootloader.bin
  python3 firmware_ext/tools/dump_flash.py --preset app         -o app.bin
  python3 firmware_ext/tools/dump_flash.py --preset resources   -o resources.bin
  python3 firmware_ext/tools/dump_flash.py --vtor
  python3 firmware_ext/tools/dump_flash.py --addr 0x0807F000 --len 4096 -o config.bin
"""
import argparse, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "host"))
from tc3 import Tc3Error, find_device_port, read_mem, READ_MEM_MAX as CHUNK

PRESETS = {
    "bootloader": (0x08000000, 0x08004000),  # vendor bootloader (update protocol)
    "app":        (0x08004000, 0x0805D54C),  # application, as written by the update
    "resources":  (0x0805D2B4, 0x08075DDC),  # copy table + resource partition
    "config":     (0x0807F000, 0x08080000),  # config/calibration sector (4 KB)
}


def dump(ser, addr, length, outfile, delay=0.0):
    ser.timeout = 2.0
    buf = bytearray()
    t0 = time.time()
    na = addr + length
    pos = addr
    while pos < na:
        n = min(CHUNK, na - pos)
        try:
            chunk = read_mem(ser, pos, n)
        except Tc3Error as e:
            raise RuntimeError(f"bad/short reply at 0x{pos:08x}: {e}")
        buf += chunk
        pos += n
        if delay:
            time.sleep(delay)
        done = (pos - addr) / length
        print(f"\r  0x{pos:08x} / 0x{na:08x} ({done:5.1%})", end="", flush=True)
    print(f"\n  {length} bytes in {time.time()-t0:.1f}s")
    os.makedirs(os.path.dirname(os.path.abspath(outfile)), exist_ok=True)
    with open(outfile, "wb") as f:
        f.write(bytes(buf))
    print(f"  wrote {outfile}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", help="serial device (default: auto-detect 19f5:5740 CDC)")
    ap.add_argument("--preset", choices=sorted(PRESETS))
    ap.add_argument("--addr", type=lambda x: int(x, 0))
    ap.add_argument("--len", type=lambda x: int(x, 0), default=0)
    ap.add_argument("--vtor", action="store_true", help="read VTOR (0xE000ED08)")
    ap.add_argument("--delay", type=float, default=0.0,
                    help="seconds to sleep after each 60-byte request "
                         "(use 0.0005 for long FLASH reads, see docstring)")
    ap.add_argument("-o", "--out", default=None)
    a = ap.parse_args()

    if a.vtor:
        a.addr, a.len, a.out = 0xE000ED08, 4, a.out or "vtor.bin"
    elif a.preset:
        a.addr, end = PRESETS[a.preset]
        a.len = a.len or (end - a.addr)
        a.out = a.out or f"{a.preset}.bin"
    if not a.addr or not a.len:
        ap.error("need --preset / --addr+--len / --vtor")

    port = a.port or find_device_port()
    if not port:
        sys.exit("CDC port not found (is the device connected and running the custom firmware?)")
    print(f"opening {port}")

    import serial
    with serial.Serial(port, 115200, timeout=2) as ser:
        time.sleep(0.2)
        dump(ser, a.addr, a.len, a.out, delay=a.delay)

    if a.vtor:
        v = int.from_bytes(open(a.out, "rb").read(), "little")
        print(f"  VTOR = 0x{v:08x}")


if __name__ == "__main__":
    main()
