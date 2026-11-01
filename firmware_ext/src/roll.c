/*
 * Roll mode (T/div 15..21). On this path the stock
 * firmware has no DMA burst: the ADC end-of-conversion interrupt (vector 34,
 * redirected here by firmware_ext/tools/build_custom_fw.py) shifts a 300-sample buffer one
 * place and appends one scaled sample. extension_adc_isr reads the raw ADC
 * value first (the stock handler reads the same register again), runs the
 * stock handler unchanged, and appends the raw sample to its own ring with
 * the V/div index in the top 4 bits, so the host gets full 12-bit samples,
 * the gain in force for each one, and an absolute sample counter to detect
 * gaps. The host reads by absolute index; nothing is consumed.
 */
#include "ext.h"

#define ROLL_RING       1024u               /* power of two: the modulo below is a mask */
#define ROLL_PER_CHUNK  29u
#define ROLL_HEADER     0xFF
#define ROLL_READ       0x01
#define ADC1_DAT        (*(volatile uint32_t *)0x4002084cu)

struct {
    volatile uint32_t count;   /* absolute index of the next sample to be written */
    volatile uint32_t base;    /* first index recorded under the current T/div */
    volatile uint32_t epoch;   /* bumps whenever T/div changes */
    uint32_t last_tdiv;
    uint16_t ring[ROLL_RING];  /* raw12 | vdiv << 12 */
} s_roll;

void extension_adc_isr(void) {
    ext_init();
    uint32_t raw = ADC1_DAT & 0xFFFu;
    stock_adc_isr();

    uint32_t tdiv = scope_get_tdiv();
    uint32_t n = s_roll.count;
    if (n == 0 && s_roll.epoch == 0) {       /* first sample since init */
        s_roll.last_tdiv = tdiv;
    } else if (tdiv != s_roll.last_tdiv) {
        s_roll.last_tdiv = tdiv;
        s_roll.base = n;
        s_roll.epoch++;
    }
    s_roll.ring[n % ROLL_RING] = (uint16_t)(raw | ((scope_get_vdiv() & 0xFu) << 12));
    mem_barrier();
    s_roll.count = n + 1;
}

/* 0x57 0xFF: header. 0x57 0x01 | from LE32: up to 29 samples starting at absolute index `from`. */
bool handle_roll(const uint8_t *req, uint32_t len) {
    uint8_t op = len >= 2 ? req[1] : ROLL_HEADER;
    uint32_t from = len >= 6 ? get_u32(&req[2]) : 0;
    uint32_t count = s_roll.count;
    if (count == 0) {
        ext_error(RESP_ROLL, ERR_EMPTY);
        return true;
    }
    s_resp_buf[0] = RESP_ROLL;
    s_resp_buf[1] = op;

    if (op == ROLL_HEADER) {
        s_resp_buf[2] = 0;
        put_u32(&s_resp_buf[3], count);
        put_u32(&s_resp_buf[7], s_roll.base);
        s_resp_buf[11] = (uint8_t)s_roll.epoch;
        s_resp_buf[12] = (uint8_t)scope_get_tdiv();
        s_resp_buf[13] = (uint8_t)scope_get_vdiv();
        s_resp_buf[14] = (uint8_t)scope_get_probe();
        s_resp_buf[15] = (uint8_t)scope_get_mode();
        put_u32(&s_resp_buf[16], (uint32_t)STOCK_GAIN);
        put_u32(&s_resp_buf[20], SS_U32(SS_SAMPLE_RATE));
        put_u16(&s_resp_buf[24], ROLL_RING);
        ext_reply(26);
        return true;
    }
    if (op != ROLL_READ) {
        ext_error(RESP_ROLL, ERR_BAD_CHUNK);
        return true;
    }

    if ((int32_t)(from - s_roll.base) < 0 || (int32_t)(count - from) > (int32_t)(ROLL_RING - 2u)) {
        ext_error(RESP_ROLL, ERR_NO_FRAME);      /* before the T/div epoch start, or already overwritten */
        return true;
    }
    uint32_t n = 0;
    if ((int32_t)(count - from) > 0) {
        n = count - from;
        if (n > ROLL_PER_CHUNK) n = ROLL_PER_CHUNK;
    }
    uint8_t *dest = &s_resp_buf[3];
    for (uint32_t i = 0; i < n; i++, dest += 2) {
        put_u16(dest, s_roll.ring[(from + i) % ROLL_RING]);
    }
    if (n && (s_roll.count - from) > ROLL_RING - 1u) {   /* writer lapped us while copying */
        ext_error(RESP_ROLL, ERR_NO_FRAME);
        return true;
    }
    s_resp_buf[2] = (uint8_t)n;
    ext_reply(3 + 2 * n);
    return true;
}
