# firmware_ext - the extension that runs inside the DSO-TC3 firmware

Code placed at flash `0x0805E000` (RAM `0x20013000`, 16 KB) next to the stock firmware. The stock image reaches it through
a few patched instructions (`HOOKS` in `firmware_ext/tools/build_custom_fw.py`). It adds the USB commands that `host/tc3_client.py`,
`host/tc3_scpi.py` and the sigrok driver use. Wire format: `include/protocol.h`.

| File | Job | Entry points (patched into the stock image) |
|---|---|---|
| `src/main.c` | command dispatch, `0x5F` info, one-time RAM init | `extension_dispatch` (USB console call hook) |
| `src/usb.c` | send one CDC packet without calling stock code | |
| `src/params.c` | legacy `0x50` snapshot, `0x51`/`0x52` raw get/set | |
| `src/frames.c` | 8-slot ring of 600-sample bursts, `0x54` | `extension_dma_isr` (DMA1_CH1 vector), `extension_wait_hook` (T/div 0..2) |
| `src/roll.c` | roll-mode sample ring, `0x57` | `extension_adc_isr` (ADC1_2 vector) |
| `src/ui.c` | queued UI actions and virtual keys, `0x55` | `extension_ui_hook` (main-loop key consumer call) |
| `src/screen.c` | screen capture, `0x56` | `ext_flush` (installed by the UI hook) |
| `src/gen.c` | signal generator, `0x58` | `extension_gen_apply_hook`, `extension_gen_screen_enter` |
| `include/ext.h` | helpers shared by the modules | |
| `include/stock_symbols.h`, `extension.ld` | addresses of stock code and data (`g_ScopeState` field offsets) | |

Rules the code relies on:

* **No startup code.** Nothing zeroes RAM. Every entry point calls `ext_init()` first; the first call clears `.bss`
  (`ext_init_slow`). Do not add per-module "magic" words.
* **Soft-float C, no libc.** The assembly stubs in `gen.c` use the FPU (`s0`) and rely on C never touching it.
* **Interrupt context.** The USB handler and the ISRs must not call stock drawing code. Queue the work and run it from
  `extension_ui_hook` (see `ui.c`, `gen.c`).
* **RAM is nearly full** (`make` prints the size; about 700 bytes free). The linker stops on overflow.
* A reply is built in `s_resp_buf` and must be shorter than 64 bytes (`ext_cdc_send` drops longer ones).

Build and test (from the repository root):

    python3 firmware_ext/tools/build_custom_fw.py          # make + patch + pack + verify -> DSO-TC3_custom-ext.s19
    python3 firmware_ext/tests/test_ui_hook.py             # Unicorn: the real image against a stock SRAM snapshot (86 checks)
    python3 firmware_ext/tests/test_gen.py                 # Unicorn: generator hooks run the real stock routine
    bash sigrok/tests/run.sh                               # driver against a fake device
    ./run_tests.sh                                         # all offline tests (host, firmware, sigrok)

`make -C firmware_ext` alone builds `build/extension.{elf,bin,dis}`; header changes rebuild the right objects. The linker
prints "Forcing branch to absolute symbol in Thumb mode" warnings: the stock symbols in `extension.ld` are absolute
addresses with bit 0 set, which is what we want.
