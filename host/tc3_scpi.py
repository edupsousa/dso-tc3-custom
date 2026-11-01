#!/usr/bin/env python3
"""SCPI-style TCP server for the DSO-TC3 custom firmware.

Bridges a raw SCPI socket (default port 5025, newline terminated) to the USB
CDC extension protocol in tc3_client.py, so PyVISA, InstrumentKit, netcat or a
script can read waveforms and a few settings.

Scope behavior:
  * Waveforms at T/div indices 0..14 (1 us .. 50 ms per division) come from
    the burst-frame ring (command 0x54). At 1, 2 and 5 us/div the acquisition
    is fixed: both ADCs interleaved, 10.29 MS/s (5.14 MS/s at 5 us), 600
    samples per frame (58 us / 117 us); only the on-screen zoom differs.
  * At T/div indices 15..21 (100 ms .. 10 s per division) the scope is in roll
    mode: WAVeform:DATA? returns the newest 300 samples of the continuous
    stream (command 0x57), i.e. what the screen shows, oldest first, fewer
    right after a T/div change. Sample rate is 25 per division (250 .. 2.5
    S/s). Voltages use the V/div in force when each sample was taken.
  * A frame is a separate 600-sample acquisition (about 15 per second), not a
    gap-free stream. STOP only freezes the device display; frames keep coming.
  * RUN/STOP, CHANnel1:SCALe and TIMebase:SCALe go through the deferred UI
    action (0x55): the main loop runs the stock button path, so labels, relay
    and capture restart follow. Needs the UI-hook firmware (otherwise V/div and
    RUN/STOP fall back to raw writes; TIMebase:SCALe then errors).

Rigol DS emulation (--rigol), for PulseView / sigrok-cli (libsigrok "rigol-ds"
driver, connection "tcp-raw/<host>/<port>"): the server then identifies itself
as a Rigol DS1052E (old DS1000 protocol, 600-point live frames, 8-bit data) and
answers the driver's configuration queries. See docs/host-tools.md for what is
real and what is approximated (timebase is reported as 50 / sample rate, about
twice the true T/div; channel 2 is a flat line).

Usage:
  tc3_scpi.py [--port-tcp 5025] [--host 127.0.0.1] [-p /dev/ttyACM0] [--rigol] [--trace]
  tc3_scpi.py --stdio          # read commands from stdin (testing)
  tc3_scpi.py --demo           # synthetic device, no hardware (testing)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tc3scpi import main

if __name__ == "__main__":
    main(__doc__)
