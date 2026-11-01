/*
 * F3: burst-frame ring. The stock DMA1_CH1 transfer-complete ISR (0x08005980)
 * only clears the TC flag and sets g_ScopeState+0xa0 = 1; its vector (flash
 * 0x0800406C) is redirected here by firmware_ext/tools/build_custom_fw.py. This ISR does
 * the same two writes, then copies the finished 600-sample burst into a ring
 * slot together with the range settings in force, so the host gets
 * tear-free frames and can detect drops from the sequence number.
 *
 * T/div 0..2 (1, 2, 5 us): the stock path runs ADC1+ADC2 interleaved with a
 * polled DMA transfer and no interrupt, and the raw DMA buffer is re-armed (and
 * overwritten within microseconds) as soon as the stock wait/copy routine
 * 0x080074d0 has unpacked it into the trace at g_ScopeState+0x594. The trace
 * holds raw samples only until the next stock call (0x08006cb4) converts it in
 * place, so extension_wait_hook replaces the renderer's "bl 0x080074d0"
 * (0x08006AA0), runs the stock routine and ring-stores the trace if it changed.
 * Stock order: even trace slots are the ADC2 half of each DMA word, odd slots
 * the ADC1 half (measured: it is also time order).
 * A timed-out wait leaves the trace unchanged, so it stores nothing.
 */
#include "ext.h"

#define FRAME_SLOTS     8
#define FRAME_SAMPLES   600
#define FRAME_CHUNKS    20
#define FRAME_PER_CHUNK 30
#define FRAME_HEADER    0xFF
#define DMA1_IFCR       (*(volatile uint32_t *)0x40020004u)
#define DMA1_IFCR_CTCIF1 2u

struct frame_slot {
    uint32_t seq;           /* sequence held by this slot, 0 while being written */
    uint32_t sample_rate;   /* float bits of g_ScopeState+0xb0 (computed sample rate) */
    int32_t  gain;          /* STOCK_GAIN at capture time */
    uint8_t  vdiv, tdiv, probe, mode;
    union {
        uint16_t samples[FRAME_SAMPLES];
        uint32_t words[FRAME_SAMPLES / 2];   /* the same samples, for the word-wide copy */
    };
};

struct {
    uint32_t seq;           /* latest completed sequence, 0 = none yet */
    struct frame_slot slot[FRAME_SLOTS];
} s_ring;

/* Copy 600 raw samples, two per load, from a 4-byte aligned source. */
static inline void copy_samples(uint32_t *dst, const volatile uint32_t *src) {
    for (uint32_t i = 0; i < FRAME_SAMPLES / 2; i++) dst[i] = src[i];
}

/* Store 600 raw samples as the next ring frame, with the range settings in force. */
static void ring_store(const volatile uint32_t *src) {
    uint32_t seq = s_ring.seq + 1;
    struct frame_slot *sl = &s_ring.slot[seq % FRAME_SLOTS];
    sl->seq = 0;
    copy_samples(sl->words, src);
    sl->sample_rate = SS_U32(SS_SAMPLE_RATE);
    sl->gain  = STOCK_GAIN;
    sl->vdiv  = (uint8_t)scope_get_vdiv();
    sl->tdiv  = (uint8_t)scope_get_tdiv();
    sl->probe = (uint8_t)scope_get_probe();
    sl->mode  = (uint8_t)scope_get_mode();
    mem_barrier();
    sl->seq = seq;
    s_ring.seq = seq;
}

void extension_dma_isr(void) {
    ext_init();
    DMA1_IFCR = DMA1_IFCR_CTCIF1;                     /* clear CH1 transfer-complete */
    SS_U8(SS_BURST_DONE) = 1;                         /* stock burst handshake */
    ring_store((const volatile uint32_t *)g_AdcRawBuffer);
}

#define TRACE_WORDS     (FRAME_SAMPLES / 2)
static uint32_t trace_hash(void) {
    const volatile uint32_t *t = (const volatile uint32_t *)(g_ScopeState + SS_TRACE);
    uint32_t h = 2166136261u;
    for (uint32_t i = 0; i < TRACE_WORDS; i++) {
        h = (h ^ t[i]) * 16777619u;
    }
    return h;
}

void extension_wait_hook(void) {
    ext_init();
    if (scope_get_tdiv() >= 3) {
        stock_single_wait();                          /* DMA ISR path feeds the ring */
        return;
    }
    uint32_t before = trace_hash();
    stock_single_wait();
    if (trace_hash() != before) {
        uint32_t pm = irq_save();
        ring_store((const volatile uint32_t *)(g_ScopeState + SS_TRACE));
        irq_restore(pm);
    }
}

/* 0x54 | chunk | seq LE32. chunk 0xFF = header; seq 0 = latest frame. */
bool handle_frame(const uint8_t *req, uint32_t len) {
    uint8_t chunk = len >= 2 ? req[1] : FRAME_HEADER;
    uint32_t seq = len >= 6 ? get_u32(&req[2]) : 0;
    uint32_t latest = s_ring.seq;
    if (latest == 0) {
        ext_error(RESP_FRAME, ERR_EMPTY);
        return true;
    }
    if (seq == 0) {
        seq = latest;
    }
    const struct frame_slot *sl = &s_ring.slot[seq % FRAME_SLOTS];
    if (sl->seq != seq) {
        ext_error(RESP_FRAME, ERR_NO_FRAME);
        return true;
    }

    s_resp_buf[0] = RESP_FRAME;
    s_resp_buf[1] = chunk;
    uint32_t reply_len;

    if (chunk == FRAME_HEADER) {
        s_resp_buf[2] = 0;                  /* status */
        put_u32(&s_resp_buf[3], seq);
        put_u32(&s_resp_buf[7], latest);
        s_resp_buf[11] = sl->vdiv;
        s_resp_buf[12] = sl->tdiv;
        s_resp_buf[13] = sl->probe;
        s_resp_buf[14] = sl->mode;
        put_u32(&s_resp_buf[15], (uint32_t)sl->gain);
        put_u32(&s_resp_buf[19], sl->sample_rate);
        s_resp_buf[23] = FRAME_CHUNKS;
        reply_len = 24;
    } else if (chunk < FRAME_CHUNKS) {
        put_samples(&s_resp_buf[2], &sl->samples[chunk * FRAME_PER_CHUNK], FRAME_PER_CHUNK);
        reply_len = 2 + 2 * FRAME_PER_CHUNK;
    } else {
        ext_error(RESP_FRAME, ERR_BAD_CHUNK);
        return true;
    }
    if (sl->seq != seq) {                   /* the slot was overwritten while we copied from it */
        ext_error(RESP_FRAME, ERR_NO_FRAME);
        return true;
    }
    ext_reply(reply_len);
    return true;
}
