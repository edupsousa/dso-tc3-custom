# FNIRSI DSO-TC3 Custom Firmware Extension & Tool Suite

An open-source, non-destructive firmware extension, native [libsigrok](https://sigrok.org) driver, Python client library, and SCPI server for the **FNIRSI DSO-TC3** handheld oscilloscope and component tester.

---

## Highlights

- **Non-Destructive Firmware Extension**:
  Extends stock firmware `V0.3` without altering the factory bootloader, component-tester modes, or calibration pages. The extension resides entirely in previously unused flash space (`0x0805E000`–`0x0805F300`).
- **Continuous 10 kS/s Streaming & Deep Capture**:
  Stream live oscilloscope data continuously over USB CDC at up to 10 kS/s, or retrieve high-speed captured waveform buffers (up to 10 MS/s equivalent sampling).
- **Native libsigrok & PulseView Integration**:
  Full native driver (`fnirsi-dso-tc3`) for `libsigrok` and `PulseView`, enabling real-time waveform visualization, protocol decoding, and automated measurements.
- **SCPI & PyVISA Lab Automation**:
  Run a standard SCPI instrument server (`tc3-scpi`) over TCP port `5025`, compatible with Rigol/Keysight VISA drivers, LabVIEW, and Python `pyvisa`.
- **Arbitrary DDS Generator Remote Control**:
  Configure the built-in DDS function generator (sine, square, triangle, ramp, DC) with remote frequency, duty-cycle, and amplitude control.
- **Zero Proprietary Blobs**:
  The repository contains only original open-source code and build scripts. The official stock firmware is downloaded directly from FNIRSI's CDN at build time and verified with SHA-256.

---

## Repository Structure

```
dso-tc3-custom/
├── common/             # Shared protocol definitions and test utilities
├── docs/               # Technical notes on firmware encryption, memory layout, and host tools
│   ├── firmware-decryption.md
│   └── host-tools.md
├── firmware_ext/       # ARM Cortex-M4 C firmware extension and packaging tools
│   ├── include/        # Extension headers (protocol, MMIO, stock symbol hooks)
│   ├── src/            # Extension C sources (dispatch, streaming, generator hooks)
│   ├── tools/          # Build scripts, packager, flash updater, and SWD configs
│   ├── tests/          # Offline Unicorn emulator test suite
│   ├── Makefile        # Extension build rules
│   └── extension.ld    # GNU LD linker script
├── host/               # Python client library (`tc3`), SCPI server (`tc3scpi`), and CLIs
│   ├── tc3/            # Core library (serial protocol, streaming, voltage calibration)
│   ├── tc3scpi/        # SCPI protocol emulation engine
│   └── tests/          # Host unit and protocol regression tests
├── sigrok/             # Native libsigrok C driver and PulseView patches
│   ├── fnirsi-dso-tc3/ # Native libsigrok hardware driver source
│   ├── default.nix     # Reproducible Nix derivation for libsigrok & PulseView
│   └── patch-libsigrok.sh # Patch and build script for upstream libsigrok
├── udev/               # Linux udev rules for unprivileged USB access
├── flake.nix           # Nix flake providing PulseView, sigrok-cli, and dev environment
├── pyproject.toml      # PEP 621 Python package configuration
└── run_tests.sh        # Top-level test runner
```

---

## Getting Started

### 1. Linux Permissions (`udev`)

Copy the udev rules to allow non-root access to the USB CDC serial interface:

```bash
sudo cp udev/60-fnirsi-dso-tc3.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

### 2. Install Host Tools

Install the Python package and CLI tools:

```bash
pip install .
```

To include optional PyVISA / SCPI server dependencies:

```bash
pip install ".[visa]"
```

Or for development (editable mode with test tools):

```bash
pip install -e ".[test,firmware]"
```

---

## Building and Flashing Custom Firmware

Building the custom firmware extension requires `arm-none-eabi-gcc` and `make`.

### Step 1: Fetch Stock Firmware

Download and verify the official FNIRSI `V0.3` firmware:

```bash
make -C firmware_ext fetch-stock
```

*(Alternatively: `python3 firmware_ext/tools/fetch_stock_fw.py`)*

### Step 2: Build the Patched Image

Compile the extension and assemble `DSO-TC3_custom-ext.s19`:

```bash
make -C firmware_ext custom-fw
```

The build process:
1. Compiles the C extension with `arm-none-eabi-gcc`.
2. Decrypts `DSO-TC3_V0.3.s19` and inserts dispatch trampolines into unused code gaps.
3. Appends the extension payload at `0x0805E000`.
4. Repacks and re-encrypts the resulting image into `DSO-TC3_custom-ext.s19`.

### Step 3: Flash to Device

1. Turn off the DSO-TC3.
2. Hold the **DOWN (▼)** key and press the **POWER** button.
3. The device enters bootloader update mode (displaying the USB update icon).
4. Connect via USB-C and run:

```bash
python3 firmware_ext/tools/flash_update.py --wait DSO-TC3_custom-ext.s19
```

Once flashing completes, the device automatically reboots into application mode with the extension active.

---

## Host CLI Tools

Once installed, the following commands are available:

### `tc3` / `tc3-client`
Main management and diagnostic tool:

```bash
# Check device communication and extension version
tc3 ping

# Print active scope settings (V/div, T/div, trigger level, coupling)
tc3 status

# Capture a single waveform and save to CSV
tc3 snap -o waveform.csv

# Download a live screenshot (PPM / PNG)
tc3 screen screenshot.png

# Configure the DDS signal generator
tc3 gen --wave sine --freq 1000 --amp 2.5
```

### `tc3-sr`
High-speed continuous roll streaming:

```bash
# Stream 10 seconds of data at 10 kS/s to a CSV file
tc3-sr -r 10000 -d 10 -o stream.csv
```

### `tc3-scpi`
Run an SCPI-compatible server over TCP:

```bash
tc3-scpi --port 5025
```

Connect via PyVISA:
```python
import pyvisa
rm = pyvisa.ResourceManager("@py")
tc3 = rm.open_resource("TCPIP::127.0.0.1::5025::SOCKET", read_termination="\n")
print(tc3.query("*IDN?"))
# FNIRSI,DSO-TC3,0,V0.3-ext1.0
```

---

## PulseView & libsigrok

### Using Nix Flake (Recommended)

If you use Nix with flakes enabled, you can run PulseView with the custom driver immediately:

```bash
nix run .#pulseview
```

Or run `sigrok-cli`:

```bash
nix run .#sigrok-cli -- -d fnirsi-dso-tc3:conn=/dev/ttyACM0 --samples 100
```

### Building Manually

Use the included build script to apply the driver patch to upstream `libsigrok`:

```bash
cd sigrok
./patch-libsigrok.sh
```

---

## Testing

Run the full automated test suite (protocol checks, client unit tests, SCPI verification, calibration tests, and offline Unicorn emulator tests):

```bash
./run_tests.sh
```

---

## Safety and Non-Destructive Design

- The device bootloader at `0x08000000`–`0x08004000` is untouched and remains write-protected.
- Factory calibration parameters at `0x0807F000` are completely preserved.
- You can revert to stock firmware at any time by entering update mode and flashing the official `DSO-TC3_V0.3.s19`.

---

## License & Attribution

- **License**: Released under the [GNU General Public License v3 (GPLv3)](LICENSE).
- **Trademarks**: "FNIRSI" and "DSO-TC3" are trademarks of Shenzhen FNIRSI Technology Co., Ltd. This project is an independent open-source effort and is not affiliated with, sponsored, or endorsed by FNIRSI.
- **Stock Firmware**: Stock firmware binaries are copyright FNIRSI and are not hosted in this repository. Users download them directly from official vendor distribution servers using the provided scripts.
