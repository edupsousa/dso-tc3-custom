#!/usr/bin/env python3
"""Run a scenario on the DSO-TC3 and save a screenshot after each step.

usage: host/tc3_shots.py SCENARIO [-o DIR] [--settle S] [--keep-going] [--quiet] [--dry-run] [--port P]
       host/tc3_shots.py --list

SCENARIO is a built-in name (see --list) or a text file with one step per line ('#' starts a comment):

    goto <menu|mtester|scope|generator|tools|settings>   navigate with virtual buttons
    key <back|select|up|down|left|right|plus|minus|hold-select>
    ui <field> <value>                                   scope setting (position, vdiv, probe, tdiv, ...)
    gen [--wave W] [--freq F] [--duty D] [--amp A]       signal generator
    sweep <field> <from> <to> [step]                     'ui' for each value, one screenshot per value
    wait <seconds>                                       pause, no screenshot
    shot [label words]                                   extra screenshot with no action

Every action step except 'wait' is followed by a screenshot (after --settle seconds) named NN_label.png in DIR,
and DIR/index.txt records for each step: the command, its result, the screen mode, the time. The run stops at the
first failing step (its screenshot is still taken) unless --keep-going is given.
The device beeps on every virtual key press (stock behaviour); --quiet sets the stock Volume to 0 before the run
and restores it afterwards (about 40 key presses each way, through the Settings screen). Needs the extension image with 0x55 field 0x20.
"""
import argparse, os, re, shlex, sys, time

BUILTIN = {
    # every screen reachable from the menu, and back
    "tour": """
        goto menu
        goto mtester
        goto scope
        goto generator
        goto tools
        goto settings
        goto menu
    """,
    # scope timebase steps (T/div 200 us .. 20 ms); needs a signal on the probe and the scope view
    "scope-tdiv": """
        goto scope
        sweep tdiv 7 13
        goto menu
    """,
    # the five generator waveforms in the generator screen
    "generator-waves": """
        goto generator
        gen --wave sine --freq 1000 --amp 2.0
        gen --wave square --freq 1000 --amp 2.0
        gen --wave pulse --freq 500 --duty 25
        gen --wave triangle --freq 200 --amp 1.5
        gen --wave ramp --freq 200 --amp 1.5
        gen --wave dc --amp 1.0
        goto menu
    """,
}

ACTIONS = {"goto", "key", "ui", "gen", "sweep", "wait", "shot"}


class ScenarioError(Exception):
    pass


def parse(text):
    """Scenario text -> list of steps (dict: cmd, args, line). 'sweep' stays one step here; run() expands it."""
    steps = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            words = shlex.split(line)
        except ValueError as e:
            raise ScenarioError(f"line {n}: {e}")
        cmd, args = words[0].lower(), words[1:]
        if cmd not in ACTIONS:
            raise ScenarioError(f"line {n}: unknown step '{cmd}' (valid: {', '.join(sorted(ACTIONS))})")
        need = {"goto": (1, 1), "key": (1, 1), "ui": (2, 2), "sweep": (3, 4), "wait": (1, 1), "shot": (0, 99), "gen": (1, 99)}[cmd]
        if not need[0] <= len(args) <= need[1]:
            raise ScenarioError(f"line {n}: '{cmd}' takes {need[0]}..{need[1]} arguments, got {len(args)}")
        if cmd == "wait":
            try:
                float(args[0])
            except ValueError:
                raise ScenarioError(f"line {n}: wait needs a number of seconds")
        if cmd == "sweep":
            try:
                [int(a, 0) for a in args[1:]]
            except ValueError:
                raise ScenarioError(f"line {n}: sweep needs integer from/to/step")
            if len(args) == 4 and int(args[3], 0) == 0:
                raise ScenarioError(f"line {n}: sweep step must not be 0")
        steps.append({"cmd": cmd, "args": args, "line": n})
    return steps


def expand(steps):
    """Replace each 'sweep field a b [s]' by one 'ui field value' step per value (a..b inclusive)."""
    out = []
    for st in steps:
        if st["cmd"] != "sweep":
            out.append(st)
            continue
        field, a, b = st["args"][0], int(st["args"][1], 0), int(st["args"][2], 0)
        step = int(st["args"][3], 0) if len(st["args"]) == 4 else (1 if b >= a else -1)
        if (b - a) * step < 0:
            raise ScenarioError(f"line {st['line']}: sweep step {step} never gets from {a} to {b}")
        v = a
        while (v <= b) if step > 0 else (v >= b):
            out.append({"cmd": "ui", "args": [field, str(v)], "line": st["line"]})
            v += step
    return out


def label_of(st):
    text = "_".join([st["cmd"]] + st["args"])
    return re.sub(r"[^A-Za-z0-9._=-]+", "-", text).strip("-")[:60]


def gen_args(args):
    p = argparse.ArgumentParser(prog="gen", add_help=False, exit_on_error=False)
    p.add_argument("--wave", choices=("sine", "square", "pulse", "triangle", "ramp", "dc"))
    p.add_argument("--freq", type=int)
    p.add_argument("--duty", type=int)
    p.add_argument("--amp", type=float)
    try:
        return p.parse_args(args)
    except (argparse.ArgumentError, SystemExit) as e:
        raise ScenarioError(f"bad gen options {args}: {e}")


def run(steps, outdir, dev, settle=0.6, keep_going=False, log=print):
    """Execute steps on `dev` (see Device below). Returns the number of failed steps."""
    os.makedirs(outdir, exist_ok=True)
    index = open(os.path.join(outdir, "index.txt"), "w")
    failed = 0
    n = 0
    for st in expand(steps):
        cmd, args = st["cmd"], st["args"]
        if cmd == "wait":
            time.sleep(float(args[0]))
            continue
        n += 1
        result = "ok"
        try:
            if cmd != "shot":
                dev.do(cmd, args)
        except Exception as e:                       # UiError and friends: record, still take the picture
            result = f"FAILED: {e}"
            failed += 1
        time.sleep(settle)
        name = f"{n:02d}_{label_of(st)}.png"
        try:
            mode = dev.mode_name()
        except Exception as e:
            mode = f"? ({e})"
        try:
            dev.shot(os.path.join(outdir, name))
            shot = name
        except Exception as e:
            shot = f"no screenshot ({e})"
            if result == "ok":
                result = "FAILED: screenshot"
                failed += 1
        line = f"{n:02d}  {cmd} {' '.join(args)}  ->  {result}  [screen: {mode}]  {shot}  @{time.strftime('%H:%M:%S')}"
        index.write(line + "\n"); index.flush()
        log(line)
        if result.startswith("FAILED") and not keep_going:
            log("stopping at the first failure (use --keep-going to continue)")
            break
    index.close()
    return failed


class Device:
    """The real device, through host/tc3_client.py on one serial connection."""
    def __init__(self, port=None):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import tc3 as T
        self.T = T
        self.ser = T.open_conn(port)

    def do(self, cmd, args):
        T, ser = self.T, self.ser
        if cmd == "goto":
            T.goto_screen(ser, args[0].lower())
        elif cmd == "key":
            if args[0].lower() not in T.KEYS:
                raise T.UiError(f"unknown key '{args[0]}'")
            T.press(ser, args[0].lower())
        elif cmd == "ui":
            if args[0].lower() not in T.UI_FIELDS:
                raise T.UiError(f"unknown ui field '{args[0]}'")
            T.ui_apply(ser, T.UI_FIELDS[args[0].lower()], int(args[1], 0))
        elif cmd == "gen":
            a = gen_args(args)
            cur = T.gen_get(ser)
            T.gen_set(ser, cur["wave"] if a.wave is None else T.GEN_WAVES[a.wave],
                      cur["freq"] if a.freq is None else a.freq,
                      cur["duty"] if a.duty is None else a.duty,
                      cur["amp"] if a.amp is None else a.amp)

    def mode_name(self):
        m = self.T.get_mode(self.ser)
        return self.T.MODE_NAMES.get(m, str(m))

    def shot(self, path):
        img, _ = self.T.capture_screen(self.ser)
        self.T.write_png(path, self.T.SCR_W, self.T.SCR_H, img)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("scenario", nargs="?", help="built-in name or scenario file")
    ap.add_argument("-o", "--out", help="output directory (default: shots-<scenario>-<time>)")
    ap.add_argument("--settle", type=float, default=0.6, help="seconds to wait before each screenshot (default 0.6)")
    ap.add_argument("--keep-going", action="store_true", help="continue after a failed step")
    ap.add_argument("--quiet", action="store_true", help="set the stock Volume to 0 for the run, restore it afterwards")
    ap.add_argument("--dry-run", action="store_true", help="parse and print the expanded steps, touch nothing")
    ap.add_argument("--list", action="store_true", help="list the built-in scenarios")
    ap.add_argument("--port")
    a = ap.parse_args(argv)
    if a.list:
        for k, v in BUILTIN.items():
            print(f"{k}:\n" + "\n".join("    " + l.strip() for l in v.strip().splitlines()))
        return 0
    if not a.scenario:
        ap.error("a scenario is required (or --list)")
    try:
        text = BUILTIN[a.scenario] if a.scenario in BUILTIN else open(a.scenario).read()
        steps = parse(text)
        for st in steps:                            # validate gen options before touching the device
            if st["cmd"] == "gen":
                gen_args(st["args"])
        expanded = expand(steps)
    except (OSError, ScenarioError) as e:
        print(f"scenario: {e}", file=sys.stderr)
        return 2
    if a.dry_run:
        for i, st in enumerate(expanded, 1):
            print(f"{i:02d}  {st['cmd']} {' '.join(st['args'])}")
        return 0
    name = os.path.splitext(os.path.basename(a.scenario))[0]
    out = a.out or f"shots-{name}-{time.strftime('%Y%m%d-%H%M%S')}"
    dev = Device(a.port)
    orig_volume = dev.T.get_volume(dev.ser) if a.quiet else None      # a RAM read, works on any screen
    try:
        if a.quiet:
            dev.T.set_volume(dev.ser, 0)
            print(f"volume {orig_volume} -> 0 for the run")
        failed = run(steps, out, dev, settle=a.settle, keep_going=a.keep_going)
    finally:                                     # also when set_volume stopped halfway or a step raised
        if a.quiet and dev.T.get_volume(dev.ser) != orig_volume:
            dev.T.set_volume(dev.ser, orig_volume)
            print(f"volume restored to {orig_volume}")
    print(f"{out}: {'all steps ok' if not failed else str(failed) + ' failed step(s)'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
