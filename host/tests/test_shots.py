#!/usr/bin/env python3
"""Offline test of host/tc3_shots.py: parsing, sweep expansion, the run loop with a fake device.
usage: host/tests/test_shots.py"""
import os, sys, tempfile, io, contextlib
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import tc3_shots as S

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "common"))
from testkit import check, finish

def raises(text):
    try:
        S.expand(S.parse(text)); return None
    except S.ScenarioError as e:
        return str(e)

st = S.parse("# c\n goto scope   # trailing\n\nui tdiv 7\ngen --wave pulse --duty 25\nshot my label\nwait 0.1\n")
check('parse: comments and blanks skipped, 5 steps', [s['cmd'] for s in st] == ['goto', 'ui', 'gen', 'shot', 'wait'], str(st))
check('parse: line numbers kept', [s['line'] for s in st] == [2, 4, 5, 6, 7])
check('sweep up', [s['args'] for s in S.expand(S.parse('sweep tdiv 3 6'))] == [['tdiv', str(v)] for v in (3, 4, 5, 6)])
check('sweep down with step', [s['args'][1] for s in S.expand(S.parse('sweep vdiv 8 2 -3'))] == ['8', '5', '2'])
check('sweep step 2', [s['args'][1] for s in S.expand(S.parse('sweep tdiv 0 5 2'))] == ['0', '2', '4'])
for bad, why in (('frobnicate 1', 'unknown step'), ('goto', 'missing arg'), ('ui tdiv', 'missing value'), ('wait x', 'bad wait'),
                 ('sweep tdiv 1', 'short sweep'), ('sweep tdiv a b', 'non-integer'), ('sweep tdiv 1 5 0', 'step 0'),
                 ('sweep tdiv 5 1 1', 'wrong direction'), ('goto "x', 'unbalanced quote')):
    check(f'rejected: {why}', raises(bad) is not None, str(raises(bad)))
check('gen options parsed', S.gen_args(['--wave', 'sine', '--freq', '10']).freq == 10)
try:
    S.gen_args(['--wave', 'saw']); ok = False
except S.ScenarioError:
    ok = True
check('gen bad wave rejected', ok)
check('labels are file-name safe', S.label_of({'cmd': 'gen', 'args': ['--wave', 'a/b c']}) == 'gen_--wave_a-b-c')
check('built-in scenarios all parse', all(S.expand(S.parse(t)) for t in S.BUILTIN.values()))

class Fake:
    def __init__(self, fail_on=None): self.log = []; self.fail_on = fail_on; self.mode = 'menu'
    def do(self, cmd, args):
        self.log.append((cmd, tuple(args)))
        if self.fail_on and (cmd, args[0]) == self.fail_on: raise RuntimeError('boom')
        if cmd == 'goto': self.mode = args[0]
    def mode_name(self): return self.mode
    def shot(self, path): open(path, 'wb').write(b'png')

with tempfile.TemporaryDirectory() as d:
    out = os.path.join(d, 'o')
    lines = []
    f = S.run(S.parse('goto scope\nsweep tdiv 7 8\nwait 0\nshot end'), out, Fake(), settle=0, log=lines.append)
    files = sorted(os.listdir(out))
    check('run: no failures, 4 shots + index', f == 0 and len([x for x in files if x.endswith('.png')]) == 4 and 'index.txt' in files, str(files))
    check('run: shot names numbered, wait not counted', files[0].startswith('01_goto_scope') and files[3].startswith('04_shot_end'), str(files))
    idx = open(os.path.join(out, 'index.txt')).read().splitlines()
    check('index: one line per step with result and screen', len(idx) == 4 and 'ok' in idx[0] and '[screen: scope]' in idx[1], idx[1])
    out2 = os.path.join(d, 'o2'); dev = Fake(fail_on=('ui', 'tdiv'))
    f = S.run(S.parse('goto scope\nui tdiv 3\nui vdiv 1'), out2, dev, settle=0, log=lambda *_: None)
    check('failure: stops at first failing step, its screenshot is taken',
          f == 1 and len(dev.log) == 2 and any(x.startswith('02_') for x in os.listdir(out2)), str(dev.log))
    check('failure recorded in the index', 'FAILED: boom' in open(os.path.join(out2, 'index.txt')).read())
    out3 = os.path.join(d, 'o3'); dev = Fake(fail_on=('ui', 'tdiv'))
    f = S.run(S.parse('ui tdiv 3\nui vdiv 1'), out3, dev, settle=0, keep_going=True, log=lambda *_: None)
    check('keep-going: runs on after a failure', f == 1 and len(dev.log) == 2)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = S.main(['tour', '--dry-run'])
    check('main --dry-run prints the tour', rc == 0 and buf.getvalue().count('\n') == 7 and 'goto settings' in buf.getvalue())
    with contextlib.redirect_stderr(io.StringIO()):
        check('main: missing file gives rc 2', S.main([os.path.join(d, 'nope.txt'), '--dry-run']) == 2)
        bad = os.path.join(d, 'bad.txt'); open(bad, 'w').write('gen --wave saw\n')
        check('main: bad gen option caught before touching the device', S.main([bad]) == 2)

# volume: logic of tc3.ui.set_volume against a fake Settings screen
import tc3 as T
class Settings:
    def __init__(self, vol=40, stuck=False): self.vol, self.row, self.mode, self.stuck, self.keys = vol, 0, 1, stuck, []
    def goto(self, ser, target):
        self.mode = {'settings': 6, 'menu': 1}[target]
        if target == 'settings': self.row = 0
    def press(self, ser, key, settle=0): 
        self.keys.append(key)
        if self.mode != 6: return
        if key == 'down': self.row = min(5, self.row + 1)
        elif key == 'up': self.row = max(0, self.row - 1)
        elif key == 'left' and self.row == 2 and not self.stuck: self.vol = max(0, self.vol - 1)
        elif key == 'right' and self.row == 2 and not self.stuck: self.vol = min(100, self.vol + 1)
    def read(self, ser, addr, n):
        return bytes([self.row if addr == T.SETTINGS_ROW_ADDR else self.vol])
import tc3.ui as U
real = (U.goto_screen, U.press, U.read_mem)
def with_fake(f, fake):
    U.goto_screen, U.press, U.read_mem = fake.goto, fake.press, fake.read
    try: return f()
    finally: U.goto_screen, U.press, U.read_mem = real
fk = Settings(40)
prev = with_fake(lambda: T.set_volume(None, 0), fk)
check('set_volume: returns the old value, ends at 0, back on the menu', prev == 40 and fk.vol == 0 and fk.mode == 1, str((prev, fk.vol, fk.mode)))
check('set_volume: went down to the Volume row first, 40 lefts', fk.keys[:2] == ['down', 'down'] and fk.keys.count('left') == 40, str(fk.keys[:4]))
prev = with_fake(lambda: T.set_volume(None, 40), fk)
check('set_volume: restores 40', prev == 0 and fk.vol == 40 and fk.keys.count('right') == 40)
fk = Settings(40); keys0 = len(fk.keys)
prev = with_fake(lambda: T.set_volume(None, 40), fk)
check('set_volume: already at target presses nothing but the row keys', fk.keys.count('left') + fk.keys.count('right') == 0)
fk = Settings(40, stuck=True)
try:
    with_fake(lambda: T.set_volume(None, 0), fk); ok = False
except T.UiError as e:
    ok = 'stuck' in str(e) and fk.mode == 1
check('set_volume: a value that does not move raises, still leaves Settings', ok)
try:
    T.set_volume(None, 101); ok = False
except T.UiError:
    ok = True
check('set_volume: 101 rejected', ok)

finish()
