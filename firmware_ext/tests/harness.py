"""Unicorn harness for the offline firmware tests.
Run tests from the repository root.

Optional environment, for a richer emulated device than the defaults:
  TC3_FLASH_BASE     full 512 KB flash dump to start from (default: blank flash)
  TC3_CUSTOM_IMAGE   built update file (.s19) to load, tried before the default locations
  TC3_SRAM_SNAPSHOT  128 KB SRAM snapshot of a running scope (default: a minimal synthetic state)"""
import os
import shutil
import struct
import subprocess

from unicorn import *
from unicorn.arm_const import *

local_nm = 'firmware_ext/tools/toolchain/bin/arm-none-eabi-nm'
NM = local_nm if (os.path.isfile(local_nm) and os.access(local_nm, os.X_OK)) else (shutil.which('arm-none-eabi-nm') or 'arm-none-eabi-nm')
syms = {l.split()[2]: int(l.split()[0], 16) for l in
        subprocess.check_output([NM, 'firmware_ext/build/extension.elf'], text=True).splitlines() if len(l.split()) == 3}

flash = bytearray(open(os.environ['TC3_FLASH_BASE'], 'rb').read() if os.environ.get('TC3_FLASH_BASE') else 0x80000)
HAS_PAYLOAD = False

# Load flash from available build artifact
SEARCH_S19 = ([os.environ['TC3_CUSTOM_IMAGE']] if os.environ.get('TC3_CUSTOM_IMAGE') else []) + [
    'DSO-TC3_custom-ext.s19',
    'firmware_ext/build/DSO-TC3_custom-ext.s19',
    'firmware/custom-builds/DSO-TC3_custom-ext.s19',
]

for s19_path in SEARCH_S19:
    if os.path.exists(s19_path):
        s19_data = open(s19_path, 'rb').read()
        key = s19_data[0] ^ s19_data[1] ^ s19_data[0x1E] ^ s19_data[0x1F]
        payload = bytes(b ^ key for b in s19_data[0x40:])
        flash[0x4000:0x4000 + len(payload)] = payload
        HAS_PAYLOAD = True
        break
else:
    for bin_path in ['DSO-TC3_custom-ext.flash.bin', 'firmware/custom-builds/DSO-TC3_custom-ext.flash.bin']:
        if os.path.exists(bin_path):
            payload = open(bin_path, 'rb').read()[0x20:]
            flash[0x4000:0x4000 + len(payload)] = payload
            HAS_PAYLOAD = True
            break

# If extension.bin is built, ensure it is in place
ext_bin = 'firmware_ext/build/extension.bin'
if os.path.exists(ext_bin):
    ext = open(ext_bin, 'rb').read()
    flash[0x5e000:0x5e000 + len(ext)] = ext

# Initial SRAM state (128 KB): scope mode=3, vdiv=7, coupling=1 at 0x20000668
s = bytearray(0x20000)
s[0x668 + 0x20] = 3
s[0x668 + 0x28] = 7
s[0x668 + 0x34] = 1
sram = open(os.environ['TC3_SRAM_SNAPSHOT'], 'rb').read() if os.environ.get('TC3_SRAM_SNAPSHOT') else bytes(s)

REQ = 0x20100000          # scratch RAM for requests
SENTINEL = 0x08079000     # return address: emulation stops here


def fresh(stubs, on_stub=None, fpu=False, names_only=False):
    """A new emulator. Calls to the addresses in `stubs` ({addr: name}) return 0 at once instead of running the stock routine;
    `on_stub(uc, name)` sees each one. Returns (uc, calls) where calls lists the stubs hit as (name, r0, r1), or just the names."""
    uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    uc.mem_map(0x08000000, 0x80000); uc.mem_write(0x08000000, bytes(flash))
    uc.mem_map(0x20000000, 0x20000); uc.mem_write(0x20000000, sram)
    uc.mem_map(0x40000000, 0x20000)
    uc.mem_map(0x40020000, 0x1000)
    uc.mem_map(REQ, 0x1000)
    if fpu:
        uc.mem_map(0xE000E000, 0x1000)
        uc.mem_write(0xE000ED88, struct.pack('<I', 0xF << 20))   # CPACR: enable the FPU
    calls = []
    def hook(uc, addr, size, ud):
        if addr in stubs:
            calls.append(stubs[addr] if names_only else (stubs[addr], uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1)))
            if on_stub:
                on_stub(uc, stubs[addr])
            uc.reg_write(UC_ARM_REG_R0, 0)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR) | 1)
    uc.hook_add(UC_HOOK_CODE, hook, begin=0x08000000, end=0x08080000)
    return uc, calls


def run(uc, entry, *args, count=200000):
    for i, a in enumerate(args):
        uc.reg_write(UC_ARM_REG_R0 + i, a & 0xFFFFFFFF)
    uc.reg_write(UC_ARM_REG_SP, 0x20100f00)
    uc.reg_write(UC_ARM_REG_LR, SENTINEL | 1)
    uc.emu_start(entry | 1, SENTINEL, count=count)


def u32(uc, a): return struct.unpack('<I', uc.mem_read(a, 4))[0]
def w32(uc, a, v): uc.mem_write(a, struct.pack('<I', v & 0xFFFFFFFF))


def dispatch(uc, data, count=200000):
    """Send one request through the real extension_dispatch."""
    uc.mem_write(REQ, bytes(data))
    run(uc, syms['extension_dispatch'], REQ, len(data), count=count)
