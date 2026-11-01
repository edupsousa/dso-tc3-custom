# libsigrok driver `fnirsi-dso-tc3`

A native libsigrok driver for the DSO-TC3 with the extension firmware. It talks the USB CDC protocol of
`host/tc3_client.py` directly, so `sigrok-cli` and PulseView need no bridge process and no Rigol emulation.

**Status (2026-10-31):** builds and passes 39 driver checks against a fake device plus 24 checks that its wire constants equal the firmware's (`sigrok/tests/run.sh`). Run on the real
device with `sigrok-cli`: scan, burst, roll (100 ms to 5 s/div), setting changes and the generator all work. PulseView: the device is found at startup and preselected
(do not scan again, the port is then busy); the burst display was checked at 200 us/div (vertical edges, 500 us high for a 1 kHz square).
Roll was checked at 100 ms/div with a 2 Hz square (250 ms high, scrolls gap-free).
A 1 kHz signal at 100 ms/div only shows aliasing (250 S/s), as on the device screen.

## What it fixes compared with the Rigol emulation (`tc3_scpi.py --rigol`)

| Rigol emulation | This driver |
|---|---|
| PulseView shows timebases at 2x the scope's labels | The timebase list is the device's own 22 T/div values (1 us .. 10 s) |
| 8-bit samples (full scale = +-5 divisions of the V/div read last) | float32 volts with the stock 12-bit resolution and the `scope_cal.json` correction |
| "500 ms" and "5 s" are 24 % off on the time axis | All 22 timebases are exact; T/div 16 is 125 Hz (0.17 % off, see below) |
| Phantom channel 2 | One channel, `CH1` |
| Roll timebases: frames of 600 samples, padded | Roll T/div 15..21 is one gap-free stream, starting with the history on the device's screen |
| Needs `tc3_scpi.py --rigol` running; the device poses as a Rigol | Opens the serial port itself, finds it by USB id |
| `Read should have been completed` log line | None |
| No generator | Generator in channel group `GEN`: `sigrok-cli`, and PulseView through `pulseview-controls.patch` (adds frequency, amplitude and duty boxes; stock PulseView shows only the wave type) |

## Use

Build (Nix):

```
nix-build sigrok -A sigrok-cli      # sigrok-cli with the driver
nix-build sigrok -A pulseview       # PulseView against the patched libsigrok (Qt build, slow)
```

```
result/bin/sigrok-cli -d fnirsi-dso-tc3 --scan                          # finds /dev/ttyACM* by VID:PID 19f5:5740
result/bin/sigrok-cli -d fnirsi-dso-tc3:conn=/dev/ttyACM0 --frames 3 -o out.sr
result/bin/sigrok-cli -d fnirsi-dso-tc3:conn=/dev/ttyACM0 --samples 600 -O csv    # at a roll timebase (T/div >= 100 ms)
result/bin/sigrok-cli -d fnirsi-dso-tc3:conn=/dev/ttyACM0 -g CH1 -c "vdiv=1 V:coupling=DC:probe_factor=10" --frames 1
result/bin/sigrok-cli -d fnirsi-dso-tc3:conn=/dev/ttyACM0 -c timebase="500 ms" --samples 300 -O srzip -o slow.sr
result/bin/sigrok-cli -d fnirsi-dso-tc3:conn=/dev/ttyACM0 -g GEN -c "pattern=sine:output_frequency=1000:amplitude=3"
```

Only one program may hold the serial port: stop `tc3_scpi.py` first. The device must be in the scope view
(`host/tc3_client.py goto scope`); an acquisition is refused otherwise, and settings are refused outside the scope view too.

## Behaviour

* **Burst timebases (T/div 1 us .. 50 ms, indices 0..14):** every new frame the device captured (about 15 per second) is one
  libsigrok frame of 600 samples. No trigger alignment: that is firmware work.
* **Roll timebases (100 ms .. 10 s, indices 15..21):** a continuous sample stream, no frames. The first packet is the history already
  recorded (up to 1020 samples), then every new sample. The samplerate is whole Hz, so 12.5 and 2.5 S/s (T/div 19 and 21) are sent
  with every sample doubled (25 and 5 Hz) to keep the time axis exact; 125.217 S/s (T/div 16) is sent as 125 Hz, 0.17 % off.
  A sample limit counts device samples, not doubled ones.
* **Interleaved timebases (1, 2, 5 us):** the rate is the measured 144 MHz / 14 (10.2857 MS/s; half at 5 us), not the firmware's
  literal 10280 kS/s.
* **V/div** is the value shown on the scope: the input range times the probe setting. The list follows the probe selector, so
  after changing `probe_factor` read the list again. The physical probe switch must match the selector.
* **Volts correction** per V/div index and probe (the generated `fnirsi-dso-tc3/cal.h`, from `host/scope_cal.json`):
  run `sigrok/gen_cal.py` after re-running `host/tc3_cal.py --write`. The numbers belong to one unit and one probe.
* **Channel group `CH1`:** `vdiv`, `coupling` (AC/DC), `probe_factor` (1/10). **Device:** `timebase`, `triggerslope` (r/f),
  `triggersource` (CH1 only), `samplerate` (read only), `limit_samples`, `limit_frames`, `limit_time`.
  Trigger level, position and mode are not exposed yet. Run/stop is the device's own button.
* **Channel group `GEN`** (no channel, so PulseView lists no trace for it, only its options): `pattern` (sine, square, pulse, triangle, ramp, dc),
  `output_frequency` 1..100000 Hz, `amplitude` 0.01..3.3 V (the output swings 0..amplitude, so this is peak-to-peak),
  `output_duty_cycle` 0..100 %. Out-of-range values are refused. There is no on/off switch.
* Settings changes go through the same deferred UI actions as `tc3_client.py ui` (a button press in the device's main loop).
* Reading settings (`--show`, PulseView's options) asks the device, so button presses on the device show up.

## Cost and failure behaviour

Measured on the device (2026-10-31): a round trip is about 0.1 ms, and a burst frame (header + 20 chunks) is read in about 4 ms.
The scope makes 8-15 burst frames per second (about 14 at the fast T/div, 8.5 at 5 ms/div), so the driver waits for the device, not the other way round;
a faster frame readout in the protocol would not help.

* **One transfer for the scope state.** `tc3_read_state()` reads the 36 bytes of `g_ScopeState` from `+0x20` to `+0x43` with one `0x42` read
  (before: nine requests), and commits them together. The acquisition poll uses it too, so the cached settings are always current.
  Settings that depend on the state (V/div list at the probe setting, trigger level) refresh it first.
* **Scan and `--show` take 0.04 s** (were 0.5 s: the banner reply has no length, and the old read waited for a full 32 bytes).
* A frame is 21 requests. The old extra header request after the chunks is gone: the device checks every chunk against its ring slot after copying it
  (`ERR_NO_FRAME`), so an overwritten frame is dropped anyway.
* **A lost connection** (cable pulled, device powered off: the port reports an I/O error) stops the acquisition at once with
  `Lost the connection to the device.` Other failures (timeouts, bad replies) stop it after 10 in a row.
* The generator and UI requests share one routine (`request_action`): acknowledge, then poll until the device reports done, and the result byte is checked.
  The poll interval is 5 ms (was 20 ms).
* `tests/check_protocol.py` compares the driver's wire constants with `firmware_ext/include/protocol.h` (the driver is built from `sigrok/` alone, so it keeps its own copy).

## Files

| File | Purpose |
|---|---|
| `fnirsi-dso-tc3/api.c`, `protocol.c`, `protocol.h` | The driver |
| `fnirsi-dso-tc3/cal.h` | Generated by `gen_cal.py` from `host/scope_cal.json` |
| `patch-libsigrok.sh` | Adds the driver to a libsigrok source tree (`configure.ac`, `Makefile.am`) |
| `default.nix` | `libsigrok`, `sigrok-cli`, `pulseview` with the driver (and the PulseView patch) |
| `pulseview-controls.patch` | PulseView: boxes for the keys it does not bind: generator output frequency, amplitude, duty cycle; trigger level (box) and trigger mode (drop-down) |
| `fake_tc3.py` | A fake device on a pty that speaks the extension protocol |
| `tests/test_driver.py`, `tests/run.sh` | The real `sigrok-cli` against the fake device (also timing, request counts and a pulled cable) |
| `tests/check_protocol.py` | Driver wire constants equal the firmware's; `run.sh` runs it first |
| `tests/ptyshim.c`, `tests/with_fake_tty.sh` | Test plumbing: libserialport accepts only kernel ttys with a sysfs entry and modem-control ioctls; the shell script fakes both in a throwaway user namespace (no root) |

## Known limits

* PulseView: checked by the user on 2026-10-31 and working: burst and roll display, timebase, sample rate, V/div, coupling, probe factor, trigger level/mode, generator boxes (log: no errors). PulseView opens the device at startup; do not scan again (the port is then busy) and close it before using `tc3_client.py` or `sigrok-cli`. At slow burst timebases (5 ms/div = 5 kHz) a 1 kHz square has 5 samples per period, so its edges are drawn diagonal.
* One scope channel; no run/stop control; no memory depth. No `capture_ratio` / horizontal trigger position: the device has none, and its burst frames are not trigger-aligned.
* `triggerlevel` (volts) is the stock trigger offset in screen units: (vertical position - offset) x V/div / 23, with the measured volts correction. One unit is V/div / 23 (0.09 V at 2 V/div), the offset range is -99..99, so a level outside the screen is clamped. The reading follows the V/div setting.
* `triggermode` (auto, normal, single) is a key that `patch-libsigrok.sh` adds to libsigrok (`SR_CONF_TRIGGER_MODE`, appended at the end of the key enum); stock PulseView does not bind it, `pulseview-controls.patch` does.
* With a running acquisition the settings calls and the data transfers share one lock, so changing options while capturing briefly delays the stream.
* libsigrok has no out-of-tree driver plugins: this is a patch of libsigrok, so a nixpkgs libsigrok update must keep the two
  anchor lines in `patch-libsigrok.sh` (`SR_DRIVER([Fluke 45]` in `configure.ac`, `if HW_FLUKE_45` in `Makefile.am`) valid.
