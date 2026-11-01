/*
 * Extension entry point and command dispatch. The USB console call (hook in firmware_ext/tools/build_custom_fw.py) jumps to
 * extension_dispatch for every console command the stock USB handler receives. Commands we do not own are passed
 * back to the stock console (0x08013f08) unchanged.
 *
 * Modules (one file per feature, each documented at its top):
 *   usb.c     packet send               params.c  legacy 0x50..0x52 (snapshot, raw get/set)
 *   frames.c  burst-frame ring (0x54)   roll.c    roll-mode stream (0x57)
 *   ui.c      deferred UI actions, virtual keys (0x55)
 *   screen.c  screen capture (0x56)     gen.c     signal generator (0x58)
 */
#include "ext.h"

uint32_t g_ext_magic;

extern uint32_t _sbss[], _ebss[];

void ext_init_slow(void) {
    uint32_t pm = irq_save();
    if (g_ext_magic != EXT_MAGIC) {            /* an ISR may have finished the init meanwhile */
        for (uint32_t *p = _sbss; p < _ebss; p++) *(volatile uint32_t *)p = 0;
        g_ext_magic = EXT_MAGIC;
    }
    irq_restore(pm);
}

static void handle_ext_info(void) {
    s_resp_buf[0] = RESP_EXT_INFO;
    s_resp_buf[1] = EXTENSION_VERSION_MAJOR;
    s_resp_buf[2] = EXTENSION_VERSION_MINOR;
    s_resp_buf[3] = EXTENSION_VERSION_PATCH;
    uint32_t len = 0;
    while (EXTENSION_BANNER[len]) {
        s_resp_buf[4 + len] = (uint8_t)EXTENSION_BANNER[len];
        len++;
    }
    ext_reply(4 + len);
}

/* 0x42 | addr LE32 | n LE16 -> 0x43 | n bytes (1..60). Needs 7 request bytes; anything shorter goes to the stock console. */
#define READ_MEM_MAX 60
static bool handle_read_mem(const uint8_t *req, uint32_t len) {
    if (len < 7) return false;
    uint32_t addr = get_u32(&req[1]);
    uint32_t n = req[5] | ((uint32_t)req[6] << 8);
    if (n == 0 || n > READ_MEM_MAX) n = n ? READ_MEM_MAX : 1;
    s_resp_buf[0] = RESP_READ_MEM;
    for (uint32_t i = 0; i < n; i++) s_resp_buf[1 + i] = ((const volatile uint8_t *)addr)[i];
    ext_reply(1 + n);
    return true;
}

/* Extension entry point placed at 0x0805E000 */
__attribute__((section(".text.entry"), used))
void extension_dispatch(uint8_t *req, uint32_t len) {
    ext_init();
    if (len == 0 || req == 0) {
        stock_console_dispatch(req, len);
        return;
    }

    bool handled = true;
    switch (req[0]) {
    case CMD_READ_MEM:  handled = handle_read_mem(req, len); break;
    case CMD_EXT_INFO:  handle_ext_info(); break;
    case CMD_SNAP:
    case CMD_GET_PARAM:
    case CMD_SET_PARAM: handled = handle_params(req[0], req, len); break;
    case CMD_FRAME:     handled = handle_frame(req, len); break;
    case CMD_ROLL:      handled = handle_roll(req, len); break;
    case CMD_UI:        handled = handle_ui(req, len); break;
    case CMD_SCREEN:    handled = handle_screen(req, len); break;
    case CMD_GEN:       handled = handle_gen(req, len); break;
    case CMD_CFG:       handled = handle_cfg(req, len); break;
    default:            handled = false; break;   /* stock commands (0x00, 0x10, ...) */
    }
    if (!handled) {
        stock_console_dispatch(req, len);
    }
}
