/*
 * Signal generator. The stock generator screen keeps
 * wave/freq/duty/amplitude in RAM and calls stock_gen_apply() when the user
 * finishes an edit. We do the same from the main loop: write the variables,
 * then apply. Limits are the stock UI limits.
 */
#include "ext.h"

__asm__(".thumb\n.fpu fpv4-sp-d16\n.thumb_func\n.global ext_gen_apply\next_gen_apply:\n"
        "push {r4, lr}\n"
        "vmov s0, r0\n"
        "mov r0, r1\n"
        "mov r1, r2\n"
        "mov r2, r3\n"
        "bl stock_gen_apply\n"
        "pop {r4, pc}\n");

#define GEN_AMP_MIN 0x3c23d70au   /* 0.01 V, float bits (positive floats order like integers) */
#define GEN_AMP_MAX 0x40533333u   /* 3.3 V */

struct {
    volatile uint32_t state;
    volatile uint32_t result;
    uint32_t wave, freq, duty, amp;
} s_gen;

/*
 * The stock DAC table (0x20000bfc) is the same RAM as the scope's trace buffer, which the
 * renderer and the roll ISR keep overwriting, so the DMA streams scope data instead of the
 * waveform. After the stock routine has built the table, copy it to private RAM and point
 * the DAC DMA channel (DMA2 ch3: CTRL, count, periph addr, mem addr) there. Interrupts stay
 * masked from the table build to the copy so the roll ISR cannot overwrite it in between.
 */
#define STOCK_GEN_TABLE ((volatile uint16_t *)0x20000bfcu)
#define GEN_DMA ((volatile uint32_t *)0x40020430u)
#define GEN_DMA_CTRL  0
#define GEN_DMA_COUNT 1
#define GEN_DMA_MADDR 3
#define GEN_DMA_EN    1u
#define GEN_TIM6_HZ 72000000u
#define GEN_TIM6_ARR (*(volatile uint32_t *)0x4000102cu)
#define GEN_TABLE_MAX 1200
static uint16_t s_gen_table[GEN_TABLE_MAX] __attribute__((aligned(4)));

/* What the scope had at 0x20000bfc before the stock routine used it as scratch for the table. Put back after the
 * copy: otherwise the scope reads the table as trace samples (pairs of halfwords as 32-bit values: Vmax over 300 V on
 * the screen) until the acquisition overwrites them, which takes seconds at the roll timebases. */
#define SCOPE_SAVE_N 600      /* the trace buffer: 300 x 4 B (all the extension RAM has room for) */
static uint16_t s_scope_save[SCOPE_SAVE_N];

/* Called before every stock apply (from the hook stub, which also runs on the first boot call: it initialises the
 * extension state). Soft-float C: it never touches s0, which the stub still needs. */
__attribute__((used)) void ext_gen_save(void) {
    ext_init();
    for (uint32_t i = 0; i < SCOPE_SAVE_N; i++) s_scope_save[i] = STOCK_GEN_TABLE[i];
}

static uint32_t gen_table_len(uint32_t freq) {   /* the stock band sizes (FUN_08005da0) */
    return freq < 101 ? 1200 : freq < 10001 ? 180 : freq < 50001 ? 72 : 24;
}

/* Log of the stock apply calls (debug aid: `gen --log`, how the scope-entry reset was found). */
#define GEN_LOG_N 8
static struct { uint32_t total; struct { uint32_t lr, freq; uint8_t wave, duty; } e[GEN_LOG_N]; } s_genlog;

/* What the DAC was last programmed with. The stock settings variables only hold what the generator screen
 * edits (power-on defaults until then), while the boot init applies a 1 V 1 kHz square: GEN_GET reports this. */
struct { uint32_t valid, wave, freq, duty, amp; } ext_gen_out;   /* amp at +16: the hook stub stores it */

/* Point the DAC DMA at a private copy of the table the stock routine just built, with the exact TIM6 reload.
 * Runs after every stock FUN_08005da0 call (see extension_gen_apply_hook): 0x58 sets, the generator screen's
 * button edits, the defaults reset and the boot init. Interrupts are masked for the copy.
 * lr = return address of the stock call site (0xFFFFFFFF for 0x58 sets). */
__attribute__((used)) void ext_gen_retarget(uint32_t freq, uint32_t lr, uint32_t wave, uint32_t duty) {
    ext_init();
    uint32_t k = s_genlog.total++ % GEN_LOG_N;
    s_genlog.e[k].lr = lr; s_genlog.e[k].freq = freq; s_genlog.e[k].wave = (uint8_t)wave; s_genlog.e[k].duty = (uint8_t)duty;
    ext_gen_out.wave = wave; ext_gen_out.freq = freq; ext_gen_out.duty = duty; ext_gen_out.valid = 1;
    if (freq == 0) return;
    uint32_t pm = irq_save();
    uint32_t n = gen_table_len(freq);
    for (uint32_t i = 0; i < n; i++) s_gen_table[i] = STOCK_GEN_TABLE[i];
    for (uint32_t i = 0; i < n && i < SCOPE_SAVE_N; i++) STOCK_GEN_TABLE[i] = s_scope_save[i];
    /* Stock reload = 390000/f etc. assumes a 70.2 MHz timer clock; TIM6 runs at 72 MHz (about 2.5% fast,
     * more at 20 kHz from integer rounding). Exact value: ARR + 1 = 72e6 / (f * points), rounded. */
    uint32_t upd = freq * n;
    uint32_t arr = (GEN_TIM6_HZ + upd / 2) / upd;
    GEN_TIM6_ARR = (arr > 1 ? arr : 2) - 1;
    GEN_DMA[GEN_DMA_CTRL] &= ~GEN_DMA_EN;                 /* channel off */
    GEN_DMA[GEN_DMA_MADDR] = (uint32_t)s_gen_table;
    GEN_DMA[GEN_DMA_COUNT] = n;
    GEN_DMA[GEN_DMA_CTRL] |= GEN_DMA_EN;                  /* channel on, circular */
    irq_restore(pm);
}

/* Replaces the three "bl 0x08005da0" call sites: same arguments (amp in s0, wave r0, freq r1, duty r2). */
__asm__(".thumb\n.thumb_func\n.global extension_gen_apply_hook\nextension_gen_apply_hook:\n"
        "push {r0, r1, r2, lr}\n"
        "bl ext_gen_save\n"       /* clobbers r0-r3, not s0 (soft-float C); r0-r2 are reloaded below */
        "ldr r3, =ext_gen_out+16\n"   /* amp (s0) is lost in the stock call: keep it */
        "vstr s0, [r3]\n"
        "ldr r0, [sp, #0]\n"
        "ldr r1, [sp, #4]\n"
        "ldr r2, [sp, #8]\n"
        "bl stock_gen_apply\n"
        "ldr r0, [sp, #4]\n"       /* freq */
        "ldr r1, [sp, #12]\n"      /* caller's return address */
        "ldr r2, [sp, #0]\n"       /* wave */
        "ldr r3, [sp, #8]\n"       /* duty */
        "bl ext_gen_retarget\n"
        "pop {r0, r1, r2, pc}\n"
        ".ltorg\n");

/*
 * Generator screen entry (menu item 2, builder 0x08010f0c). Its first state write, "str.w r6,[r4,#0x94]" at
 * 0x08010f1a, stores 0 in the wave variable, so the screen always opened on Sine whatever the output was, and
 * the next button press would apply that. It becomes "bl extension_gen_screen_enter": the screen opens on the
 * wave, frequency, duty and amplitude the DAC was last given. Before any apply it keeps the stock behaviour.
 */
__attribute__((used)) void ext_gen_screen_seed(void) {
    ext_init();
    if (ext_gen_out.valid) {
        STOCK_GEN_WAVE = (int32_t)ext_gen_out.wave;
        STOCK_GEN_FREQ = (int32_t)ext_gen_out.freq;
        STOCK_GEN_DUTY = (int32_t)ext_gen_out.duty;
        STOCK_GEN_AMP = ext_gen_out.amp;
    } else {
        STOCK_GEN_WAVE = 0;
    }
}

/* Called mid-function with r0, r4, r5, r6 live: keep every register. */
__asm__(".thumb\n.thumb_func\n.global extension_gen_screen_enter\nextension_gen_screen_enter:\n"
        "push {r0, r1, r2, r3, r12, lr}\n"
        "bl ext_gen_screen_seed\n"
        "pop {r0, r1, r2, r3, r12, pc}\n");

/* Run the queued 0x58 set, if any. Called from extension_ui_hook. */
void gen_hook_step(void) {
    if (s_gen.state != UI_PENDING) return;
    int32_t old_wave = STOCK_GEN_WAVE;
    if ((uint32_t)old_wave > GEN_DC) { STOCK_GEN_WAVE = 0; old_wave = 0; }
    STOCK_GEN_FREQ = (int32_t)s_gen.freq;
    STOCK_GEN_DUTY = (int32_t)s_gen.duty;
    STOCK_GEN_AMP = s_gen.amp;
    if (scope_get_mode() == STOCK_GEN_SCREEN_MODE) {
        /* Generator screen on display: let its own routine move the wave selector and redraw the labels and
         * the preview, then apply. The table is stable here (the scope is idle). Cursor to the wave row:
         * a row hidden by the new wave must not keep it. */
        STOCK_GEN_ROW = 0;
        STOCK_GEN_DIGIT = 0;
        stock_gen_screen_update(0, (int32_t)s_gen.wave - old_wave);   /* its apply call is patched: retargets itself */
    } else {
        STOCK_GEN_WAVE = (int32_t)s_gen.wave;
        uint32_t pm = irq_save();
        ext_gen_save();
        ext_gen_apply(s_gen.amp, s_gen.wave, s_gen.freq, s_gen.duty);
        ext_gen_out.amp = s_gen.amp;
        ext_gen_retarget(s_gen.freq, 0xFFFFFFFFu, s_gen.wave, s_gen.duty);
        irq_restore(pm);
    }
    s_gen.result = UI_OK;
    s_gen.state = UI_DONE;
}

/*
 * 0x58 0 = get:  reply 0x58 0 state result wave freq32 duty amp32
 * 0x58 1 = set:  0x58 1 wave freq32 duty amp32 (float bits); queued, applied by gen_hook_step; poll with get
 * 0x58 2 idx = apply log, newest first: 0x58 2 idx total32 lr32 wave duty freq32
 */
static void gen_get(void) {
    s_resp_buf[2] = (uint8_t)s_gen.state;
    s_resp_buf[3] = (uint8_t)s_gen.result;
    if (ext_gen_out.valid) {
        s_resp_buf[4] = (uint8_t)ext_gen_out.wave;
        put_u32(&s_resp_buf[5], ext_gen_out.freq);
        s_resp_buf[9] = (uint8_t)ext_gen_out.duty;
        put_u32(&s_resp_buf[10], ext_gen_out.amp);
    } else {   /* no apply yet: the stock variables */
        s_resp_buf[4] = (uint8_t)STOCK_GEN_WAVE;
        put_u32(&s_resp_buf[5], (uint32_t)STOCK_GEN_FREQ);
        s_resp_buf[9] = (uint8_t)STOCK_GEN_DUTY;
        put_u32(&s_resp_buf[10], STOCK_GEN_AMP);
    }
    ext_reply(14);
    if (s_gen.state == UI_DONE) s_gen.state = UI_IDLE;
}

static void gen_log(uint32_t idx) {
    uint32_t total = s_genlog.total;
    if (idx >= GEN_LOG_N || idx >= total) { s_resp_buf[2] = ERR_BAD_PARAM; ext_reply(3); return; }
    uint32_t k = (total - 1 - idx) % GEN_LOG_N;
    s_resp_buf[2] = (uint8_t)idx;
    put_u32(&s_resp_buf[3], total);
    put_u32(&s_resp_buf[7], s_genlog.e[k].lr);
    s_resp_buf[11] = s_genlog.e[k].wave;
    s_resp_buf[12] = s_genlog.e[k].duty;
    put_u32(&s_resp_buf[13], s_genlog.e[k].freq);
    ext_reply(17);
}

static void gen_set(const uint8_t *req) {
    uint32_t wave = req[2], freq = get_u32(&req[3]), duty = req[7], amp = get_u32(&req[8]);
    if (s_gen.state == UI_PENDING) {
        s_resp_buf[2] = GEN_ERR_BUSY;
    } else if (wave > GEN_DC || freq < 1 || freq > GEN_FREQ_MAX || duty > 100 ||
               amp < GEN_AMP_MIN || amp > GEN_AMP_MAX) {
        s_resp_buf[2] = GEN_ERR_RANGE;
    } else {
        s_gen.wave = wave; s_gen.freq = freq; s_gen.duty = duty; s_gen.amp = amp;
        mem_barrier();
        s_gen.state = UI_PENDING;
        s_resp_buf[2] = UI_OK;          /* queued: poll with GEN_GET for state DONE */
    }
    ext_reply(3);
}

bool handle_gen(const uint8_t *req, uint32_t len) {
    if (len < 2) return false;
    s_resp_buf[0] = RESP_GEN;
    s_resp_buf[1] = req[1];
    if (req[1] == GEN_GET) {
        gen_get();
    } else if (req[1] == GEN_LOG && len >= 3) {
        gen_log(req[2]);
    } else if (req[1] == GEN_SET && len >= 12) {
        gen_set(req);
    } else {
        s_resp_buf[2] = ERR_BAD_PARAM;
        ext_reply(3);
    }
    return true;
}
