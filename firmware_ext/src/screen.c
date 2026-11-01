/*
 * Screen capture. The GUI is LVGL-style: it renders 320x60 strips into one RAM
 * buffer (0x20001a2c) and calls the driver's flush_cb(drv, area, color_p)
 * (stock 0x0801984c), which writes the strip to the LCD. The main-loop hook
 * swaps flush_cb for ext_flush. While armed, ext_flush publishes the area and
 * pixel pointer and waits (bounded) for the host to read the strip with the
 * 0x42 memory read and send SCR_ACK, then runs the stock flush. If the host
 * stops answering, the wait times out and capture disarms itself.
 */
#include "ext.h"

#define DISP_DRV_FLUSH_CB   (*(volatile uint32_t *)0x2000b040u)   /* driver 0x2000b02c + 0x14 */
#define DEFAULT_DISP_PTR    (*(volatile uint32_t *)0x20000578u)
#define DISP_ACT_SCR        0x18                                  /* lv_disp_t.act_scr */
#define FLUSH_WAIT_LOOPS    100000000u                           /* several seconds at 144 MHz: a strip takes the host up to a second to read */

struct {
    volatile uint32_t armed, ready, ack, seq, px, want_inv, timeouts;
    int16_t area[4];
} s_cap;

void ext_flush(void *drv, const int16_t *area, const uint16_t *color_p) {
    ext_init();
    if (s_cap.armed) {
        for (int i = 0; i < 4; i++) s_cap.area[i] = area[i];
        s_cap.px = (uint32_t)color_p;
        s_cap.ack = 0;
        s_cap.seq++;
        mem_barrier();
        s_cap.ready = 1;
        uint32_t n = 0;
        while (!s_cap.ack && s_cap.armed && n < FLUSH_WAIT_LOOPS) n++;
        if (!s_cap.ack && s_cap.armed) {
            s_cap.timeouts++;
            s_cap.armed = 0;                 /* host gone: stop holding the GUI */
        }
        s_cap.ready = 0;
    }
    stock_flush_cb(drv, area, color_p);
}

void screen_hook_step(void) {
    if (DISP_DRV_FLUSH_CB == ((uint32_t)&stock_flush_cb | 1u) || DISP_DRV_FLUSH_CB == (uint32_t)&stock_flush_cb) {
        DISP_DRV_FLUSH_CB = (uint32_t)ext_flush | 1u;
    }
    if (s_cap.want_inv) {
        s_cap.want_inv = 0;
        uint32_t disp = DEFAULT_DISP_PTR;
        uint32_t scr = disp ? *(volatile uint32_t *)(disp + DISP_ACT_SCR) : 0;
        if (scr) stock_obj_invalidate(scr);
    }
}

/* 0x56 | op [| arg]. SCR_ARM starts holding strips; POLL describes the held strip; ACK releases it. */
bool handle_screen(const uint8_t *req, uint32_t len) {
    if (len < 2) return false;
    uint8_t op = req[1], arg = len >= 3 ? req[2] : 0;
    s_resp_buf[0] = RESP_SCREEN;
    s_resp_buf[1] = op;
    s_resp_buf[2] = 0;
    uint32_t reply_len = 3;
    switch (op) {
    case SCR_ARM:
        s_cap.ready = 0; s_cap.ack = 0;
        s_cap.armed = 1;
        if (arg) s_cap.want_inv = 1;
        break;
    case SCR_DISARM:
        s_cap.armed = 0; s_cap.ack = 1;
        break;
    case SCR_ACK:
        s_cap.ack = 1;
        break;
    case SCR_POLL:
        s_resp_buf[2] = (uint8_t)s_cap.ready;
        s_resp_buf[3] = (uint8_t)s_cap.armed;
        put_u32(&s_resp_buf[4], s_cap.seq);
        for (int i = 0; i < 4; i++) put_u16(&s_resp_buf[8 + 2 * i], (uint16_t)s_cap.area[i]);
        put_u32(&s_resp_buf[16], s_cap.px);
        s_resp_buf[20] = (uint8_t)s_cap.timeouts;
        reply_len = 21;
        break;
    default:
        s_resp_buf[0] = RESP_ERROR;
        s_resp_buf[2] = ERR_BAD_PARAM;
        break;
    }
    ext_reply(reply_len);
    return true;
}
