"""Command line of tc3_client.py."""
import argparse
import sys
import time

from . import *
from .volts import cal_volts   # noqa: F401
from . import wire

DOC = """DSO-TC3 Custom Firmware Host Client (USB CDC, /dev/ttyACM0).

Commands:
  info                  Extension version banner (0x5F)
  snap [-o file.csv]    Live DMA buffer, 600 raw ADC samples (0x50)
  get <param>           Scope state parameter (0x51)
  set <param> <val>     Scope state parameter (0x52)
  ui <field> <value>    Change a setting like a button press (0x55)
  key / goto / volume   Virtual buttons, screen navigation, beep level
  gen                   Signal generator (0x58)
  screen                LCD to PNG (0x56)
  read <addr> [len]     Device memory (0x42)
  frame [--seq N]       One ring-buffered burst frame with its metadata (0x54)
  stream [-n N] [-t S]  Poll the frame ring and print/save every new burst (0x54)
  roll [-t S] [-o f]    Slow-timebase sample stream, T/div 15..21 (0x57)
"""


def fail(prefix, e):
    sys.exit(f"{prefix}: {e}")


def stats_line(volts):
    return f"Vmax={max(volts):.3f}, Vmin={min(volts):.3f}, Vavg={sum(volts) / len(volts):.3f}, Vpp={max(volts) - min(volts):.3f}"


def cmd_info(args):
    ser = open_conn(args.port)
    ser.write(bytes([CMD_EXT_INFO]))
    resp = ser.read(32)
    if not resp or resp[0] != CMD_EXT_INFO:
        sys.exit(f"unexpected response: {resp.hex() if resp else 'timeout'}")
    print(f"Extension Version: v{resp[1]}.{resp[2]}.{resp[3]}")
    print(f"Banner: {resp[4:].decode('ascii', errors='replace').rstrip(chr(0))}")


def cmd_get(args):
    if args.param.lower() not in PARAMS:
        sys.exit(f"Unknown parameter '{args.param}'. Valid: {list(PARAMS)}")
    name = args.param.lower()
    try:
        val = get_param(open_conn(args.port), name)
    except Tc3Error as e:
        fail("get", e)
    print(f"{name} = {val} (0x{val:x})")


def cmd_set(args):
    if args.param.lower() not in PARAMS:
        sys.exit(f"Unknown parameter '{args.param}'. Valid: {list(PARAMS)}")
    name, val = args.param.lower(), int(args.value, 0)
    try:
        set_param(open_conn(args.port), name, val)
    except Tc3Error as e:
        fail("set", e)
    print(f"Set {name} = {val}: OK")


def cmd_ui(args):
    name = args.field.lower()
    if name not in UI_FIELDS:
        sys.exit("Unknown field '%s'. Valid: %s" % (name, "; ".join(f"{k} ({v})" for k, v in UI_NAMES.items())))
    try:
        ui_apply(open_conn(args.port), UI_FIELDS[name], int(args.value, 0))
    except UiError as e:
        fail(f"ui {name}={args.value}", e)
    print(f"ui {name} = {args.value}: done")


def cmd_key(args):
    name = args.key.lower()
    if name not in KEYS:
        sys.exit(f"Unknown key '{name}'. Valid: {', '.join(KEYS)}")
    ser = open_conn(args.port)
    try:
        press(ser, name)
        mode = get_mode(ser)
    except UiError as e:
        fail(f"key {name}", e)
    print(f"key {name}: sent; screen is now {MODE_NAMES.get(mode, '?')}")


def cmd_goto(args):
    try:
        mode = goto_screen(open_conn(args.port), args.screen.lower())
    except UiError as e:
        fail(f"goto {args.screen}", e)
    print(f"goto {args.screen}: now on the {MODE_NAMES.get(mode, mode)} screen")


def cmd_volume(args):
    ser = open_conn(args.port)
    try:
        if args.value is None:
            print(f"volume = {get_volume(ser)}")
            return
        prev = set_volume(ser, int(args.value, 0))
    except UiError as e:
        fail("volume", e)
    print(f"volume: {prev} -> {get_volume(ser)}")


def gen_text(cur):
    return "generator: %s %d Hz duty %d%% amplitude %.3f V" % (GEN_WAVE_NAMES.get(cur["wave"], cur["wave"]), cur["freq"], cur["duty"], cur["amp"])


def cmd_gen(args):
    ser = open_conn(args.port)
    if args.log:
        for total, lr, wave, duty, freq in gen_log(ser):
            who = "usb (0x58 set)" if lr == 0xFFFFFFFF else "stock call, returns to 0x%08x" % (lr & ~1)
            print("%s: %s %d Hz duty %d  [%d calls so far]" % (who, GEN_WAVE_NAMES.get(wave, wave), freq, duty, total))
        return
    try:
        cur = gen_get(ser)
        changes = {k: v for k, v in (("wave", GEN_WAVES.get(args.wave)), ("freq", args.freq), ("duty", args.duty), ("amp", args.amp)) if v is not None}
        if changes:
            cur.update(changes)
            gen_set(ser, cur["wave"], cur["freq"], cur["duty"], cur["amp"])
            cur = gen_get(ser)
    except UiError as e:
        fail("gen", e)
    print(gen_text(cur))


def cmd_screen(args):
    try:
        img, strips = capture_screen(open_conn(args.port), full=not args.no_redraw)
    except UiError as e:
        fail("screen", e)
    write_png(args.out, SCR_W, SCR_H, img)
    print(f"{args.out}: {SCR_W}x{SCR_H}, {len(strips)} strips {strips}")


def cmd_read(args):
    addr, length = int(args.addr, 0), int(args.len, 0)
    try:
        buf = read_mem(open_conn(args.port), addr, length)
    except Tc3Error as e:
        fail("read", e)
    if args.out:
        with open(args.out, "wb") as f:
            f.write(buf)
        print(f"Dumped {len(buf)} bytes from 0x{addr:08x} -> {args.out}")
        return
    for i in range(0, len(buf), 16):
        chunk = buf[i:i + 16]
        print(f"0x{addr + i:08x}:  {' '.join(f'{b:02x}' for b in chunk):<48}  |{''.join(chr(b) if 32 <= b <= 126 else '.' for b in chunk)}|")


def cmd_snap(args):
    ser = open_conn(args.port)
    t0 = time.time()
    try:
        hdr, samples = snap(ser)
    except Tc3Error as e:
        fail("snap", e)
    elapsed = time.time() - t0
    probe, tdiv = hdr["probe"], hdr["tdiv"]
    print(f"Frame Header: State={'STOP' if hdr['run_stop'] else 'RUN'}, V/div={hdr['vdiv']}, T/div={tdiv}, "
          f"Probe={'X10' if probe else 'X1'}, Gain={hdr['gain']}, Edge={'FALLING' if hdr['edge'] else 'RISING'}, "
          f"TrigPos={hdr['trig_pos']}, Samples={hdr['total']}, Chunks={hdr['chunks']}")
    print(f"Read {len(samples)} samples in {elapsed * 1000:.1f} ms ({len(samples) * 2 / elapsed / 1024:.1f} KB/s)")
    print(f"Stats: Min={min(samples)}, Max={max(samples)}, Vpp={max(samples) - min(samples)}, Mean={sum(samples) / len(samples):.1f}")
    volts = None
    if tdiv >= ROLL_MIN_TDIV:
        print("Volts: not converted (T/div >= 15 uses the slow path, formula unverified)")
    elif probe > 1:
        print(f"Volts: not converted (unknown probe selector {probe})")
    else:
        if read_gain(ser) != hdr["gain"]:
            print("Warning: gain changed during capture; volts use the first value")
        volts = to_volts(samples, hdr["gain"], probe, hdr["vdiv"])
        print("Volts: " + stats_line(volts))
    if args.out:
        with open(args.out, "w") as f:
            f.write("index,adc_raw,volts\n" if volts else "index,adc_raw\n")
            for i, s in enumerate(samples):
                f.write(f"{i},{s},{volts[i]:.4f}\n" if volts else f"{i},{s}\n")
        print(f"Saved {len(samples)} samples to {args.out}")


def cmd_frame(args):
    ser = open_conn(args.port)
    try:
        hdr, samples = fetch_frame(ser, args.seq)
    except FrameError as e:
        fail("Frame error", e)
    volts = frame_volts(hdr, samples)
    print(f"Frame seq={hdr['seq']} (latest {hdr['latest']}), V/div={hdr['vdiv']}, T/div={hdr['tdiv']}, "
          f"Probe={'X10' if hdr['probe'] else 'X1'}, Gain={hdr['gain']}, rate={hdr['rate']:.6g}, mode={hdr['mode']}")
    print(f"Raw: Min={min(samples)}, Max={max(samples)}, Mean={sum(samples) / len(samples):.1f}")
    if hdr["rate"] > 0:
        print(f"Sample rate: {hdr['rate']:.6g} kS/s (interval {1000.0 / hdr['rate']:.4g} us, "
              f"window {len(samples) * 1000.0 / hdr['rate'] / 1000.0:.4g} ms)")
    if volts:
        print(f"Volts: Vmax={max(volts):.3f}, Vmin={min(volts):.3f}, Vavg={sum(volts) / len(volts):.3f}")
    if args.out:
        with open(args.out, "w") as f:
            f.write("index,t_us,adc_raw,volts\n" if volts else "index,t_us,adc_raw\n")
            for i, v in enumerate(samples):
                t_us = i * 1000.0 / hdr["rate"] if hdr["rate"] > 0 else 0.0
                f.write(f"{i},{t_us:.3f},{v},{volts[i]:.4f}\n" if volts else f"{i},{t_us:.3f},{v}\n")
        print(f"Saved {len(samples)} samples to {args.out}")


def cmd_stream(args):
    ser = open_conn(args.port)
    out = open(args.out, "w") if args.out else None
    if out:
        out.write("seq,index,adc_raw,volts\n")
    last = got = dropped = 0
    t_start = t_print = time.time()
    try:
        while True:
            now = time.time()
            if (args.frames and got >= args.frames) or (args.time and now - t_start >= args.time):
                break
            try:
                head = frame_header(ser, 0)
                if head["latest"] == last:
                    time.sleep(0.002)
                    continue
                hdr, samples = fetch_frame(ser, hdr=head)
            except FrameError as e:
                if e.code in (ERR_EMPTY, TIMEOUT):
                    time.sleep(0.01)
                    continue
                if e.code == ERR_NO_FRAME:
                    continue                      # expired while reading: pick up the newest next loop
                raise
            if last and hdr["seq"] > last + 1:
                dropped += hdr["seq"] - last - 1
            last = hdr["seq"]
            got += 1
            volts = frame_volts(hdr, samples)
            if out:
                for i, v in enumerate(samples):
                    out.write(f"{hdr['seq']},{i},{v}," + (f"{volts[i]:.4f}\n" if volts else "\n"))
            if args.verbose or now - t_print >= 1.0:
                t_print = now
                vtxt = f"Vavg={sum(volts) / len(volts):.3f} Vpp={max(volts) - min(volts):.3f}" if volts else f"raw mean={sum(samples) / len(samples):.0f}"
                print(f"seq={hdr['seq']} V/div={hdr['vdiv']} T/div={hdr['tdiv']} {vtxt}  frames={got} dropped={dropped} "
                      f"({got / (now - t_start + 1e-9):.1f} fps)", flush=True)
    except KeyboardInterrupt:
        pass
    el = time.time() - t_start
    print(f"Stream done: {got} frames in {el:.1f}s ({got / max(el, 1e-9):.1f} fps), {dropped} dropped"
          + (f", saved to {args.out}" if out else ""))
    if out:
        out.close()


def cmd_roll(args):
    ser = open_conn(args.port)
    out = open(args.out, "w") if args.out else None
    if out:
        out.write("index,time_s,adc_raw,vdiv,volts\n")
    nxt = epoch = last_v = None
    got = lost = 0
    t_start = t_print = time.time()
    started = False
    try:
        while not args.time or time.time() - t_start < args.time:
            try:
                hdr = roll_header(ser)
            except RollError as e:
                if e.code in (TIMEOUT, ERR_EMPTY):
                    time.sleep(0.1)
                    continue
                raise
            if hdr["tdiv"] < ROLL_MIN_TDIV:
                if time.time() - t_print >= 1.0:
                    t_print = time.time()
                    print(f"T/div index {hdr['tdiv']} is not a roll timebase (need >= {ROLL_MIN_TDIV}); waiting", flush=True)
                time.sleep(0.2)
                continue
            if epoch != hdr["epoch"] or nxt is None or nxt < hdr["base"]:
                if epoch is not None and not args.quiet:
                    print(f"T/div changed to {hdr['tdiv']}: restarting at sample {hdr['base']}", flush=True)
                epoch, nxt = hdr["epoch"], hdr["base"]
                started = False
            if hdr["count"] - nxt > hdr["ring"] - 4:        # older samples are gone from the ring
                skip = hdr["count"] - nxt - (hdr["ring"] - 4)
                if started:                                 # only a gap after we began reading is a loss
                    lost += skip
                nxt += skip
            started = True
            period = 1.0 / (hdr["rate"] * 1000.0) if hdr["rate"] > 0 else 0.0
            try:
                while nxt < hdr["count"]:
                    chunk = roll_read(ser, nxt)
                    if not chunk:
                        break
                    for i, (raw, vd) in enumerate(chunk):
                        v = last_v = roll_volts(raw, vd, hdr["probe"])
                        if out:
                            out.write(f"{nxt + i},{(nxt + i - hdr['base']) * period:.4f},{raw},{vd},"
                                      + (f"{v:.4f}\n" if v is not None else "\n"))
                        if not args.quiet and args.verbose:
                            print(f"{nxt + i}: raw={raw} vdiv={vd} V={'?' if v is None else f'{v:.3f}'}", flush=True)
                    nxt += len(chunk)
                    got += len(chunk)
            except RollError as e:
                if e.code != ERR_NO_FRAME:
                    raise
                lost += 1                                   # lapped mid-read: resync from the header next loop
                nxt = hdr["count"]
            now = time.time()
            if not args.quiet and now - t_print >= 1.0 and got:
                t_print = now
                print(f"count={hdr['count']} T/div={hdr['tdiv']} V/div={hdr['vdiv']} rate={hdr['rate']:.4g} kS/s "
                      f"samples={got} lost={lost} last={'?' if last_v is None else f'{last_v:.3f} V'}", flush=True)
            if out:
                out.flush()
            time.sleep(max(0.05, min(1.0, 20 * period)) if period else 0.2)
    except KeyboardInterrupt:
        pass
    print(f"Roll done: {got} samples in {time.time() - t_start:.1f}s, {lost} lost"
          + (f", saved to {args.out}" if out else ""))
    if out:
        out.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=DOC, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-p", "--port", help="Serial port (auto-detects 19f5:5740)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, func, help, *opts):
        p = sub.add_parser(name, help=help)
        for flags, kw in opts:
            p.add_argument(*flags, **kw)
        p.set_defaults(func=func)

    out = lambda h="Save samples to CSV file": (("-o", "--out"), {"help": h})
    add("info", cmd_info, "Query extension info")
    add("snap", cmd_snap, "Single frame capture readout", out())
    add("get", cmd_get, "Get scope state parameter", (("param",), {"help": "one of: " + ", ".join(PARAMS)}))
    add("set", cmd_set, "Set scope state parameter", (("param",), {"help": "one of: " + ", ".join(PARAMS)}), (("value",), {}))
    add("ui", cmd_ui, "Change a setting the way a button press would (0x55), label included",
        (("field",), {"help": "one of: " + ", ".join(UI_FIELDS)}), (("value",), {"help": "absolute target value (docs/host-tools.md)"}))
    add("key", cmd_key, "Press a virtual button (0x55 field 0x20): " + ", ".join(KEYS), (("key",), {}))
    add("volume", cmd_volume, "Show or set the stock Volume (beep level, 0..100) through the Settings screen",
        (("value",), {"nargs": "?", "help": "0..100; omit to show"}))
    add("goto", cmd_goto, "Navigate to a screen with virtual buttons: menu, mtester, scope, generator, tools, settings", (("screen",), {}))
    add("gen", cmd_gen, "Show or set the signal generator (0x58); no options = show",
        (("--wave",), {"choices": list(GEN_WAVES)}), (("--freq",), {"type": int, "help": "Hz, 1..100000"}),
        (("--duty",), {"type": int, "help": "percent 0..100 (pulse and ramp)"}),
        (("--amp",), {"type": float, "help": "volts 0.01..3.3 (output swings 0..amp)"}),
        (("--log",), {"action": "store_true", "help": "show the last stock apply calls"}))
    add("screen", cmd_screen, "Capture the LCD contents to a PNG (0x56 flush hook + 0x42 reads)",
        (("-o", "--out"), {"default": "screen.png"}), (("--no-redraw",), {"action": "store_true", "help": "capture whatever is flushed"}))
    add("read", cmd_read, "Read device memory (0x42)", (("addr",), {}), (("len",), {"nargs": "?", "default": "64"}), out("Save raw bytes to file"))
    add("frame", cmd_frame, "Read one ring-buffered burst frame (0x54)",
        (("--seq",), {"type": int, "default": 0, "help": "Frame sequence number (0 = latest)"}), out())
    add("stream", cmd_stream, "Poll the frame ring and report/save every new burst",
        (("-n", "--frames"), {"type": int, "default": 0}), (("-t", "--time"), {"type": float, "default": 0}),
        out("Save all frames to a long-format CSV (seq,index,adc_raw,volts)"), (("-v", "--verbose"), {"action": "store_true"}))
    add("roll", cmd_roll, "Record the slow-timebase sample stream, T/div 15..21 (0x57)",
        (("-t", "--time"), {"type": float, "default": 0}), out("Save samples to CSV (index,time_s,adc_raw,vdiv,volts)"),
        (("-v", "--verbose"), {"action": "store_true"}), (("-q", "--quiet"), {"action": "store_true"}))

    args = ap.parse_args(argv)
    args.func(args)
