#ifndef STOCK_SYMBOLS_H
#define STOCK_SYMBOLS_H

#include <stdint.h>

/* Raw ADC buffer (populated by DMA1 CH1 from ADC1_DR): 600 samples of 16-bit ADC counts */
extern volatile uint16_t g_AdcRawBuffer[600];

/* Stock firmware functions */
extern void stock_console_dispatch(uint8_t *req, uint32_t len);
/* Reads g_ScopeState+0x28, stores gain at 0x2000000c, drives the front-end GPIOs. Does not redraw the V/div label. */
extern void stock_apply_vdiv_gain(void);

/* UI layer: stock routines called from the main-loop hook */
extern void stock_ui_adjust(int32_t delta);
extern void stock_refresh_runstop(int32_t create);
extern void stock_widget_show(uint32_t widget, uint32_t flags);
extern void stock_capture_start(void);
extern void stock_single_wait(void);
extern uint32_t stock_key_consumer(uint32_t *arg);
extern volatile uint32_t g_UiState[];   /* 0x20000140: [1] = field cursor, [0x2c/4] = run/stop widget */

/* Screen capture: display driver at 0x2000b02c (flush_cb at +0x14), default disp pointer at 0x20000578 */
extern void stock_flush_cb(void *drv, const int16_t *area, const uint16_t *color_p);
extern void stock_obj_invalidate(uint32_t obj);

/* ADC1_2 interrupt handler (roll path, T/div 15..21): clears flags, shifts the 300-sample buffer, appends one scaled sample */
extern void stock_adc_isr(void);

/* Signal generator: settings the stock generator screen edits, and the routine that applies them.
 * ext_gen_apply() is an assembly shim: stock takes the amplitude in s0 (hard-float), the extension is soft-float. */
#define STOCK_GEN_AMP   (*(volatile uint32_t *)0x200006b8u)  /* float volts 0.01..3.3 */
#define STOCK_GEN_FREQ  (*(volatile int32_t *)0x200006b0u)   /* Hz 1..100000 */
#define STOCK_GEN_DUTY  (*(volatile int32_t *)0x200006b4u)   /* percent 0..100 */
#define STOCK_GEN_WAVE  (*(volatile int32_t *)0x200006fcu)   /* enum GenWave */
#define STOCK_GEN_SCREEN_MODE 4u                                   /* g_ScopeState mode of the generator screen */
#define STOCK_GEN_ROW   (*(volatile uint8_t *)0x200002b1u)         /* cursor row: 0 wave, 1 freq, 2 amp, 3 duty */
#define STOCK_GEN_DIGIT (*(volatile uint8_t *)0x200002b2u)         /* cursor digit inside the row */
extern void stock_gen_screen_update(int32_t build, int32_t delta);
extern void ext_gen_apply(uint32_t amp_bits, uint32_t wave, uint32_t freq, uint32_t duty);

/* Scope state: field offsets from g_ScopeState */
extern volatile uint8_t g_ScopeState[];
enum ScopeStateOffset {
    SS_MODE        = 0x20,
    SS_POSITION    = 0x24,  /* vertical position, -99..99 */
    SS_VDIV        = 0x28,
    SS_PROBE       = 0x2c,
    SS_TDIV        = 0x30,
    SS_COUPLING    = 0x34,
    SS_TRIGGER     = 0x38,  /* trigger level offset, -99..99 */
    SS_TRIGMODE    = 0x3c,  /* 0 Auto, 1 Normal, 2 Single */
    SS_EDGE        = 0x40,
    SS_KEY_TYPE    = 0x90,  /* key mailbox: 0 = empty, 1 = press, 0xFE = hold */
    SS_KEY_CODE    = 0x91,
    SS_BURST_DONE  = 0xa0,  /* set by the DMA1_CH1 handler: a burst is in the raw buffer */
    SS_RUNSTOP     = 0xa1,
    SS_SAMPLE_RATE = 0xb0,  /* float */
    SS_TRACE       = 0x594, /* display trace: 300 slots of two halfwords */
};
#define SS_U32(off) (*(volatile uint32_t *)(g_ScopeState + (off)))
#define SS_I32(off) (*(volatile int32_t *)(g_ScopeState + (off)))
#define SS_U8(off)  (*(volatile uint8_t *)(g_ScopeState + (off)))

/* 12-bit-to-volts gain code written by stock_apply_vdiv_gain(), read by the renderer */
#define STOCK_GAIN (*(volatile int32_t *)0x2000000cu)

static inline uint32_t scope_get_mode(void)      { return SS_U32(SS_MODE); }
static inline uint32_t scope_get_vdiv(void)      { return SS_U32(SS_VDIV); }
static inline uint32_t scope_get_probe(void)     { return SS_U32(SS_PROBE); }
static inline uint32_t scope_get_tdiv(void)      { return SS_U32(SS_TDIV); }
static inline uint32_t scope_get_coupling(void)  { return SS_U32(SS_COUPLING); }
static inline uint8_t  scope_get_run_stop(void)  { return SS_U8(SS_RUNSTOP); }
static inline void     scope_set_run_stop(uint8_t stop) { SS_U8(SS_RUNSTOP) = stop; }

#endif /* STOCK_SYMBOLS_H */
