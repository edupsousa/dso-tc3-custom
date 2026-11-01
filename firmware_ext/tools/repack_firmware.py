#!/usr/bin/env python3
"""Pack/unpack FNIRSI DSO-TC3 firmware files.

File layout (see docs/firmware-decryption.md):

  bytes 0x00..0x1F   vendor header = first update packet (never written to
                     flash). The bootloader's update dispatcher parses it:
                       key   = h[0] ^ h[1] ^ h[0x1E] ^ h[0x1F]
                       magic = h[2..5] ^ key   must equal 6f 29 4c 64
                       size  = h[6..9] as uint32 LE -- display only
                         ("Sys.State.FileSize:%d" progress divisor; stock
                         value is the placeholder 0x2C2C2C2C)
  bytes 0x20..end    flash payload, each byte XOR key -- streams to flash

Wire protocol (host -> bootloader):
  [cmd][idx_lo][idx_hi][len][payload(len)][checksum]
  checksum = (sum of all preceding bytes) & 0xFF, computed by the sender.
  cmd 0x00 heartbeat / 0x10 data / 0x20 run-app.
  Frame idx0 = header (32 B), idx1 acked but NOT written (payload bytes
  0x00..0x1F are skipped), idx>=2 written at 0x08004000 + idx*32 - 0x40,
  i.e. file[0x40 + j] lands at 0x08004000 + j.
  Erase: before writing a frame, if (received & 0x400) == 0 the bootloader
  erases the 2 KB page containing 0x08004000 + received.

Flash regions:
  0x08000000-0x08003FFF  bootloader (survives updates; never in the file)
  0x08004000-0x0805D54B  application  = update payload bytes 0x20..end
  0x0805D54C-0x08075DDB  erased flash (0xFF) / available for extension
  0x08075DDC-0x0807EFFF  erased flash
  0x0807F000-0x0807FFFF  configuration/calibration -- DO NOT TOUCH

Usage:
  python3 repack_firmware.py unpack IN.s19 OUT.flash.bin
  python3 repack_firmware.py pack IN.flash.bin OUT.s19 [--header SRC]
  python3 repack_firmware.py grow IN.s19 OUT.s19 --extra N [--ff]
  python3 repack_firmware.py verify IN.s19 [--stock STOCK.s19]
  python3 repack_firmware.py bounds PAYLOAD_LEN
"""
import argparse
import struct
import sys

HDR = 0x20
MAGIC = bytes([0x6F, 0x29, 0x4C, 0x64])     # expected h[2..5] ^ key
STOCK_KEY = 0x7F
# stock header: d7 86 10 56 33 1b, then 0x2c x24, then e6 c8
STOCK_H0, STOCK_H1, STOCK_HE, STOCK_HF = 0xD7, 0x86, 0xE6, 0xC8
STOCK_FILLER = 0x2C

# flash layout (device addresses)
FLASH_APP = 0x08004000
APP_WRITE_END = 0x0805D54B       # last byte the stock-length payload writes
RES_NOMINAL_START = 0x0805D54C   # copy-table "resources" source
RES_ERASED_TO = 0x0807F000       # verified 0xFF up to here on the reference unit
CONFIG = 0x0807F000
FLASH_END = 0x08080000
PAGE = 0x800                     # 2 KB erase pages


def derive_key(header):
    return header[0] ^ header[1] ^ header[0x1E] ^ header[0x1F]


def build_header(key, size_word=None):
    """Build a 32-byte header that the bootloader will accept.

    key: payload XOR key. h[0], h[0x1E], h[0x1F] keep the stock values and
    h[1] is solved so the derived key matches. h[2..5] = MAGIC ^ key.
    size_word: None keeps the stock placeholder bytes (h[6..9] = 2c 2c 2c 2c);
    an int is stored little-endian and is display-only (progress divisor).
    """
    h = bytearray([STOCK_FILLER] * HDR)
    h[0] = STOCK_H0
    h[1] = key ^ STOCK_H0 ^ STOCK_HE ^ STOCK_HF
    h[0x1E], h[0x1F] = STOCK_HE, STOCK_HF
    h[2:6] = bytes(m ^ key for m in MAGIC)
    if size_word is not None:
        h[6:10] = struct.pack('<I', size_word & 0xFFFFFFFF)
    return bytes(h)


def split_update_file(data):
    if len(data) < HDR:
        raise SystemExit("file shorter than the 32-byte header")
    return data[:HDR], data[HDR:]


def check_payload(payload):
    """Sanity-check a decrypted flash image; returns list of notes."""
    notes = []
    if len(payload) < 0x40:
        notes.append("payload too small to contain a vector table")
        return notes
    sp, reset = struct.unpack_from('<II', payload, 0)
    if 0x20000000 <= sp < 0x20020000:
        notes.append("SP   0x%08x  (plausible RAM top)" % sp)
    else:
        notes.append("SP   0x%08x  (UNEXPECTED)" % sp)
    if 0x08000000 <= reset < 0x08080000 and reset & 1:
        notes.append("Reset 0x%08x  (Thumb, plausible)" % reset)
    else:
        notes.append("Reset 0x%08x  (UNEXPECTED)" % reset)
    # each 8-vector set ends with one reserved zero slot (word 7);
    # set 1 at payload 0x00 (not written), main table at payload 0x20
    for off, what in ((0x1C, "dup-set"), (0x3C, "main table")):
        v = struct.unpack_from('<I', payload, off)[0]
        notes.append("%s reserved slot %s" % (what, "zero: ok" if v == 0
                                              else "0x%08x (UNEXPECTED)" % v))
    return notes


def bounds_report(payload_len, out=print):
    """Which flash regions a payload of this length would touch."""
    end = FLASH_APP + payload_len - 1     # last written byte
    first_page = FLASH_APP
    last_page = (end // PAGE) * PAGE
    out("payload %d bytes -> writes 0x%08x..0x%08x" %
        (payload_len, FLASH_APP, end))
    out("erased 2KB pages: 0x%08x .. 0x%08x (%d pages)" %
        (first_page, last_page, (last_page - first_page) // PAGE + 1))
    verdicts = []
    if end <= APP_WRITE_END:
        verdicts.append("inside the stock app window -- same bounds as stock")
    else:
        over = end - APP_WRITE_END
        verdicts.append("OVERSHOOT: +0x%x bytes past the stock app end "
                        "(0x%08x)" % (over, APP_WRITE_END))
        if end < CONFIG:
            verdicts.append(
                "overshoot lands in the nominal resource partition, which is "
                "VERIFIED ERASED (0xFF) up to 0x0807F000 on the reference "
                "unit; appending 0xFF plaintext keeps flash content "
                "identical")
        else:
            verdicts.append("!!! reaches the configuration sector at "
                            "0x0807F000 -- calibration loss; do not flash")
    if end >= FLASH_END:
        verdicts.append("!!! beyond the 512 KB flash end -- impossible wrap")
    for v in verdicts:
        out("  - " + v)
    return end <= APP_WRITE_END or end < CONFIG


def cmd_unpack(a):
    data = open(a.infile, "rb").read()
    header, payload_enc = split_update_file(data)
    key = derive_key(header)
    magic_ok = bytes(b ^ key for b in header[2:6]) == MAGIC
    size = struct.unpack_from('<I', header, 6)[0]
    payload = bytes(b ^ key for b in payload_enc)
    open(a.outfile, "wb").write(payload)
    print("header: key=0x%02x magic_ok=%s size_word=%d (0x%08x)"
          % (key, magic_ok, size, size))
    for line in check_payload(payload):
        print("  " + line)
    print("unpacked %d B payload -> %s" % (len(payload), a.outfile))


def cmd_pack(a):
    payload = open(a.infile, "rb").read()
    key = int(a.xor_key, 0) if a.xor_key else STOCK_KEY
    size_word = None
    if a.size_word == "auto":
        size_word = len(payload)
    elif a.size_word is not None:
        size_word = int(a.size_word, 0)
    if a.header:
        header = open(a.header, "rb").read()[:HDR]
        if key != STOCK_KEY:
            raise SystemExit("--xor-key requires header synthesis (drop --header)")
    else:
        header = build_header(key, size_word)
    out = header + bytes(b ^ key for b in payload)
    open(a.outfile, "wb").write(out)
    print("packed %d B payload -> %s (%d B total, key=0x%02x, size_word=%s)"
          % (len(payload), a.outfile, len(out), key,
             "stock placeholder" if size_word is None else size_word))
    bounds_report(len(payload) - 0x20)   # first 32 payload bytes are skipped


def cmd_grow(a):
    """Build the C1-style artifact: stock file + appended payload bytes.

    Appended plaintext defaults to 0xFF so the flash content stays identical
    even where new 2KB pages are erased and rewritten.
    """
    data = open(a.infile, "rb").read()
    header, payload_enc = split_update_file(data)
    key = derive_key(header)
    fill = 0xFF ^ key                     # decrypts back to 0xFF on device
    payload_enc = payload_enc + bytes([fill]) * a.extra
    if a.size_word:
        sw = struct.pack('<I', len(payload_enc) & 0xFFFFFFFF)
        header = header[:6] + sw + header[10:]
    out = header + payload_enc
    open(a.outfile, "wb").write(out)
    print("grew %s -> %s: +0x%x bytes (plaintext 0xFF), total %d B "
          "(payload %d B)" % (a.infile, a.outfile, a.extra, len(out),
                              len(payload_enc)))
    bounds_report(len(payload_enc) - 0x20)


def cmd_verify(a):
    data = open(a.infile, "rb").read()
    header, payload_enc = split_update_file(data)
    key = derive_key(header)
    magic_ok = bytes(b ^ key for b in header[2:6]) == MAGIC
    size = struct.unpack_from('<I', header, 6)[0]
    print("file %s: %d B (payload %d B)" % (a.infile, len(data), len(payload_enc)))
    print("header: key=0x%02x  magic_ok=%s  size_word=%d (0x%08x)"
          % (key, magic_ok, size, size))
    payload = bytes(b ^ key for b in payload_enc)
    for line in check_payload(payload):
        print("  " + line)
    if a.stock:
        stock = open(a.stock, "rb").read()
        shdr, spay = split_update_file(stock)
        n = min(len(spay), len(payload_enc))
        diff = [i for i in range(n) if spay[i] != payload_enc[i]]
        print("vs stock: header identical=%s; %d payload bytes differ"
              % (header == shdr, len(diff)))
        if diff:
            print("  first diffs at payload offsets: %s"
                  % [hex(x) for x in diff[:8]])
        if len(payload_enc) > len(spay):
            extra = payload_enc[len(spay):]
            print("  appended 0x%x bytes, plaintext %s"
                  % (len(extra), "all 0xFF (content-identical on flash)"
                     if all(b == 0xFF for b in
                            bytes(b ^ key for b in extra)) else "NOT all 0xFF"))
    ok = bounds_report(len(payload_enc) - 0x20)
    sys.exit(0 if (magic_ok and ok) else 1)


def cmd_bounds(a):
    bounds_report(a.length)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("unpack", help="update file -> decrypted flash image")
    p.add_argument("infile"); p.add_argument("outfile"); p.set_defaults(fn=cmd_unpack)
    p = sub.add_parser("pack", help="flash image -> update file")
    p.add_argument("infile"); p.add_argument("outfile")
    p.add_argument("--header", help="donate 32-byte header from this file")
    p.add_argument("--xor-key", help="payload XOR key (default 0x7f); synthesizes the header")
    p.add_argument("--size-word", help="h[6..9] uint32 (display-only); 'auto' = payload length")
    p.set_defaults(fn=cmd_pack)
    p = sub.add_parser("grow", help="append safe filler to an update file (C1 artifact)")
    p.add_argument("infile"); p.add_argument("outfile")
    p.add_argument("--extra", type=lambda x: int(x, 0), required=True,
                   help="bytes to append (0xFF plaintext by construction)")
    p.add_argument("--size-word", action="store_true",
                   help="also set the display-only size word to the new length")
    p.set_defaults(fn=cmd_grow)
    p = sub.add_parser("verify", help="decode + validate an update file")
    p.add_argument("infile")
    p.add_argument("--stock", help="diff against the stock update file")
    p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("bounds", help="flash-window report for a payload length")
    p.add_argument("length", type=lambda x: int(x, 0))
    p.set_defaults(fn=cmd_bounds)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
