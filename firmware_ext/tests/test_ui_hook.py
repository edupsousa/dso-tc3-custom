#!/usr/bin/env python3
"""Offline test of the extension's deferred UI hook (Unicorn).

Loads the built custom image over the stock full-flash dump, restores a scope
SRAM snapshot, queues requests through the real `extension_dispatch` (0x55),
runs `extension_ui_hook` and checks the g_ScopeState fields and the sequence
of stock refresh routines called. Drawing code is stubbed (logged, not run);
the stock `adjust()` at 0x080116b0 runs for real.
usage: firmware_ext/tests/test_ui_hook.py
"""
import struct, sys
from harness import *   # fresh(stubs), run, u32, w32, dispatch, syms

STUBS = {0x08011df0: 'refresh_vdiv', 0x08011ea8: 'refresh_tdiv', 0x08011f58: 'refresh_trig',
         0x08011b04: 'refresh_pos', 0x08011a50: 'refresh_coupling', 0x08012038: 'refresh_edge',
         0x08011d20: 'refresh_runstop', 0x08025480: 'set_text', 0x08025b6c: 'widget_show',
         0x080073b8: 'capture_start', 0x080074d0: 'single_wait', 0x08007cb0: 'key_consumer'}
SS = 0x20000668
_fresh = fresh
def fresh(): return _fresh(STUBS)

def req(uc, field, val):
    dispatch(uc, bytes([0x55, field]) + struct.pack('<i', val))

def poll(uc):
    dispatch(uc, bytes([0x55, 0xFF]))

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "common"))
from testkit import check, finish

def case(name, field, val, offs, want, calls_want, mode=3, cursor_keep=5):
    uc, calls = fresh()
    w32(uc, SS + 0x20, mode); w32(uc, 0x20000144, cursor_keep); w32(uc, 0x20000704, 0)
    req(uc, field, val)
    run(uc, syms['extension_ui_hook'], 0)
    got = u32(uc, SS + offs) if offs is not None else None
    names = [c[0] for c in calls]
    ok = (offs is None or got == (want & 0xFFFFFFFF)) and names == calls_want and u32(uc, 0x20000144) == cursor_keep
    check(name, ok, f'state={got if got is None else hex(got)} calls={names} cursor={u32(uc,0x20000144)}')
    return uc

case('vdiv 7 -> 2', 2, 2, 0x28, 2, ['refresh_vdiv', 'key_consumer'])
case('vdiv same value is a no-op', 2, 7, 0x28, 7, ['key_consumer'])
case('vdiv clamps via range error', 2, 12, 0x28, 7, ['key_consumer'])
case('tdiv 9 -> 4', 4, 4, 0x30, 4, ['refresh_tdiv', 'key_consumer'])
case('probe 0 -> 1 (stock refreshes V/div label)', 3, 1, 0x2c, 1, ['set_text', 'refresh_vdiv', 'key_consumer'])
case('coupling toggle', 5, 0, 0x34, 0, ['refresh_coupling', 'key_consumer'])
case('edge toggle', 7, 1, 0x40, 1, ['refresh_edge', 'key_consumer'])
case('trigger mode 0 -> 2 (two steps)', 8, 2, 0x3c, 2, ['refresh_runstop', 'set_text', 'refresh_runstop', 'set_text', 'key_consumer'])
case('position -> 10', 1, 10, 0x24, 10, ['refresh_pos', 'key_consumer'])
case('trigger level -> -20', 6, -20, 0x38, -20, ['refresh_trig', 'key_consumer'])
uc = case('run -> stop', 0x10, 1, 0xa1, 1, ['refresh_runstop', 'key_consumer'])
# stop -> run restarts the widget
uc, calls = fresh(); w32(uc, SS + 0x20, 3); uc.mem_write(SS + 0xa1, b'\x01')
req(uc, 0x10, 0); run(uc, syms['extension_ui_hook'], 0)
check('stop -> run shows widget', [c[0] for c in calls] == ['refresh_runstop', 'widget_show', 'key_consumer'], str([c[0] for c in calls]))
# not in scope view: nothing is drawn, result code 8, then the poll clears it
uc, calls = fresh(); w32(uc, SS + 0x20, 1)
req(uc, 2, 2); run(uc, syms['extension_ui_hook'], 0)
S = syms['s_ui']
check('menu mode refuses', [c[0] for c in calls] == ['key_consumer'] and u32(uc, SS + 0x28) == 7
      and u32(uc, S) == 2 and u32(uc, S + 4) == 8, f'state={u32(uc,S)} result={u32(uc,S+4)}')
poll(uc)
check('poll returns DONE then idles', u32(uc, S) == 0)
# second request while one is pending is refused, the first survives
uc, calls = fresh(); w32(uc, SS + 0x20, 3)
req(uc, 2, 3); req(uc, 4, 1)
check('busy keeps first request', u32(uc, S) == 1 and u32(uc, S + 8) == 2 and u32(uc, S + 12) == 3)
# virtual key: any screen, posted to the stock mailbox (+0x90 type 1, +0x91 key) before the key consumer runs
for mode in (1, 3, 4, 5):
    uc, calls = fresh(); w32(uc, SS + 0x20, mode); uc.mem_write(SS + 0x90, b'\x00\x00')
    req(uc, 0x20, 0x01); run(uc, syms['extension_ui_hook'], 0)
    check(f'key back in mode {mode}: mailbox written, consumer called after',
          bytes(uc.mem_read(SS + 0x90, 2)) == b'\x01\x01' and [c[0] for c in calls] == ['key_consumer'] and u32(uc, S + 4) == 0,
          f'mailbox={bytes(uc.mem_read(SS + 0x90, 2)).hex()} result={u32(uc, S + 4)}')
uc, calls = fresh(); w32(uc, SS + 0x20, 1); uc.mem_write(SS + 0x90, b'\x01\x08')   # a person's event still unconsumed
req(uc, 0x20, 0x02); run(uc, syms['extension_ui_hook'], 0)
check('key refused while the mailbox holds an event (result 10), event untouched',
      u32(uc, S + 4) == 10 and bytes(uc.mem_read(SS + 0x90, 2)) == b'\x01\x08')
for bad in (0, 3, 0x40, 0x11):
    uc, calls = fresh(); uc.mem_write(SS + 0x90, b'\x00\x00')
    req(uc, 0x20, bad); run(uc, syms['extension_ui_hook'], 0)
    check(f'key mask {bad:#x} refused (result 9), mailbox untouched', u32(uc, S + 4) == 9 and bytes(uc.mem_read(SS + 0x90, 2)) == b'\x00\x00')
# long press of select: held-type event, key 2
uc, calls = fresh(); w32(uc, SS + 0x20, 3); uc.mem_write(SS + 0x90, b'\x00\x00')
req(uc, 0x20, 0x102); run(uc, syms['extension_ui_hook'], 0)
check('hold-select: mailbox = type 0xFE, key 2', bytes(uc.mem_read(SS + 0x90, 2)) == b'\xfe\x02' and u32(uc, S + 4) == 0,
      bytes(uc.mem_read(SS + 0x90, 2)).hex())
# bad field
uc, calls = fresh(); w32(uc, SS + 0x20, 3)
req(uc, 0x33, 1); run(uc, syms['extension_ui_hook'], 0)
check('unknown field -> result 7', u32(uc, S + 4) == 7)

# ---- screen capture (0x56) ----
STUBS[0x08027d68] = 'obj_invalidate'
STUBS[0x0801984c] = 'stock_flush'
C = syms['s_cap']          # armed, ready, ack, seq, px, want_inv, timeouts, area[4] (int16)
uc, calls = fresh()
w32(uc, 0x2000b040, 0x0801984d)                     # as on the stock device
w32(uc, 0x20000578, 0x2000bec4); w32(uc, 0x2000bec4 + 0x18, 0x2000c160)
run(uc, syms['extension_ui_hook'], 0)
check('hook swaps flush_cb', u32(uc, 0x2000b040) == (syms['ext_flush'] | 1), hex(u32(uc, 0x2000b040)))
dispatch(uc, bytes([0x56, 1, 1]))
check('arm sets armed + want_inv', u32(uc, C) == 1 and u32(uc, C + 20) == 1)
calls.clear(); run(uc, syms['extension_ui_hook'], 0)
check('arm triggers invalidate of act_scr', calls[0][0] == 'obj_invalidate' and calls[0][1] == 0x2000c160, str(calls))
# ext_flush while armed: publishes area/px, waits for ack, then runs the stock flush
uc.mem_write(0x20100100, struct.pack('<hhhh', 0, 60, 319, 119))
calls.clear()
uc.reg_write(UC_ARM_REG_R0, 0x2000b02c); uc.reg_write(UC_ARM_REG_R1, 0x20100100); uc.reg_write(UC_ARM_REG_R2, 0x20001a2c)
uc.reg_write(UC_ARM_REG_SP, 0x20100f00); uc.reg_write(UC_ARM_REG_LR, 0x08079001)
try: uc.emu_start(syms['ext_flush'] | 1, 0x08079000, count=400)
except UcError: pass
check('flush waits while armed', u32(uc, C + 4) == 1 and calls == [], f'ready={u32(uc,C+4)} calls={calls}')
check('area/px published', struct.unpack('<hhhh', bytes(uc.mem_read(C + 28, 8))) == (0, 60, 319, 119) and u32(uc, C + 16) == 0x20001a2c)
w32(uc, C + 8, 1)                                   # host ack
pc = uc.reg_read(UC_ARM_REG_PC)
uc.emu_start(pc | 1, 0x08079000, count=2000)
check('ack releases to stock flush', [c[0] for c in calls] == ['stock_flush'] and u32(uc, C + 4) == 0, str(calls))
# disarmed: flush passes straight through
dispatch(uc, bytes([0x56, 0]))
calls.clear(); run(uc, syms['ext_flush'], 0x2000b02c, 0x20100100, 0x20001a2c)
check('disarmed flush is a pass-through', [c[0] for c in calls] == ['stock_flush'])

# ---- roll mode (0x57) ----
STUBS[0x08004a4c] = 'stock_adc_isr'
R = syms['s_roll']          # count, base, epoch, last_tdiv, ring[1024]
def roll_isr(uc, raw, vdiv=3, tdiv=16):
    w32(uc, SS + 0x28, vdiv); w32(uc, SS + 0x30, tdiv); w32(uc, 0x4002084c, raw)
    run(uc, syms['extension_adc_isr'])
def roll_req(uc, op, frm=0):
    dispatch(uc, bytes([0x57, op]) + struct.pack('<I', frm))
def sent(uc):
    # ext_cdc_send copies the reply to USB PMA; read back via the response buffer instead
    return bytes(uc.mem_read(syms['s_resp_buf'], 64))
uc, calls = fresh()
roll_req(uc, 0xFF)
check('roll header before any sample -> ERR_EMPTY', sent(uc)[:3] == bytes([0x7E, 0x57, 5]))
for i in range(40):
    roll_isr(uc, 0x100 + i, vdiv=3 if i < 30 else 5)
check('isr calls stock handler once per sample', [c[0] for c in calls].count('stock_adc_isr') == 40)
check('count after 40 samples', u32(uc, R) == 40 and u32(uc, R + 4) == 0 and u32(uc, R + 8) == 0)
roll_req(uc, 0xFF); h = sent(uc)
check('header fields', h[0] == 0x57 and h[1] == 0xFF and struct.unpack('<II', h[3:11]) == (40, 0)
      and h[12] == 16 and h[13] == 5 and struct.unpack('<H', h[24:26])[0] == 1024, h[:26].hex())
roll_req(uc, 1, 0); r = sent(uc)
vals = struct.unpack('<29H', r[3:61])
check('chunk 0: 29 samples packed raw|vdiv<<12', r[2] == 29 and vals[0] == (0x100 | 3 << 12) and vals[28] == (0x11c | 3 << 12),
      f'n={r[2]} first={vals[0]:#x} last={vals[28]:#x}')
roll_req(uc, 1, 29); r = sent(uc)
vals = struct.unpack('<11H', r[3:25])
check('chunk at 29: 11 samples, vdiv changes at 30', r[2] == 11 and vals[0] == (0x11d | 3 << 12) and vals[1] == (0x11e | 5 << 12),
      f'n={r[2]} {vals[0]:#x} {vals[1]:#x}')
roll_req(uc, 1, 40); check('from == count -> n=0', sent(uc)[:3] == bytes([0x57, 1, 0]))
roll_isr(uc, 0x200, tdiv=17)
check('tdiv change starts a new epoch at the current count', u32(uc, R + 4) == 40 and u32(uc, R + 8) == 1, f'base={u32(uc,R+4)} epoch={u32(uc,R+8)}')
roll_req(uc, 1, 10); check('from before epoch base -> ERR_NO_FRAME', sent(uc)[:3] == bytes([0x7E, 0x57, 4]))
for i in range(1100): roll_isr(uc, i & 0xFFF, tdiv=17)
roll_req(uc, 1, 40 + 5); check('overwritten index -> ERR_NO_FRAME', sent(uc)[:3] == bytes([0x7E, 0x57, 4]))
c = u32(uc, R); roll_req(uc, 1, c - 29); r = sent(uc)
vals = struct.unpack('<29H', r[3:61])
check('recent window readable after wrap', r[2] == 29 and all((v & 0xFFF) == ((c - 29 + j - 41) & 0xFFF) for j, v in enumerate(vals)), f'n={r[2]}')
roll_req(uc, 7); check('bad op -> ERR_BAD_CHUNK', sent(uc)[:3] == bytes([0x7E, 0x57, 2]))

# ---- T/div 0..2 wait/copy hook ----
RING = syms['s_ring']        # seq, slot[] (seq, rate, gain, vdiv, tdiv, probe, mode, samples[600])
TRACE = 0x20000bfc
def run_wait(uc, new_trace):
    """Run extension_wait_hook; the stubbed stock routine 0x080074d0 writes `new_trace` (or nothing)."""
    h = None
    if new_trace is not None:
        h = uc.hook_add(UC_HOOK_CODE, lambda u, a, sz, ud: u.mem_write(TRACE, struct.pack('<600H', *new_trace)),
                        begin=0x080074d0, end=0x080074d0)
    run(uc, syms['extension_wait_hook'])
    if h is not None:
        uc.hook_del(h)
uc, calls = fresh()
w32(uc, SS + 0x30, 1); w32(uc, SS + 0x28, 3); w32(uc, SS + 0xb0, 0x4620a000)
patA = [(i * 7 + 100) & 0xFFF for i in range(600)]
run_wait(uc, patA)
check('hook stores a changed trace as frame 1', u32(uc, RING) == 1 and [c[0] for c in calls].count('single_wait') == 1)
slot1 = RING + 4 + 1 * (16 + 1200)          # seq % 8 == 1
check('slot holds samples and metadata', u32(uc, slot1) == 1 and u32(uc, slot1 + 4) == 0x4620a000
      and bytes(uc.mem_read(slot1 + 12, 2))[0] == 3 and bytes(uc.mem_read(slot1 + 12, 4))[1] == 1
      and list(struct.unpack('<600H', bytes(uc.mem_read(slot1 + 16, 1200)))) == patA)
run_wait(uc, None)
check('unchanged trace (timed-out wait) stores nothing', u32(uc, RING) == 1)
patB = [(i * 5 + 900) & 0xFFF for i in range(600)]
run_wait(uc, patB)
check('next change is frame 2', u32(uc, RING) == 2)
w32(uc, SS + 0x30, 7)
run_wait(uc, [(i + 1) & 0xFFF for i in range(600)])
check('T/div >= 3 passes through (DMA ISR owns the ring)', u32(uc, RING) == 2)
w32(uc, SS + 0x30, 1)
dispatch(uc, bytes([0x54, 0xFF]) + struct.pack('<I', 0))
r = bytes(uc.mem_read(syms['s_resp_buf'], 24))
check('0x54 header reports the T/div 1 frame', r[0] == 0x54 and struct.unpack('<I', r[3:7])[0] == 2 and r[12] == 1, r.hex())
dispatch(uc, bytes([0x54, 3]) + struct.pack('<I', 0))
r = bytes(uc.mem_read(syms['s_resp_buf'], 62))
check('0x54 chunk 3 returns trace samples 90..119', list(struct.unpack('<30H', r[2:62])) == patB[90:120])

# ---- legacy params (0x50..0x52), info (0x5F), first-use RAM init ----
def cmd(uc, data):
    dispatch(uc, data)
    return bytes(uc.mem_read(syms['s_resp_buf'], 64))
uc, calls = fresh()
w32(uc, SS + 0x28, 6); w32(uc, SS + 0x2c, 1); w32(uc, SS + 0x30, 9); w32(uc, SS + 0x34, 1); w32(uc, SS + 0x20, 3); uc.mem_write(SS + 0xa1, b'\x01')
for pid, off, want in ((1, 0x20, 3), (2, 0x28, 6), (3, 0x2c, 1), (4, 0x30, 9), (5, 0x34, 1), (7, 0xa1, 1)):
    r = cmd(uc, [0x51, pid])
    check(f'get param {pid} reads +{off:#x}', r[:2] == bytes([0x51, pid]) and struct.unpack('<I', r[2:6])[0] == want, r[:6].hex())
check('get unknown param -> error 1', cmd(uc, [0x51, 0x42])[:3] == bytes([0x7E, 0x42, 1]))
check('set tdiv 12', cmd(uc, [0x52, 4] + list(struct.pack('<I', 12)))[:3] == bytes([0x52, 4, 0]) and u32(uc, SS + 0x30) == 12)
for pid, bad in ((4, 22), (2, 10), (3, 2), (5, 2), (7, 2), (1, 9)):
    r = cmd(uc, [0x52, pid] + list(struct.pack('<I', bad)))
    check(f'set param {pid} = {bad} refused (error 3), field untouched', r[:3] == bytes([0x7E, pid, 3]) and u32(uc, SS + {1: 0x20, 2: 0x28, 3: 0x2c, 4: 0x30, 5: 0x34, 7: 0xa1}[pid]) in (3, 6, 1, 12, 1))
r = cmd(uc, [0x52, 0x42] + list(struct.pack('<I', 1)))
check('set unknown param -> error 1', r[:3] == bytes([0x7E, 0x42, 1]))
r = cmd(uc, [0x50, 0xFF])
check('snap header: run/stop, vdiv, tdiv, probe, coupling, count 600, 20 chunks',
      r[:2] == bytes([0x50, 0xFF]) and r[2:7] == bytes([1, 6, 12, 1, 1]) and struct.unpack('<H', r[9:11])[0] == 600 and r[11] == 20, r[:12].hex())
uc.mem_write(0x2000b07c, struct.pack('<600H', *range(600)))
r = cmd(uc, [0x50, 2])
check('snap chunk 2 = raw samples 60..89', list(struct.unpack('<30H', r[2:62])) == list(range(60, 90)))
check('snap chunk 20 -> bad chunk error', cmd(uc, [0x50, 20])[:3] == bytes([0x7E, 20, 2]))
r = cmd(uc, [0x5F])
check('info: version bytes and banner', r[0] == 0x5F and r[1:4] == bytes([1, 1, 0]) and r[4:18] == b'TC3-EXT:v1.1.0', r[:20].hex())
# stock commands and short requests go to the stock console (stub: the sentinel call is not made, so check for no reply change)
STUBS[0x08013f08] = 'console'
uc, calls = fresh()
for data in ([0x00], [0x10, 1, 2], [0x51], [0x52, 1, 2], [0x55, 1], [0x58], [0x56]):
    calls.clear(); cmd(uc, data)
    check(f'{data[0]:#x} len {len(data)} is forwarded to the stock console', [c[0] for c in calls] == ['console'], str(calls))
# first use after power-up: RAM_EXT holds garbage, the first entry clears it (no stale PENDING/armed state)
uc, calls = fresh()
uc.mem_write(0x20013000, b'\xa5' * 0x4000)
check('garbage RAM: gen state would be PENDING without init', u32(uc, syms['s_gen']) == 0xa5a5a5a5)
run(uc, syms['extension_ui_hook'], 0)
check('first entry clears the extension RAM', u32(uc, syms['s_gen']) == 0 and u32(uc, syms['s_cap']) == 0 and u32(uc, syms['s_ui']) == 0
      and u32(uc, syms['g_ext_magic']) == 0x45585431 and calls and [c[0] for c in calls] == ['key_consumer'], str(calls))
w32(uc, syms['s_roll'] + 16, 0x1234)
run(uc, syms['extension_ui_hook'], 0)
check('later entries leave the state alone', u32(uc, syms['s_roll'] + 16) == 0x1234)

# ---- DMA1_CH1 burst ISR (T/div >= 3) ----
uc, calls = fresh()
w32(uc, SS + 0x30, 8); w32(uc, SS + 0x28, 4); w32(uc, SS + 0xb0, 0x49742400); uc.mem_write(SS + 0xa0, b'\x00')
burst = [(i * 13 + 7) & 0xFFF for i in range(600)]
uc.mem_write(0x2000b07c, struct.pack('<600H', *burst))
run(uc, syms['extension_dma_isr'])
slot = RING + 4 + 1 * (16 + 1200)
check('dma isr: clears TC, sets the burst flag', u32(uc, 0x40020004) == 2 and uc.mem_read(SS + 0xa0, 1)[0] == 1)
check('dma isr: frame 1 holds the burst and the settings',
      u32(uc, RING) == 1 and u32(uc, slot) == 1 and u32(uc, slot + 4) == 0x49742400
      and uc.mem_read(slot + 12, 4)[0] == 4 and uc.mem_read(slot + 12, 4)[1] == 8
      and list(struct.unpack('<600H', bytes(uc.mem_read(slot + 16, 1200)))) == burst)
for i in range(9): run(uc, syms['extension_dma_isr'])
r = cmd(uc, [0x54, 0xFF, 1, 0, 0, 0])
check('frame 1 is overwritten after 8 more bursts (ring of 8)', r[:3] == bytes([0x7E, 0x54, 4]))
r = cmd(uc, [0x54, 29, 0, 0, 0, 0])
check('chunk 29 -> bad chunk error', r[:3] == bytes([0x7E, 0x54, 2]))
r = cmd(uc, [0x54, 19, 0, 0, 0, 0])
check('latest frame, chunk 19 = samples 570..599', list(struct.unpack('<30H', r[2:62])) == burst[570:600])
finish()
