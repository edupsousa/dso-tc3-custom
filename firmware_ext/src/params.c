/*
 * Legacy raw access to g_ScopeState fields (0x50 snapshot, 0x51 get, 0x52 set). Kept for tc3_client.py and the
 * sigrok driver's state read. SET writes the field and nothing else (no redraw, no hardware reprogramming), except
 * V/div, which re-applies the front-end gain. For changes the stock UI follows, use the 0x55 requests (ui.c).
 */
#include "ext.h"

#define SNAP_HEADER     0xFF
#define SNAP_CHUNKS     20
#define SNAP_PER_CHUNK  30

static const struct param {
    uint8_t id, off, bytes, max;     /* max: highest accepted value on SET (0xFF = any) */
} s_params[] = {
    { PARAM_MODE,          SS_MODE,     4, 6 },
    { PARAM_VDIV,          SS_VDIV,     4, 9 },
    { PARAM_PROBE,         SS_PROBE,    4, 1 },
    { PARAM_TDIV,          SS_TDIV,     4, 21 },
    { PARAM_COUPLING,      SS_COUPLING, 4, 1 },
    { PARAM_TRIGGER_LEVEL, SS_TRIGGER,  4, 0xFF },   /* signed offset: not range-checked */
    { PARAM_RUN_STOP,      SS_RUNSTOP,  1, 1 },
};

static const struct param *find_param(uint8_t id) {
    for (uint32_t i = 0; i < sizeof(s_params) / sizeof(s_params[0]); i++) {
        if (s_params[i].id == id) return &s_params[i];
    }
    return 0;
}

static void param_error(uint8_t id, uint8_t code) {
    s_resp_buf[0] = RESP_ERROR;
    s_resp_buf[1] = id;
    s_resp_buf[2] = code;
    ext_reply(3);
}

static void get_param(uint8_t id) {
    const struct param *p = find_param(id);
    if (!p) { param_error(id, ERR_BAD_PARAM); return; }
    s_resp_buf[0] = RESP_GET_PARAM;
    s_resp_buf[1] = id;
    put_u32(&s_resp_buf[2], p->bytes == 1 ? SS_U8(p->off) : SS_U32(p->off));
    ext_reply(6);
}

static void set_param(uint8_t id, uint32_t val) {
    const struct param *p = find_param(id);
    if (!p) { param_error(id, ERR_BAD_PARAM); return; }
    /* An out-of-range T/div or V/div index is used by the stock code as a table index: refuse it here. */
    if (p->max != 0xFF && val > p->max) { param_error(id, ERR_RANGE); return; }
    if (p->bytes == 1) SS_U8(p->off) = (uint8_t)val; else SS_U32(p->off) = val;
    if (id == PARAM_VDIV) stock_apply_vdiv_gain();
    s_resp_buf[0] = RESP_SET_PARAM;
    s_resp_buf[1] = id;
    s_resp_buf[2] = 0;
    ext_reply(3);
}

/* 0x50 | chunk: the raw DMA buffer as last left by the burst, 20 chunks of 30 samples; 0xFF = header. */
static void snap(uint8_t chunk) {
    s_resp_buf[0] = RESP_SNAP;
    s_resp_buf[1] = chunk;
    if (chunk == SNAP_HEADER) {
        s_resp_buf[2] = scope_get_run_stop();
        s_resp_buf[3] = (uint8_t)scope_get_vdiv();
        s_resp_buf[4] = (uint8_t)scope_get_tdiv();
        s_resp_buf[5] = (uint8_t)scope_get_probe();
        s_resp_buf[6] = (uint8_t)scope_get_coupling();
        put_u16(&s_resp_buf[7], (uint16_t)SS_U32(SS_TRIGGER));
        put_u16(&s_resp_buf[9], SNAP_CHUNKS * SNAP_PER_CHUNK);
        s_resp_buf[11] = SNAP_CHUNKS;
        ext_reply(12);
    } else if (chunk < SNAP_CHUNKS) {
        put_samples(&s_resp_buf[2], (const uint16_t *)&g_AdcRawBuffer[chunk * SNAP_PER_CHUNK], SNAP_PER_CHUNK);
        ext_reply(2 + 2 * SNAP_PER_CHUNK);
    } else {
        param_error(chunk, ERR_BAD_CHUNK);   /* the historical error frame carries the chunk, not the tag */
    }
}

bool handle_params(uint8_t cmd, const uint8_t *req, uint32_t len) {
    switch (cmd) {
    case CMD_GET_PARAM:
        if (len < 2) return false;
        get_param(req[1]);
        return true;
    case CMD_SET_PARAM:
        if (len < 6) return false;
        set_param(req[1], get_u32(&req[2]));
        return true;
    default:   /* CMD_SNAP; no chunk byte = header */
        snap(len >= 2 ? req[1] : SNAP_HEADER);
        return true;
    }
}
