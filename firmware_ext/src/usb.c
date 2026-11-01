#include "ext.h"

uint8_t s_resp_buf[CDC_MAX_PACKET] __attribute__((aligned(4)));

/*
 * Self-contained USB CDC send, reimplemented from stock_cdc_send
 * (0x08005158) + its helpers FUN_08013d7c (PMA write) and FUN_0801446c
 * (COUNT_TX set). Runs entirely on MMIO peripheral registers, never
 * branching back into stock app flash -- stock_cdc_send crashed the
 * device when tail-called from the extension's entry context, even
 * though static analysis and Unicorn emulation found no fault in its
 * logic; this sidesteps the call instead of chasing the hardware cause.
 */
#define USB_EP1R        (*(volatile uint32_t *)0x40005c04)
#define USB_BTABLE_REG  (*(volatile uint32_t *)0x40005c50)
#define USB_PMA_BASE    0x40006000u
#define EP1_TX_PMA_OFF  0xc0          /* EP1 IN buffer inside the packet memory (stock layout) */

/* EP1R: toggle bits are written as 1 to flip. Keep the non-toggle bits (mask 0x8fbf also drops CTR_RX/CTR_TX and the
 * toggle bits), flip STAT_TX 0x30 to VALID, and write CTR_RX/CTR_TX = 1 (0x8080: "no change" for the clear-on-write flags). */
#define EP1R_KEEP       0x8fbfu
#define EP1R_STAT_TX    0x30u
#define EP1R_CTR_NOP    0x8080u

static void ext_pma_write(const uint16_t *src, int pma_offset, int len) {
    volatile uint16_t *dst = (volatile uint16_t *)(USB_PMA_BASE + pma_offset * 2);
    uint32_t n = ((uint32_t)len + 1U) >> 1;
    for (uint32_t i = 0; i < n; i++) {
        *dst = *src;
        dst += 2;
        src += 1;
    }
}

static void ext_set_count_tx(int ep, uint32_t count) {
    *(volatile uint32_t *)(((USB_BTABLE_REG & 0xffffu) + (uint32_t)ep * 8) * 2 + 0x40006004u) = count;
}

void ext_cdc_send(const uint8_t *buf, uint32_t len) {
    if (len < CDC_MAX_PACKET) {
        ext_pma_write((const uint16_t *)buf, EP1_TX_PMA_OFF, (int)len);
        ext_set_count_tx(1, len);
        USB_EP1R = (((USB_EP1R & EP1R_KEEP) ^ EP1R_STAT_TX) | EP1R_CTR_NOP);
    }
}
