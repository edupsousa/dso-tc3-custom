# Host tools for the DSO-TC3 custom firmware

All tools live in `host/` and talk to the device over USB CDC (`/dev/ttyACM0`,
VID:PID `19f5:5740`) using the extension in `firmware_ext/`. The device must run
the custom image (`firmware/custom-builds/DSO-TC3_custom-ext.s19`). Only one
program can hold the serial port at a time: stop the SCPI server before using
`tc3_client.py` or `tc3_sr.py`.

## What the device can deliver

- **Waveforms** come from a ring of burst frames: each frame is a separate
  600-sample acquisition, about 15 per second (fewer at 5 ms/div and slower).
  They are **not** a gap-free stream.
- Frames exist for T/div indices 0..14 (1 us to 50 ms per division). At 1, 2 and
  5 us/div the acquisition is fixed: both ADCs interleaved at 10.29 MS/s (5.14
  MS/s at 5 us), 600 samples = 58 us (117 us). T/div 0 and 1 are identical; only
  the on-screen zoom differs. The rate is the measured 144 MHz / 14, not the
  firmware's rounded 10280 kS/s.
- **Roll mode, T/div 15..21 (100 ms to 10 s per division):** no frames; the device
  records every sample (25 per division, 250 down to 2.5 S/s) in a ring with an
  absolute sample counter (command 0x57). This stream is gap-free. The on-screen
  trace scrolls continuously there and ignores the trigger; that is the stock
  roll display.
- Sample rate, gain and range are stored with every frame. Volts are computed
  with the voltage conversion formula, checked
  against the device's own display. The scope's accuracy is about 5% against a
  multimeter.
- `set vdiv` changes gain and relay but not the on-screen label until a
  button press. Setting T/div over USB is not verified.

## The `tc3` library

`host/tc3/` is the Python library behind every tool here (`import tc3 as T`; every function takes the serial connection from `T.open_conn()`).
`wire.py` holds the protocol constants (checked against `firmware_ext/include/protocol.h` by `host/tests/check_protocol.py`), `link.py` the shared
request/reply code, and `ui`, `gen`, `frames`, `roll`, `screen`, `memory`, `volts`, `params` one feature each. Errors derive from `T.Tc3Error` (`.code`).
`host/tc3scpi/` is the SCPI server behind `tc3_scpi.py` (interpreter, generator commands, Rigol emulation, transports).
`./run_tests.sh` in the repository root runs all offline tests.

## tc3_client.py

```
tc3_client.py info
tc3_client.py snap [-o file.csv]        # live DMA buffer, may tear
tc3_client.py frame [--seq N] [-o file.csv]   # ring frame with metadata, tear-free
tc3_client.py stream [-n N] [-t SECONDS] [-o file.csv] [-v]
tc3_client.py roll [-t SECONDS] [-o file.csv] [-v] [-q]   # T/div 15..21: samples as they arrive
tc3_client.py get|set <param> [value]
tc3_client.py read <addr> [len]
```

## tc3_scpi.py (SCPI over TCP)

```
host/tc3_scpi.py            # listens on 127.0.0.1:5025
host/tc3_scpi.py --host 0.0.0.0 --port-tcp 5025
host/tc3_scpi.py --stdio    # commands from stdin, for testing
```

Raw socket, newline terminated. Works with `nc`, PyVISA (`TCPIP::127.0.0.1::5025::SOCKET`
with `read_termination='\n'`), InstrumentKit or any script. There is no
authentication, so keep it on loopback unless you trust the network.

| Command | Meaning |
|---|---|
| `*IDN?`, `*CLS`, `*OPC?`, `SYSTem:ERRor?` | identity, error queue |
| `:RUN`, `:STOP` | run/stop flag (the display freezes; frames and roll samples keep coming) |
| `CHANnel1:SCALe?` / `CHANnel1:SCALe <V/div>` | volts per division (nearest step; label stays stale) |
| `CHANnel1:PROBe?` | probe multiplier (1 or 10) |
| `TIMebase:SCALe?` / `TIMebase:SCALe <s/div>` | seconds per division (nearest step, any of the 22) |
| `TRIGger:SWEep?` / `TRIGger:SWEep {AUTO\|NORMal\|SINGle}` | trigger mode (stock `+0x3c`, through the `ui` path: label and capture restart follow; SINGLE clears STOP). Error -224 for another word |
| `TRIGger:EDGE:SLOPe?` / `... {POSitive\|NEGative}` | trigger edge (stock `+0x40`) |
| `:AUToscale` (Rigol: `:AUTO`) | the scope's own Auto routine, started with a virtual select press; waits until V/div and T/div stop changing (error -221 outside the scope view). Hardware: V/div 7 and T/div 12 settled on 1 V/div, 500 us/div for a 1 kHz 3 V sine |
| `ACQuire:SRATe?`, `WAVeform:XINCrement?` | sample rate (Hz), sample interval (s) |
| `WAVeform:FORMat {ASCii\|FLOat}` | ASCII: comma separated volts. FLOat: `#<n><len>` block of float32 LE |
| `WAVeform:PREamble?` | `format,type,points,count,xincrement,xorigin,xreference,yincrement,yorigin,yreference` |
| `WAVeform:DATA?` | T/div 3..14: waits for a new frame (up to 3 s), returns 600 volts. T/div 15..21: returns the newest 300 roll samples (the screen), oldest first, fewer right after a T/div change |
| `MEASure:VMAX?`/`VMIN?`/`VAVerage?`/`VPP?` | computed on a fresh frame, or on the roll window |
| `FRAMe:SEQuence?` | latest frame sequence number |

Mnemonics accept long or short forms. An unknown query sets error -113 and,
as is usual in SCPI, sends no reply. Error -230 means the device is at a
setting with no data (T/div 0..2, or no roll sample yet); -231 means the stored frame is from an older setting.

## tc3_sr.py (sigrok / PulseView)

```
host/tc3_sr.py capture -o out.sr [-n FRAMES]
sigrok-cli -i out.sr -O csv
```

Writes a sigrok v2 session: one analog channel `CH1`, volts as float32, the
device's real sample rate. With `-n > 1` frames are concatenated and the joins
are not contiguous in time (the format has no gap marker); the default is one
frame. `tc3_sr.py selftest -o x.sr` writes a synthetic sine for checking the
sigrok side without a device.

```
host/tc3_sr.py roll -o out.sr [-n SAMPLES | -t SECONDS]    # T/div 15..21
```

Records the gap-free roll stream, starting with the samples already on the
screen. The format stores the rate as whole Hz, so a rate that is not (12.5 and
2.5 S/s) is written with every sample repeated twice (25 and 5 Hz) to keep the
time axis exact; the tool says so. 125.217 S/s (200 ms/div) is written as 125 Hz
(0.17% off). Stops with an error if T/div or the probe changes during the run.

Offline tests: `host/tests/test_roll_host.py` (fake serial device),
`firmware_ext/tests/test_ui_hook.py` (firmware in Unicorn), `host/tests/test_rigol_emu.py` (Rigol emulation, incl. real `sigrok-cli`).

## Rigol DS emulation for PulseView (`tc3_scpi.py --rigol`)

```
host/tc3_scpi.py --rigol [--port-tcp 5025] [--trace]
sigrok-cli -d rigol-ds:conn=tcp-raw/127.0.0.1/5025 --scan
sigrok-cli -d rigol-ds:conn=tcp-raw/127.0.0.1/5025 -C CH1 --frames 10 -O srzip -o out.sr
```

In PulseView: *Connect to Device*, driver **Rigol DS**, protocol **Raw TCP**, host
`127.0.0.1`, port `5025`, *Scan*. The server then answers as a Rigol **DS1052E**
(old DS1000 protocol; libsigrok `rigol-ds` PROTOCOL_V2) so the stock libsigrok
driver works unmodified. `host/tests/test_rigol_emu.py` runs the real `sigrok-cli`
against a synthetic device (`--demo`) and passes; the same flow was run once
against the hardware (T/div 5 us, 1 MHz sine: 3 frames, 5.142857 MHz). **PulseView
GUI checked by the user (T/div 200 us, 1 kHz sine): 4.8 cycles over 4.8 ms, as expected.**

What is real, and what is approximated:

- **Samples:** each live frame is one 600-sample device frame (burst timebases) or
  the newest 600 roll samples (T/div 15..21; shorter right after a T/div change,
  padded on the left with the first sample). Frames are separate acquisitions
  (about 15 per second), not a gap-free stream; PulseView shows them one after
  another.
- **Sample rate and time axis are correct**, but `:TIM:SCAL?` is **not the
  device's T/div**: the driver derives its sample rate as `50 / timebase` assuming
  a 600-point frame spans 12 divisions, while ours spans 24. The server therefore
  reports `50 / real sample rate`, which is about twice the real T/div (400 us at
  200 us/div). Choosing a timebase in PulseView picks the T/div whose reported
  value is nearest, so the labels in PulseView are 2x the labels on the scope.
- **Amplitude is 8-bit,** as on a real DS1052E: the driver decodes
  `volts = (128 - byte) x (V/div / 25.6)`, so the full range is +-5 divisions at
  the V/div the driver read last. The device's own 12-bit data is quantised to that
  (1 V/div: 39 mV per step). Values beyond +-5 divisions clip. The server encodes with
  the scale the driver last read, so a V/div change made on the scope after
  connecting keeps correct volts but a coarser or clipped trace; reconnect (or change
  V/div from PulseView) to re-read it.
- **Settings from PulseView** map to the device: timebase, V/div (`:CHAN1:SCAL`),
  probe (`:CHAN1:PROB`), AC/DC (`:CHAN1:COUP`), trigger slope. Trigger level and
  source, horizontal offset and memory depth are accepted or answered with fixed
  values and do nothing. `:RUN` runs the scope; there is no single/stop control.
- **Channel 2 does not exist:** it reads as a flat 0 V line if enabled.
- **What PulseView shows:** one frame at a time, redrawn from t = 0 (no trigger alignment, so the
  phase changes from frame to frame). Its channel row autoranges in 1-2-5 steps around 0 V (a 0 to
  3.2 V signal shows at 5 V/div in the upper half of the row). Use the zoom-to-fit button and drag
  the row's lower edge to enlarge the view. Use a slow signal: at 5 us/div a frame is only 117 us.
- libsigrok logs `Read should have been completed` once per frame. Harmless: its
  TCP transport only counts a read as complete when it comes back short, and this
  driver always reads exactly the 600 data bytes. A trailing newline after the
  block would fix the message but breaks the next frame, so none is sent.
- **Roll timebases through the driver (hardware, 1 Hz sine on the probe, verified 2026-10-31).** For a reported timebase of
  50 ms/div and up, libsigrok polls `:TRIG:STAT?` in two phases (until the status is not `T`/`A`, then until it is). The server
  used to answer `AUTO` always, so the first phase never ended and `sigrok-cli` retried for ever at every roll timebase. It now
  answers `RUN` once after each `:RUN` and each data block, then `AUTO` (tests: `host/tests/test_rigol_emu.py`, new slow-timebase check).
  Results: 200 ms, 1 s and 2 s complete (2 frames x 600 samples) and match the device's 250, 50 and 25 S/s; at 200 ms the
  autocorrelation period is 250 samples, 1.004 s at the driver's rate (249 Hz in the `.sr` metadata, whole Hz). **Limitation:** the
  driver's timebase list has no 400 ms or 4 s; choosing 500 ms or 5 s gives the nearest device rate (T/div 16, 125.2 S/s;
  T/div 19, 12.5 S/s) while the driver assumes 100 or 10 S/s, so the time axis is 24 % off at those two (measured: a 1 Hz sine
  shows 1.24 s at "500 ms"). Use 200 ms, 1 s, 2 s, 10 s or 20 s for exact axes. Right after a T/div change the roll history is
  young and the frame is mostly padding (first sample held on the left); wait one window length before judging the period.
  Slow frames: 600 samples at 2.5 S/s is 4 minutes of history, so T/div 20..21 frames stay padded for a long time.
- **Settings from the driver (hardware):** `-g CH1 -c vdiv=2V`, `-g CH1 -c coupling=AC`, `-g CH1 -c probe_factor=1` and
  `-c triggerslope=f` each reached the device (V/div index 3 -> 4, coupling DC -> AC, probe X10 -> X1, edge rising -> falling) and every
  capture still returned 600 samples. All were restored afterwards with `tc3_client.py ui`.
- The device identifies as a Rigol only in this mode. `--trace` prints every
  command and reply size; `--demo` serves a synthetic sine with no hardware.

## PyVISA

`host/tests/test_pyvisa.py` drives the SCPI server with PyVISA over a raw socket resource
(`TCPIP::127.0.0.1::5025::SOCKET`, termination `\n`). It is not installed system-wide; run it in a throwaway environment:

```
uv run --with pyvisa --with pyvisa-py python host/tests/test_pyvisa.py            # demo device, no hardware (25 checks)
python3 host/tc3_scpi.py &                                                  # then, against the real device:
uv run --with pyvisa --with pyvisa-py python host/tests/test_pyvisa.py --hw
```

Checked on the hardware (2026-10-31, 23 checks, 0 failures; the 17 are the SOURce, error queue and scope read, 6 more are `TRIGger:SWEep` and `:EDGE:SLOPe`): `*IDN?`, `SOURce:FUNCtion` (all six waveforms), `:FREQuency`, `:VOLTage`,
`:DCYCle` set and query, error -222 for an out-of-range frequency and -224 for a bad function, the queue read with `SYST:ERR?`, `WAV:DATA?`
(600 samples) and `ACQ:SRAT?`. It leaves a 1 kHz 3 V sine. Plain `pyvisa-py` is enough; no VISA library is needed.

## Not built

A native libsigrok driver is in `sigrok/` (see `sigrok/README.md`): builds, 34 checks pass against a fake device and a hardware run
(2026-10-31) passed; **PulseView: all settings and displays checked 2026-10-31**. Until then the Rigol emulation above is the verified route to live acquisition.

## UI actions (`ui`, command 0x55)

Changes a setting the way a
button press does: label, relay, capture restart. Values are absolute.

```
host/tc3_client.py ui vdiv 3        # V/div index 0..9
host/tc3_client.py ui tdiv 7        # T/div index 0..21
host/tc3_client.py ui probe 1       # 0 = X1, 1 = X10
host/tc3_client.py ui coupling 0    # 0 = AC, 1 = DC
host/tc3_client.py ui trigger -10   # trigger level offset -99..99
host/tc3_client.py ui edge 1        # trigger edge 0/1
host/tc3_client.py ui trigmode 0    # 0 Auto, 1 Normal, 2 Single
host/tc3_client.py ui position 0    # vertical position -99..99
host/tc3_client.py ui runstop 1     # 0 run, 1 stop
```

The request runs in the device's main loop. The device refuses outside the
scope view (error "not in the scope view").

## Virtual buttons and screen navigation (`key`, `goto`, command 0x55 field 0x20)

```
host/tc3_client.py goto scope        # menu, mtester, scope, generator, tools, settings
host/tc3_client.py key right         # back, select, left, right, up, down, plus, minus, hold-select
```

`key` posts one button press to the stock key mailbox, so the screen on display handles it exactly like the real button
(including its beep). `goto` backs out to the menu, moves the menu cursor, selects, and waits until the screen mode changes;
`goto settings` uses a long press of select, which opens Settings from any other screen. The device refuses a key while a
person's button event is still unconsumed (error "key mailbox busy"). Menu items: 0 M-Tester, 1 scope, 2 Generator,
3 Tools; Settings is the long press.

## Volts correction (`host/scope_cal.json`, `host/tc3_cal.py`)

The stock conversion reads about 3-4 % low and has a range-dependent offset (up to -36 mV and +61 mV). The host tools correct it
per V/div index and probe setting (sections `x10`, indices 0..6, and `x1`, indices 3..9): `true = (scope - b) / k` with `k` and `b` from `host/scope_cal.json`, applied where volts are
made (`tc3_client.frame_volts`, `roll_volts`, `snap`), so the SCPI server, the Rigol emulation, `tc3_sr.py` and the client all follow. The raw
ADC counts and the device's own screen are not touched. `TC3_CAL=0` in the environment turns it off. Not corrected: X10 indices 7..9 (not reachable by
a 3.3 V generator) and X1 indices 0..2 (windows too small for the generator line). The probe setting is the scope's selector: the physical probe switch must match it.

How it was measured (2026-10-31, one unit): generator DC against the scope at each range, with the generator's own line
`true = 0.992 * setting - 0.008 V` taken from two multimeter readings (3.0 V -> 2.968 V, 1.0 V -> 0.984 V). Result per index (k, b):
0 (0.940, +0.005), 1 (0.971, -0.000), 2 (0.961, -0.005), 3 (0.967, -0.029), 4 (0.969, -0.036), 5 (0.982, +0.053), 6 (0.978, +0.061).
Check after enabling it, at levels not used for the fit: X10 ranges 2 to 5 (14 points) worst error 12.9 mV, which is the noise of a 4-read
average (reads of one level vary by 15..30 mV at X10); X1 ranges 3 to 9 (19 points) worst error 11.5 mV at the coarsest range, most points within 2 mV.
**At X1 the stock conversion is already within 1 % in gain** (k 1.003..1.010): the X10 gain error (about 3 %) comes from this probe and the
scope input divider (about 10.3:1), only the per-range offsets are the scope's own. Accuracy limits: the multimeter's own accuracy is not known, the
generator line is two points, and the numbers belong to this unit and this probe.

`host/tc3_cal.py [--gen-gain G --gen-offset O] [--indices 0-9] [--write]` repeats the measurement for the scope's current probe selector (about 5 minutes; probe on the
generator output, scope view, nothing else on the serial port) and rewrites the file with `--write`. Offline test: `python3 host/tests/test_cal.py`.

## Screenshot at each step (`tc3_shots.py`)

```
host/tc3_shots.py --list                 # built-in scenarios: tour, scope-tdiv, generator-waves
host/tc3_shots.py tour -o /tmp/tour      # menu -> every screen -> menu, one PNG per step
host/tc3_shots.py my_scenario.txt --dry-run
```

`--quiet` sets the stock Volume to 0 before the run and restores it afterwards, so the virtual keys make no sound
(`tc3_client.py volume [0..100]` shows or sets it by hand: it goes through the Settings screen with about one key press per
percent point, so 40 to 0 takes roughly 10 s and leaves the device on the menu). The original value is read first and
restored in a `finally`, also after a failed step. Whether the stock code writes the volume to flash when Settings is left is not known.
Verified on hardware: Volume 40 -> 0 took about 10 s (beeps got quieter and stopped at 0), and `tour --quiet` ran 7 steps
silently and restored 40.

A scenario is a text file, one step per line: `goto <screen>`, `key <name>`, `ui <field> <value>`,
`gen [--wave ..] [--freq ..] [--duty ..] [--amp ..]`, `sweep <field> <from> <to> [step]` (a `ui` step per value),
`wait <s>`, `shot [label]`. Each step except `wait` is followed by a screenshot (after `--settle`, default 0.6 s) named
`NN_<step>.png`, and `index.txt` in the output directory lists the step, its result, the screen mode and the time. The run
stops at the first failing step, after taking its screenshot, unless `--keep-going` is given. Default output:
`shots-<scenario>-<time>/`. Each screenshot takes about a second and freezes the GUI meanwhile; each virtual key
beeps. Offline test: `python3 host/tests/test_shots.py` (parser, sweep expansion, run loop with a fake device).

## Screen capture (`screen`, command 0x56)

```
host/tc3_client.py screen -o screen.png     # 320x240 PNG, about 1 s, GUI frozen meanwhile
host/tc3_client.py screen --no-redraw       # only what the GUI flushes next
```

Hooks the GUI flush callback. The device
holds each 320x60 strip until the host has read it; if the host stops, the
device gives up after about 5 s and disarms. Python 3 only, no image library.

## Signal generator (command 0x58)

`tc3_client.py gen [--wave sine|square|pulse|triangle|ramp|dc] [--freq 1..100000] [--duty 0..100] [--amp 0.01..3.3]`
(no options: show). Output swings 0..amp volts. SCPI: `SOURce:FUNCtion SIN|SQU|PULS|TRI|RAMP|DC`, `SOURce:FREQuency`,
`SOURce:VOLTage[:AMPLitude]`, `SOURce:DCYCle` (or `SOURce:PULSe:DCYCle`), each with a `?` query. Queries return the
settings variables, which are only meaningful after the first set.

Hardware check over TCP (`host/tests/test_scpi_gen_hw.py` against `tc3_scpi.py`, 2026-10-30): FUNC/FREQ/VOLT/DCYC set and
query for all six waveforms, each measured through `WAVeform:DATA?`: sine 1 kHz 2.94 Vpp, sine 2 kHz 2005 Hz 1.94 Vpp,
triangle 500 Hz, square 3.30 Vpp, pulse 25 % reads 24 % high (mean 0.74 V), ramp 1004 Hz, DC 2.0 V reads 1.88 V mean.
Out-of-range gives -222, bad function -224, unknown header -113 in `SYST:ERR?`.

## Where things live

| Folder | Content |
|---|---|
| `host/` | Everyday tools (`tc3_client`, `tc3_scpi`, `tc3_sr`, `tc3_shots`, `tc3_cal`, `scope_cal.json`); `host/tests/` has their tests |
| `firmware_ext/` | Firmware extension; `tools/` builds, flashes and dumps (`build_custom_fw`, `flash_update`, `dump_flash`, `repack_firmware`, toolchain); `tests/` has the Unicorn tests |
| `sigrok/` | Native libsigrok driver, with its own tests |
