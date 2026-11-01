/*
 * M-Tester calibration write. The config sector mirrors g_ScopeState: +0x54..+0x68 hold the six
 * per-pin-pair stray capacitances and +0x78 the 5 V scale. 0x59 | flags | 7 x float32 LE (the six slots in order, then
 * the scale) queues a write; extension_ui_hook runs it in the main loop, where it stores the words in RAM and, when
 * flags bit 0 is set, calls the stock config save (0x08019468: erase the sector, program the first 0x90 bytes from
 * RAM). Without the flag only RAM changes and a power cycle brings the flash values back.
 * 0x59 0xFF polls: 0x59 0xFF state result.
 */
#include "ext.h"

#define CFG_SLOTS 7
#define CFG_SAVE  1u

static const uint8_t s_cfg_off[CFG_SLOTS] = { 0x54, 0x58, 0x5c, 0x60, 0x64, 0x68, 0x78 };

struct {
    volatile uint32_t state;
    volatile uint32_t result;
    uint32_t flags;
    uint32_t val[CFG_SLOTS];
} s_cfg;

void cfg_hook_step(void) {
    if (s_cfg.state != UI_PENDING) return;
    uint32_t pm = irq_save();
    for (uint32_t i = 0; i < CFG_SLOTS; i++) SS_U32(s_cfg_off[i]) = s_cfg.val[i];
    irq_restore(pm);
    if (s_cfg.flags & CFG_SAVE) stock_cfg_save();
    s_cfg.result = UI_OK;
    s_cfg.state = UI_DONE;
}

bool handle_cfg(const uint8_t *req, uint32_t len) {
    if (len < 2) return false;
    s_resp_buf[0] = RESP_CFG;
    s_resp_buf[1] = req[1];
    if (req[1] == UI_POLL) {
        s_resp_buf[2] = (uint8_t)s_cfg.state;
        s_resp_buf[3] = (uint8_t)s_cfg.result;
        ext_reply(4);
        if (s_cfg.state == UI_DONE) s_cfg.state = UI_IDLE;
        return true;
    }
    if (len < 2 + 4 * CFG_SLOTS) return false;
    if (s_cfg.state == UI_PENDING) {
        s_resp_buf[2] = UI_ERR_BUSY;
    } else {
        s_cfg.flags = req[1];
        for (uint32_t i = 0; i < CFG_SLOTS; i++) s_cfg.val[i] = get_u32(&req[2 + 4 * i]);
        mem_barrier();
        s_cfg.state = UI_PENDING;
        s_resp_buf[2] = UI_OK;
    }
    ext_reply(3);
    return true;
}
