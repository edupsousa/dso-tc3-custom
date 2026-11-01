#!/usr/bin/env python3
"""FNIRSI DSO-TC3 bootloader flasher -- speaks the vendor update protocol.

The device must be in update mode: hold the DOWN (▼) key and press POWER.
The bootloader then enumerates as a USB CDC serial port (VID:PID 19f5:5740).

Protocol summary:
  Frame format (host -> device):
    [cmd][idx_lo][idx_hi][len][payload(len)][checksum]
    checksum = (byte-sum of all preceding bytes in frame) & 0xFF
    cmd 0x00 heartbeat -> acked with cmd 0x01 (5 bytes)
    cmd 0x10 data      -> acked with cmd 0x11 (5 bytes: [0x11, idx_lo, idx_hi, 0x00, checksum])
        idx 0 = 32-byte header: host MUST overwrite bytes 6..9 with len(file) uint32 LE
        idx 1 = acked but not written (file bytes 0x20..0x3F are skipped)
        idx >= 2 -> written at 0x08004000 + (idx - 2) * 32
    cmd 0x20 run-app   -> boots into app mode (device resets immediately)

  Erase gate in bootloader (0x080022bc):
    `ldr r0, [r5, #4]; lsls r1, r0, #0x15; bne 0x080022d6`
    Tests `(counter << 21) == 0`, which is true IF AND ONLY IF `counter % 2048 == 0`.
    The bootloader erases each 2 KB page exactly once at the page boundary.
    UART logs (erase:... / Sys.State.FileSize) go to USART1 at 600,000 baud (J4 pin 2).

Usage:
  python3 firmware_ext/tools/flash_update.py --wait FILE.s19              # flash and run
  python3 firmware_ext/tools/flash_update.py --wait --dry FILE.s19        # test header & idx1 only
  python3 firmware_ext/tools/flash_update.py --heartbeat                  # test update connection
"""
import argparse
import os
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "host"))
from tc3.link import candidate_ports, serial as serial_module

CMD_HB, CMD_DATA, CMD_RUN = 0x00, 0x10, 0x20
ACK_HB, ACK_DATA = 0x01, 0x11
CHUNK = 32
DEFAULT_DELAY = 0.003   # 3 ms pacing per packet (~5 ms vendor cadence)


def wait_for_port(timeout=60, known=()):
    print("waiting for the update-mode CDC port (hold DOWN, press POWER)...")
    t0 = time.time()
    while time.time() - t0 < timeout:
        for p in candidate_ports():
            if p not in known:
                print("  found port: %s" % p)
                return p
        time.sleep(0.5)
    sys.exit("timeout waiting for an update-mode port")


def open_port(dev):
    # Match vendor tool: 12 MBaud (CDC full-speed), 1s timeout
    ser = serial_module.Serial(dev, 12000000, timeout=1.0)
    time.sleep(0.1)
    return ser


class Link:
    """Handles USB CDC packet framing and 5-byte ACK verification."""

    def __init__(self, ser, delay=DEFAULT_DELAY):
        self.ser = ser
        self.delay = delay

    @staticmethod
    def checksum(frame_bytes):
        return sum(frame_bytes) & 0xFF

    def send_frame(self, cmd, idx, payload, ack_cmd, timeout=3.0, retries=2):
        frame = bytearray([cmd, idx & 0xFF, (idx >> 8) & 0xFF, len(payload)])
        frame.extend(payload)
        frame.append(self.checksum(frame))

        for attempt in range(retries + 1):
            try:
                self.ser.write(frame)
            except serial_module.SerialException as e:
                raise RuntimeError(
                    f"USB link lost while sending frame idx={idx} ({e})."
                ) from e

            # Vendor protocol: device replies with exactly 5 binary bytes
            # [ack_cmd, idx_lo, idx_hi, 0x00, checksum]
            deadline = time.time() + timeout
            resp = bytearray()
            while len(resp) < 5 and time.time() < deadline:
                chunk = self.ser.read(5 - len(resp))
                if chunk:
                    resp.extend(chunk)

            if len(resp) == 5:
                expected_csum = sum(resp[:4]) & 0xFF
                ack_idx = resp[1] | (resp[2] << 8)
                if resp[0] == ack_cmd and ack_idx == idx and resp[4] == expected_csum:
                    if self.delay:
                        time.sleep(self.delay)
                    return True
                else:
                    print(f"  warn: bad ack on idx={idx}: "
                          f"cmd=0x{resp[0]:02x} (want 0x{ack_cmd:02x}), "
                          f"idx={ack_idx} (want {idx}), "
                          f"csum=0x{resp[4]:02x} (want 0x{expected_csum:02x})")
            else:
                if attempt < retries:
                    print(f"  warn: timeout on idx={idx} (got {len(resp)}/5 bytes), retrying...")

        return False

    def heartbeat(self):
        # Heartbeat: cmd 0x00, idx 0, payload empty -> ack 0x01
        return self.send_frame(CMD_HB, 0, b"", ACK_HB, timeout=1.0)

    def run_app(self, last_idx=0):
        # Bootloader resets immediately; no ACK expected
        frame = bytearray([CMD_RUN, last_idx & 0xFF, (last_idx >> 8) & 0xFF, 0])
        frame.append(self.checksum(frame))
        try:
            self.ser.write(frame)
        except Exception:
            pass


def load_update(path):
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 64:
        sys.exit(f"Error: file {path} too small ({len(data)} bytes)")

    total_len = len(data)
    hdr = bytearray(data[:32])

    # Check key and magic
    key = hdr[0] ^ hdr[1] ^ hdr[0x1E] ^ hdr[0x1F]
    magic = bytes(b ^ key for b in hdr[2:6])
    if magic != bytes([0x6F, 0x29, 0x4C, 0x64]):
        sys.exit(f"Error: invalid firmware magic in {path} (got {magic.hex()})")

    file_size_in_hdr = struct.unpack('<I', hdr[6:10])[0]
    print(f"File {path}: {total_len} bytes")
    print(f"Header: key=0x{key:02x}, magic=ok, original size_word={file_size_in_hdr}")
    print(f"Auto-updating header FileSize field to {total_len} (vendor tool requirement)")

    # Vendor tool requirement: overwrite header bytes 6..9 with len(file) uint32 LE
    hdr[6:10] = struct.pack('<I', total_len)

    # Prepare 32-byte chunks
    num_packets = (total_len + CHUNK - 1) // CHUNK
    chunks = []
    # Packet 0: updated header
    chunks.append(bytes(hdr))
    # Packets 1..N-1: payload from file
    for k in range(1, num_packets):
        chunks.append(data[k * CHUNK : (k + 1) * CHUNK])

    return chunks, total_len


def flash(link, chunks, total_len, run=True, dry=False):
    t0 = time.time()
    n = len(chunks)

    # Frame 0: Header (parsed by bootloader, counter initialized to 0)
    if not link.send_frame(CMD_DATA, 0, chunks[0], ACK_DATA):
        sys.exit("ABORT: header frame idx=0 not acknowledged")
    print("  idx=0 (header) acknowledged")

    # Frame 1: Handshake frame (acked by bootloader, never written to flash)
    if not link.send_frame(CMD_DATA, 1, chunks[1], ACK_DATA):
        sys.exit("ABORT: frame idx=1 not acknowledged")
    print("  idx=1 (handshake) acknowledged")

    if dry:
        print("Dry run requested: stopping before any flash write.")
        return

    # Frames 2..N-1: Flash data writes
    print(f"Flashing {n - 2} data frames ({total_len - 64} bytes)...")
    for idx in range(2, n):
        chunk = chunks[idx]
        # At each 2 KB boundary ((idx - 2) * 32 % 2048 == 0), the bootloader erases a page.
        # Allow 5.0s timeout on erase frames.
        is_erase_page = ((idx - 2) * CHUNK) % 2048 == 0
        timeout = 5.0 if is_erase_page else 2.0

        if not link.send_frame(CMD_DATA, idx, chunk, ACK_DATA, timeout=timeout):
            sys.exit(f"\nABORT: frame idx={idx} not acknowledged. Power-cycle device and re-flash stock.")

        if (idx - 2) % 256 == 0 or idx == n - 1:
            written = (idx - 1) * CHUNK
            pct = min(100.0, (written / total_len) * 100.0)
            elapsed = time.time() - t0
            print(f"\r  Progress: {pct:5.1f}% [{idx}/{n} frames] ({written/1024:.1f} KB) in {elapsed:.1f}s",
                  end="", flush=True)

    elapsed = time.time() - t0
    rate = (total_len / 1024.0) / elapsed if elapsed > 0 else 0
    print(f"\nFlash completed successfully in {elapsed:.1f}s ({rate:.1f} KB/s)!")

    if run:
        print("Sending run-app command (0x20)...")
        link.run_app(last_idx=n)
        print("Device rebooting into application mode.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", help="update file (.s19 layout)")
    ap.add_argument("--port", help="serial device (default: auto)")
    ap.add_argument("--wait", action="store_true",
                    help="wait for update-mode port to appear")
    ap.add_argument("--heartbeat", action="store_true",
                    help="test connection with heartbeat ping")
    ap.add_argument("--dry", action="store_true",
                    help="validate up to idx=1 without writing to flash")
    ap.add_argument("--no-run", action="store_true",
                    help="stay in update mode after flashing")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                    help=f"delay in seconds between packets (default: {DEFAULT_DELAY}s)")
    a = ap.parse_args()

    known = set(candidate_ports())
    port = a.port
    if a.wait and not port:
        port = wait_for_port(known=known)
    elif not port:
        ports = candidate_ports()
        if not ports:
            sys.exit("No CDC port found. Hold DOWN (▼) and press POWER to enter update mode.")
        port = ports[0]

    print(f"Opening port {port}...")
    ser = open_port(port)
    link = Link(ser, delay=a.delay)

    try:
        if a.heartbeat:
            print("Sending heartbeat ping...")
            ok = link.heartbeat()
            print("Heartbeat:", "ACKNOWLEDGED (0x01)" if ok else "NO REPLY")
            return

        if not a.file:
            sys.exit("Please specify a firmware file to flash.")

        chunks, total_len = load_update(a.file)

        print("Testing link with heartbeat...")
        if not link.heartbeat():
            sys.exit("ABORT: Bootloader did not reply to heartbeat ping.")
        print("Heartbeat verified.")

        flash(link, chunks, total_len, run=not a.no_run, dry=a.dry)
    finally:
        ser.close()


if __name__ == "__main__":
    main()
