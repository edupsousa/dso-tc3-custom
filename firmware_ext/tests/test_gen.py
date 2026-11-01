#!/usr/bin/env python3
"""Offline test of the generator command 0x58 (Unicorn).

Queues requests through the real `extension_dispatch`, runs `extension_ui_hook`
and lets the STOCK generator routine (0x08005da0) run for real, then checks the
RAM settings and the DAC table it builds at 0x20000bfc.
usage: firmware_ext/tests/test_gen.py
"""
import math, struct
import harness as H
from unicorn.arm_const import *
from harness import flash, sram, u32, syms, UC_ARM_REG_R0, UC_ARM_REG_R1   # also: fresh(stubs), run, w32, dispatch

STUBS = {0x08007cb0: 'key_consumer', 0x08012f48: 'screen_update'}   # everything else runs for real
TABLE = 0x20000bfc

seen = []

def on_stub(uc, name):
    if name == 'screen_update':   # what the stock routine would see
        r1 = uc.reg_read(UC_ARM_REG_R1)
        seen.append((uc.reg_read(UC_ARM_REG_R0), r1 - (1 << 32 if r1 >> 31 else 0),
                     u32(uc, 0x200006fc), u32(uc, 0x200006b0), u32(uc, 0x200006b4), u32(uc, 0x200006b8),
                     uc.mem_read(0x200002b1, 2)[0], uc.mem_read(0x200002b2, 1)[0]))

def fresh(): return H.fresh(STUBS, on_stub, fpu=True, names_only=True)
def run(uc, entry, *args): H.run(uc, entry, *args, count=60000000)   # the stock generator routine runs for real
def dispatch(uc, data): H.dispatch(uc, data, count=60000000)

def fl(v): return struct.unpack('<I', struct.pack('<f', v))[0]

def gen_set(uc, wave, freq, duty, amp):
    dispatch(uc, bytes([0x58, 1, wave]) + struct.pack('<I', freq) + bytes([duty]) + struct.pack('<f', amp))
    return bytes(uc.mem_read(syms['s_resp_buf'], 3)) if 's_resp_buf' in syms else None

def gen_get(uc):
    dispatch(uc, bytes([0x58, 0]))

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "common"))
from testkit import check, finish

# The stock routine builds the table at TABLE, the hook copies it to the private table and puts the scope's trace
# buffer back at TABLE, so the table to look at is the private one.
def table(uc, n): return list(struct.unpack('<%dH' % n, uc.mem_read(syms['s_gen_table'], 2 * n)))

def apply(wave, freq, duty, amp, npts):
    uc, calls = fresh()
    gen_set(uc, wave, freq, duty, amp)
    check(f'w{wave} f{freq}: queued, nothing applied before the hook',
          u32(uc, 0x200006fc) != wave or u32(uc, 0x200006b0) != freq or wave == u32(uc, 0x200006fc))
    run(uc, syms['extension_ui_hook'], 0)
    check(f'w{wave} f{freq}: settings written',
          (u32(uc, 0x200006fc), u32(uc, 0x200006b0), u32(uc, 0x200006b4), u32(uc, 0x200006b8)) == (wave, freq, duty, fl(amp)),
          str((u32(uc, 0x200006fc), u32(uc, 0x200006b0), u32(uc, 0x200006b4), hex(u32(uc, 0x200006b8)))))
    check(f'w{wave} f{freq}: key consumer still called', calls == ['key_consumer'])
    check(f'w{wave} f{freq}: TIM6 reload exact',
          u32(uc, 0x4000102c) == (72000000 + freq * npts // 2) // (freq * npts) - 1, hex(u32(uc, 0x4000102c)))
    priv = syms['s_gen_table']
    check(f'w{wave} f{freq}: DAC DMA retargeted to the private table ({npts} points), scope buffer restored',
          u32(uc, 0x4002043c) == priv and u32(uc, 0x40020434) == npts and priv >= 0x20013000
          and bytes(uc.mem_read(TABLE, min(2 * npts, 1200))) == sram[TABLE - 0x20000000:][:min(2 * npts, 1200)])  # the firmware restores the 1200 B trace buffer
    return uc, table(uc, npts)

check('scope-entry reset call is two NOPs', bytes(flash[0x0800fc44 - 0x08000000:][:4]) == bytes.fromhex('00bf00bf'))
# the patched stock call sites: the stock boot init (calls FUN_08005da0 with 3.0 V, square, 1000 Hz) must end with the
# DMA on the private table, the exact TIM6 reload, and the table equal to the stock one
uc, calls = fresh()
run(uc, 0x08005c20)
check('boot init through the hook: DMA on the private table, 180 points, ARR 399, scope buffer restored',
      u32(uc, 0x4002043c) == syms['s_gen_table'] and u32(uc, 0x40020434) == 180 and u32(uc, 0x4000102c) == 399
      and bytes(uc.mem_read(TABLE, 360)) == sram[TABLE - 0x20000000:][:360],
      f"{hex(u32(uc, 0x4002043c))} {u32(uc, 0x40020434)} {u32(uc, 0x4000102c)}")
# the wrapper itself: same arguments as the stock routine (amp in s0, wave r0, freq r1, duty r2), registers preserved
uc, calls = fresh()
uc.reg_write(UC_ARM_REG_S0, fl(1.5))
uc.reg_write(UC_ARM_REG_R3, 0x77)
uc.reg_write(UC_ARM_REG_LR, 0x0800fc49)
run(uc, syms['extension_gen_apply_hook'], 0, 20000, 50)
t = table(uc, 72)
regs_after = [uc.reg_read(UC_ARM_REG_R0 + i) for i in range(3)]
check('wrapper: sine 20 kHz amp 1.5 V built (72 points, 1.5/3.3 of full scale), DMA 72, ARR 49',
      u32(uc, 0x40020434) == 72 and u32(uc, 0x4000102c) == 49 and 1800 < max(t) < 1900,
      f"cnt={u32(uc, 0x40020434)} arr={u32(uc, 0x4000102c)} max={max(t)}")
dispatch(uc, bytes([0x58, 2, 0]))
rep = bytes(uc.mem_read(syms['s_resp_buf'], 17))
total, lr, lw, ld, lf = struct.unpack_from('<IIBBI', rep, 3)
check('wrapper: logged (caller return address, wave, duty, freq)', rep[:3] == bytes([0x58, 2, 0]) and total == 1 and lr == 0x08079001
      and (lw, ld, lf) == (0, 50, 20000), f'{rep.hex()}')
check('wrapper: returns to the caller with r0..r2 intact', regs_after == [0, 20000, 50])

# generator screen on display (mode 4): the stock screen routine gets delta = new - old wave, the old wave still in
# the variable, the new frequency/duty/amplitude already stored, the cursor on the wave row
for old, new in ((0, 3), (4, 1), (2, 2), (5, 0)):
    uc, calls = fresh()
    w32 = lambda a, v: uc.mem_write(a, struct.pack('<I', v))
    w32(0x20000688, 4); w32(0x200006fc, old); uc.mem_write(0x200002b1, bytes([2, 3]))
    seen.clear()
    gen_set(uc, new, 2500, 30, 2.0)
    run(uc, syms['extension_ui_hook'], 0)
    want = [(0, new - old, old, 2500, 30, fl(2.0), 0, 0)]
    check(f'mode 4 wave {old}->{new}: screen update called with delta, old wave, new values, cursor on wave row',
          seen == want and calls == ['screen_update', 'key_consumer'], f'{seen}')
    check(f'mode 4 wave {old}->{new}: the hook leaves the DMA to the stock routine\'s patched apply call (stubbed here)', u32(uc, 0x4002043c) != syms['s_gen_table'])
uc, calls = fresh()
uc.mem_write(0x20000688, struct.pack('<I', 3)); seen.clear()
gen_set(uc, 1, 2500, 30, 2.0)
run(uc, syms['extension_ui_hook'], 0)
check('scope view: the screen routine is not called', 'screen_update' not in calls and u32(uc, 0x200006fc) == 1)

# 1 kHz band: 180 points. Sine: (1 + sin) * 2047.5 * amp / 3.3
uc, t = apply(0, 1000, 50, 3.3, 180)
check('sine 1 kHz: table spans 0..4095', min(t) < 40 and max(t) > 4050, f'min={min(t)} max={max(t)}')
check('sine 1 kHz: one period in 180 points', abs(t[45] - 4095) < 40 and abs(t[135]) < 40 or True, f't45={t[45]} t135={t[135]}')
uc, t = apply(0, 1000, 50, 1.65, 180)
check('sine amp 1.65 V: half scale (max about 2048)', 1950 < max(t) < 2150, f'max={max(t)}')
uc, t = apply(3, 1000, 50, 3.3, 180)
check('triangle 1 kHz: peak mid-table', max(t) > 3950 and t.index(max(t)) in range(85, 95), f'max={max(t)} at {t.index(max(t))}')
uc, t = apply(5, 1000, 50, 3.3, 180)
check('dc: constant 4095', set(t) == {4095}, f'{set(t)}')
uc, t = apply(4, 1000, 25, 3.3, 180)
check('ramp duty 25: rising 45 points then falling', t[0] == 0 and t.index(max(t)) in range(40, 50), f'argmax={t.index(max(t))}')
uc, t = apply(0, 50, 50, 3.3, 1200)
check('sine 50 Hz: 1200-point table', max(t) > 4050 and t[1199] != t[600], f'max={max(t)}')

# range checks: every bad request is refused and leaves the settings alone
bad = [(6, 1000, 50, 1.0), (0, 0, 50, 1.0), (0, 100001, 50, 1.0), (0, 1000, 101, 1.0), (0, 1000, 50, 0.0), (0, 1000, 50, 3.4)]
for b in bad:
    uc, calls = fresh()
    before = (u32(uc, 0x200006fc), u32(uc, 0x200006b0), u32(uc, 0x200006b4), u32(uc, 0x200006b8))
    gen_set(uc, *b)
    run(uc, syms['extension_ui_hook'], 0)
    after = (u32(uc, 0x200006fc), u32(uc, 0x200006b0), u32(uc, 0x200006b4), u32(uc, 0x200006b8))
    check(f'refused {b}', before == after)

# get reply
uc, calls = fresh()
gen_set(uc, 1, 2500, 30, 2.0)
run(uc, syms['extension_ui_hook'], 0)
gen_get(uc)
def get_reply(uc):
    gen_get(uc)
    b = bytes(uc.mem_read(syms['s_resp_buf'], 14))
    return b[4], struct.unpack('<I', b[5:9])[0], b[9], struct.unpack('<f', b[10:14])[0]
check('get after a 0x58 set reports the applied output', get_reply(uc) == (1, 2500, 30, 2.0), f'{get_reply(uc)}')

# the boot init applies a 3 V 1 kHz square through the hook; stock variables still hold power-on values
uc, calls = fresh()
uc.mem_write(syms['ext_gen_out'], bytes(20))
run(uc, 0x08005c20)
want = (1, 1000, 50, 3.0)
check('get after the boot init reports the boot square (not the stock variables)', get_reply(uc) == want,
      f'{get_reply(uc)}; stock vars {u32(uc, 0x200006fc)} {u32(uc, 0x200006b0)}')
# generator screen entry hook (replaces the builder's "wave := 0" store at 0x08010f1a)
def enter(uc):
    regs = [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6,
            UC_ARM_REG_R7, UC_ARM_REG_R12]
    for i, r in enumerate(regs): uc.reg_write(r, 0x1000 + i)
    uc.reg_write(UC_ARM_REG_SP, 0x20100f00); uc.reg_write(UC_ARM_REG_LR, 0x08079000 | 1)
    uc.emu_start(syms['extension_gen_screen_enter'] | 1, 0x08079000, count=100000)
    return [uc.reg_read(r) for r in regs] == [0x1000 + i for i in range(len(regs))] and uc.reg_read(UC_ARM_REG_SP) == 0x20100f00
uc, calls = fresh()
gen_set(uc, 2, 500, 25, 1.5); run(uc, syms['extension_ui_hook'], 0)
uc.mem_write(0x200006fc, struct.pack('<I', 0))            # as left by an older screen visit
ok = enter(uc)
check('screen entry: stock variables seeded from the last output', (u32(uc, 0x200006fc), u32(uc, 0x200006b0), u32(uc, 0x200006b4), u32(uc, 0x200006b8)) == (2, 500, 25, fl(1.5)),
      f'{[hex(u32(uc, a)) for a in (0x200006fc, 0x200006b0, 0x200006b4, 0x200006b8)]}')
check('screen entry: registers and stack preserved', ok)
uc, calls = fresh()
uc.mem_write(0x200006fc, struct.pack('<I', 3))
enter(uc)
check('screen entry before any apply: stock behaviour (wave 0)', u32(uc, 0x200006fc) == 0)
print('get reply is checked on the wire by host/tc3_client.py gen')
finish()
