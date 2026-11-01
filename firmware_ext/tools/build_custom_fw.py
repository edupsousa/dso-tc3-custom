#!/usr/bin/env python3
"""Build and repack the DSO-TC3 Custom Firmware with Phase 2 Extension.

Combines:
  1. Base dump-firmware (stock + gate stub at 0x0800f490)
  2. The hooks listed in HOOKS below, each checked against the stock bytes it replaces
     (vector entries, `bl` call sites, the trampoline into extension_dispatch)
  3. Padding (0xFF) up to 0x0805E000
  4. Extension binary compiled from firmware_ext/
  5. Repacks into .s19 update file with valid header and XOR encryption

Run from anywhere: paths are relative to the repository root.
"""

import argparse
import os
import shutil
import subprocess
import sys

import keystone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)

EXT_DIR = "firmware_ext"
EXT_BIN = "firmware_ext/build/extension.bin"
EXT_ELF = "firmware_ext/build/extension.elf"
DEFAULT_OUT_FLASH_BIN = "DSO-TC3_custom-ext.flash.bin"
DEFAULT_OUT_S19 = "DSO-TC3_custom-ext.s19"

FLASH_APP_START = 0x08004000
FLASH_EXT_START = 0x0805E000
FLASH_EXT_SIZE = 0x20000
MAX_FLASH_END   = 0x0807F000          # Configuration sector (calibration) boundary
PAYLOAD_HEADER = 0x20                 # the payload file starts 0x20 bytes before FLASH_APP_START

GATE_STUB_BYTES = bytes.fromhex(
    "70b50278422a23d1072921d3432202704378827843ea0223c27843ea0243027943ea02644679827956ea0226"
    "06b901263c2e00d93c26421c35462378137001340132013df9d1711cbde87040f5f73cbebde8704004f010bd"
)

_ks = keystone.Ks(keystone.KS_ARCH_ARM, keystone.KS_MODE_THUMB)
_nm_path = None


def get_nm():
    global _nm_path
    if not _nm_path:
        local_nm = "firmware_ext/tools/toolchain/bin/arm-none-eabi-nm"
        if os.path.isfile(local_nm) and os.access(local_nm, os.X_OK):
            _nm_path = local_nm
        else:
            _nm_path = shutil.which("arm-none-eabi-nm") or "arm-none-eabi-nm"
    return _nm_path


def payload_offset(device_addr):
    return PAYLOAD_HEADER + (device_addr - FLASH_APP_START)


def asm(text, addr):
    """Assemble one 4-byte Thumb-2 instruction at a device address. Never hand-encode these: a hardcoded
    "fff7febf" once stood for "b.w 0x0805e000" and was silently a self-branch, hanging the device."""
    enc, count = _ks.asm(text, addr)
    assert count == 1 and len(enc) == 4, f"{text!r} did not assemble to one 4-byte instruction"
    return bytes(enc)


def bl(target, at):
    return asm(f"bl 0x{target:08x}", at)


# Hooks. Each replaces 4 bytes of the stock image; `new` is computed from the extension's symbol address.
#   kind "vector": an interrupt vector entry (a word) now points at the extension symbol
#   kind "call":   a `bl stock_target` now calls the extension symbol, which wraps the stock routine
#   kind "nop":    a call is removed
#   kind "branch": the gate-stub trampoline `b.w` to the extension entry
def _vector(name, addr, stock, symbol, what):
    return dict(kind="vector", name=name, addr=addr, stock=stock.to_bytes(4, "little"), symbol=symbol, what=what)


def _call(name, addr, stock_target, symbol, what):
    return dict(kind="call", name=name, addr=addr, stock=bl(stock_target, addr), symbol=symbol, what=what)


HOOKS = [
    # b.w 0x08013f08 (stock console dispatch) -> extension_dispatch; other commands are forwarded back to it
    dict(kind="branch", name="trampoline", addr=0x0800F4E4, stock=bytes.fromhex("04f010bd"),
         symbol="extension_dispatch", what="gate-stub trampoline"),
    # DMA1_CH1 (IRQ 11, vector 27, app offset 0x6C): the stock handler 0x08005980 clears TC and sets the burst flag;
    # the extension ISR does the same and ring-stores the burst.
    _vector("dma", payload_offset(0x0800406C), 0x08005981, "extension_dma_isr", "DMA1_CH1 vector"),
    # ADC1_2 (IRQ 18, vector 34, app offset 0x88): roll path, the extension calls the stock handler 0x08004a4c itself.
    _vector("adc", payload_offset(0x08004088), 0x08004A4D, "extension_adc_isr", "ADC1_2 vector"),
    # T/div 0..2: the renderer's stock wait/copy routine call; the hook ring-stores the raw trace it filled.
    _call("wait", 0x08006AA0, 0x080074D0, "extension_wait_hook", "renderer wait/copy call"),
    # Main loop: the key consumer call, once per pass; the hook runs queued USB UI requests first.
    _call("ui", 0x0802A5D4, 0x08007CB0, "extension_ui_hook", "main-loop key-consumer call"),
    # Generator: boot init and generator screen body call the stock apply; the hook points the DAC DMA at a private table.
    _call("gen-boot", 0x08005C66, 0x08005DA0, "extension_gen_apply_hook", "generator apply call (boot init)"),
    _call("gen-screen", 0x08012DF4, 0x08005DA0, "extension_gen_apply_hook", "generator apply call (screen body)"),
    # The scope-entry block (main-menu item 1 and boot) called the apply with (1.0 V, square, 1000 Hz, 50 %): every scope
    # entry reset the output (`gen --log` showed it). Two NOPs, so the generator keeps what was last applied.
    dict(kind="nop", name="gen-reset", addr=0x0800FC44, stock=bl(0x08005DA0, 0x0800FC44), symbol=None,
         what="generator reset on scope entry"),
    # Generator screen entry: "str.w r6,[r4,#0x94]" (wave := 0) becomes a call that seeds the screen with the output in use.
    dict(kind="call", name="gen-seed", addr=0x08010F1A, stock=bytes.fromhex("c4f89460"), symbol="extension_gen_screen_enter",
         what="generator screen entry"),
]
NOP_NOP = bytes.fromhex("00bf00bf")


def extension_symbols():
    syms = {}
    for line in subprocess.check_output([get_nm(), EXT_ELF], text=True).splitlines():
        parts = line.split()
        if len(parts) == 3:
            syms[parts[2]] = int(parts[0], 16)
    return syms


def hook_bytes(hook, syms):
    """The 4 bytes that replace hook['stock'], and a description of the target."""
    if hook["kind"] == "nop":
        return NOP_NOP, "nop nop"
    sym = hook["symbol"]
    addr = syms.get(sym)
    if addr is None or not (FLASH_EXT_START <= addr < FLASH_EXT_START + FLASH_EXT_SIZE):
        raise RuntimeError(f"{sym} not found in the extension ELF (got {addr})")
    if hook["kind"] == "vector":
        return (addr | 1).to_bytes(4, "little"), f"0x{addr | 1:08x} ({sym})"
    if hook["kind"] == "branch":
        return asm(f"b.w 0x{addr:08x}", hook["addr"]), f"b.w 0x{addr:08x} ({sym})"
    return bl(addr, hook["addr"]), f"bl 0x{addr:08x} ({sym})"


def build_extension():
    print("[1/4] Compiling firmware_ext...")
    res = subprocess.run(["make", "-C", EXT_DIR])
    if res.returncode != 0:
        raise RuntimeError("Failed to build firmware_ext")
    if not os.path.isfile(EXT_BIN):
        raise FileNotFoundError(f"Missing build artifact: {EXT_BIN}")


def apply_hooks(data):
    syms = extension_symbols()
    for hook in HOOKS:
        off = payload_offset(hook["addr"]) if hook["kind"] != "vector" else hook["addr"]
        new, target = hook_bytes(hook, syms)
        cur = bytes(data[off:off + 4])
        if cur != hook["stock"] and cur != new:      # either the stock bytes or an earlier run of this script
            raise ValueError(f"Unexpected bytes for the {hook['what']} (payload offset 0x{off:x}): "
                             f"{cur.hex()} (expected {hook['stock'].hex()})")
        data[off:off + 4] = new
        print(f"  - {hook['what']}: payload offset 0x{off:x} -> {target}")


def assemble_payload(base_payload=None, stock_s19=None, out_bin=None):
    out_bin = out_bin or DEFAULT_OUT_FLASH_BIN
    print("[2/4] Assembling multi-segment payload...")
    if stock_s19 and os.path.isfile(stock_s19):
        print(f"  - Unpacking and applying gate stub to stock firmware: {stock_s19}")
        stock_bytes = open(stock_s19, "rb").read()
        hdr = stock_bytes[:0x20]
        key = hdr[0] ^ hdr[1] ^ hdr[0x1E] ^ hdr[0x1F]
        data = bytearray(b ^ key for b in stock_bytes[0x20:])
        # Retarget console caller at 0x361c to 0x0800f490, and place gate stub at 0xb4b0
        data[0x361c:0x3620] = bytes.fromhex("07f048ff")
        data[0xb4b0:0xb4b0 + len(GATE_STUB_BYTES)] = GATE_STUB_BYTES
    elif base_payload and os.path.isfile(base_payload):
        print(f"  - Using base payload: {base_payload}")
        data = bytearray(open(base_payload, "rb").read())
    else:
        raise FileNotFoundError(
            "Neither a valid stock firmware (.s19) nor base payload was found.\n"
            "Provide --stock <path_to_DSO-TC3_V0.3.s19> or place DSO-TC3_V0.3.s19 in the current directory."
        )

    apply_hooks(data)

    # Pad up to the extension; the payload starts PAYLOAD_HEADER bytes before FLASH_APP_START
    curr_flash_end = FLASH_APP_START + len(data) - PAYLOAD_HEADER
    gap = FLASH_EXT_START - curr_flash_end
    if gap < 0:
        raise ValueError(f"Base payload extends past extension start (curr 0x{curr_flash_end:x} > 0x{FLASH_EXT_START:x})")
    print(f"  - Base app ends at flash 0x{curr_flash_end:x}, adding 0x{gap:x} ({gap}) bytes 0xFF padding")
    data.extend(b"\xFF" * gap)

    ext_data = open(EXT_BIN, "rb").read()
    print(f"  - Appending extension binary ({len(ext_data)} bytes) at flash 0x{FLASH_EXT_START:x}")
    data.extend(ext_data)

    # Align total payload to 32-byte chunk boundary
    pad_len = -len(data) % 32
    if pad_len:
        data.extend(b"\xFF" * pad_len)
        print(f"  - Padded {pad_len} bytes to 32-byte boundary")

    final_flash_end = FLASH_APP_START + len(data) - PAYLOAD_HEADER
    print(f"  - Final payload size: {len(data)} bytes")
    print(f"  - Flash range: 0x{FLASH_APP_START:08x} .. 0x{final_flash_end:08x} (safe ceiling: 0x{MAX_FLASH_END:08x})")
    if final_flash_end >= MAX_FLASH_END:
        raise RuntimeError(f"CRITICAL SAFETY VIOLATION: Payload reaches config sector 0x{MAX_FLASH_END:08x}!")

    open(out_bin, "wb").write(bytes(data))
    print(f"  - Saved decrypted flash binary -> {out_bin}")
    return out_bin


def repack_s19(payload_path, out_s19=None):
    out_s19 = out_s19 or DEFAULT_OUT_S19
    print("[3/4] Repacking into encrypted .s19 update file...")
    cmd = [
        sys.executable, "firmware_ext/tools/repack_firmware.py", "pack",
        payload_path, out_s19,
        "--size-word", "auto"
    ]
    res = subprocess.run(cmd)
    if res.returncode != 0:
        raise RuntimeError("repack_firmware.py pack failed")
    print(f"  - Saved update file -> {out_s19}")


def verify_s19(out_s19=None, stock_s19=None):
    out_s19 = out_s19 or DEFAULT_OUT_S19
    print("[4/4] Verifying generated update file...")
    cmd = [sys.executable, "firmware_ext/tools/repack_firmware.py", "verify", out_s19]
    if stock_s19 and os.path.isfile(stock_s19):
        cmd += ["--stock", stock_s19]
    res = subprocess.run(cmd)
    if res.returncode != 0:
        raise RuntimeError("Verification failed!")
    print("SUCCESS: Custom firmware image built and verified.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stock", default="DSO-TC3_V0.3.s19",
                    help="path to official stock firmware (default: DSO-TC3_V0.3.s19)")
    ap.add_argument("--base", default=None,
                    help="path to base dump-firmware flash binary if already unpacked")
    ap.add_argument("-o", "--out", default=DEFAULT_OUT_S19,
                    help=f"output .s19 filename (default: {DEFAULT_OUT_S19})")
    ap.add_argument("--payload", default=DEFAULT_OUT_FLASH_BIN,
                    help=f"output decrypted payload filename (default: {DEFAULT_OUT_FLASH_BIN})")
    ap.add_argument("--nm", default=None, help="path to arm-none-eabi-nm")
    args = ap.parse_args(argv)

    if args.nm:
        global _nm_path
        _nm_path = args.nm

    build_extension()
    payload = assemble_payload(base_payload=args.base, stock_s19=args.stock, out_bin=args.payload)
    repack_s19(payload, out_s19=args.out)
    verify_s19(out_s19=args.out, stock_s19=args.stock)


if __name__ == "__main__":
    main()
