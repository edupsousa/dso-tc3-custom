#ifndef EXT_H
#define EXT_H

#include <stdint.h>
#include <stdbool.h>
#include "protocol.h"
#include "stock_symbols.h"

/*
 * Shared helpers for the extension modules. Everything runs on the stock firmware's stack and RAM rules:
 * no libc, no startup code (the linker script has no crt0), soft-float C.
 */

/* ---- one-time state init ----
 * Nothing zeroes RAM_EXT for us, and a warm reset keeps its old contents. The first entry into the extension, from
 * any entry point (USB dispatch, hooks, ISRs), clears the whole .bss and sets g_ext_magic. Every entry point calls
 * ext_init() before touching module state. */
#define EXT_MAGIC 0x45585431u
extern uint32_t g_ext_magic;
void ext_init_slow(void);
static inline void ext_init(void) {
    if (__builtin_expect(g_ext_magic != EXT_MAGIC, 0)) ext_init_slow();
}

/* ---- interrupt masking and ordering ---- */
static inline uint32_t irq_save(void) {
    uint32_t pm;
    __asm__ volatile("mrs %0, primask\n\tcpsid i" : "=r"(pm) :: "memory");
    return pm;
}
static inline void irq_restore(uint32_t pm) {
    __asm__ volatile("msr primask, %0" :: "r"(pm) : "memory");
}
static inline void mem_barrier(void) { __asm__ volatile("dmb" ::: "memory"); }

/* ---- little-endian wire helpers ---- */
static inline uint32_t get_u32(const uint8_t *s) {
    return (uint32_t)s[0] | ((uint32_t)s[1] << 8) | ((uint32_t)s[2] << 16) | ((uint32_t)s[3] << 24);
}
static inline void put_u32(uint8_t *d, uint32_t v) {
    d[0] = (uint8_t)v; d[1] = (uint8_t)(v >> 8); d[2] = (uint8_t)(v >> 16); d[3] = (uint8_t)(v >> 24);
}
static inline void put_u16(uint8_t *d, uint16_t v) {
    d[0] = (uint8_t)v; d[1] = (uint8_t)(v >> 8);
}
/* n little-endian halfwords; the Cortex-M4 is little-endian, so this is a plain copy. dst may be 2-byte aligned. */
static inline void put_samples(uint8_t *dst, const uint16_t *src, uint32_t n) {
    uint16_t *d = (uint16_t *)dst;
    for (uint32_t i = 0; i < n; i++) d[i] = src[i];
}

/* ---- replies ---- */
extern uint8_t s_resp_buf[CDC_MAX_PACKET];            /* 4-byte aligned; every reply is built here */
void ext_cdc_send(const uint8_t *buf, uint32_t len);  /* one packet, len < 64; longer is dropped */
static inline void ext_reply(uint32_t len) { ext_cdc_send(s_resp_buf, len); }
/* RESP_ERROR | tag | code */
static inline void ext_error(uint8_t tag, uint8_t code) {
    s_resp_buf[0] = RESP_ERROR;
    s_resp_buf[1] = tag;
    s_resp_buf[2] = code;
    ext_reply(3);
}

/* ---- command handlers: return false when the request is not ours and must go to the stock console ---- */
bool handle_params(uint8_t cmd, const uint8_t *req, uint32_t len);   /* 0x50 snap, 0x51 get, 0x52 set */
bool handle_frame(const uint8_t *req, uint32_t len);                 /* 0x54 */
bool handle_roll(const uint8_t *req, uint32_t len);                  /* 0x57 */
bool handle_ui(const uint8_t *req, uint32_t len);                    /* 0x55 */
bool handle_screen(const uint8_t *req, uint32_t len);                /* 0x56 */
bool handle_gen(const uint8_t *req, uint32_t len);                   /* 0x58 */

/* ---- main-loop steps run by extension_ui_hook, in this order ---- */
void screen_hook_step(void);
void gen_hook_step(void);
void ui_hook_step(void);

/* ---- generator module entry points used by assembly stubs ---- */
void ext_gen_save(void);

#endif /* EXT_H */
