/*
 * Deferred UI actions. The USB handler runs in interrupt
 * context, where stock drawing code must not be called. It only queues a
 * request here; extension_ui_hook() replaces the main loop's
 * "bl 0x08007cb0" (key consumer, once per pass) and runs the request in main
 * loop context by driving the same routines a button press drives: the
 * stock adjust(delta) routine, which edits the field chosen by the menu
 * cursor and redraws its label.
 */
#include "ext.h"

#define SCOPE_MODE_LIVE 3u
#define SCOPE_TESTER_FLAG (*(volatile uint32_t *)0x20000704u)  /* 0 while the scope view is active */

struct {
    volatile uint32_t state;
    volatile uint32_t result;
    uint32_t field;
    int32_t value;
} s_ui;

/*
 * Fields the stock adjust() edits, by menu cursor id. `how` says what delta makes the field reach the target:
 *   ADJ_STEP    adjust(target - current)
 *   ADJ_NEGATE  adjust(current - target): the stock routine subtracts the delta for the position and trigger level
 *   ADJ_TOGGLE  adjust(1) flips 0 <-> 1, the argument is ignored
 *   ADJ_CYCLE   adjust(1) steps to the next value, wrapping; at most 3 steps
 */
enum { ADJ_STEP, ADJ_NEGATE, ADJ_TOGGLE, ADJ_CYCLE };
static const struct ui_field {
    uint8_t id, off, how;
    int8_t lo, hi;
} s_ui_fields[] = {
    { UI_POSITION, SS_POSITION, ADJ_NEGATE, -99, 99 },
    { UI_TRIGGER,  SS_TRIGGER,  ADJ_NEGATE, -99, 99 },
    { UI_VDIV,     SS_VDIV,     ADJ_STEP,   0, 9 },
    { UI_PROBE,    SS_PROBE,    ADJ_STEP,   0, 1 },   /* stock also refreshes the V/div label */
    { UI_TDIV,     SS_TDIV,     ADJ_STEP,   0, 21 },
    { UI_COUPLING, SS_COUPLING, ADJ_TOGGLE, 0, 1 },
    { UI_EDGE,     SS_EDGE,     ADJ_TOGGLE, 0, 1 },
    { UI_TRIGMODE, SS_TRIGMODE, ADJ_CYCLE,  0, 2 },
};

static void ui_adjust_field(uint32_t field, int32_t delta) {
    int32_t saved = (int32_t)g_UiState[1];
    g_UiState[1] = field;
    stock_ui_adjust(delta);
    g_UiState[1] = (uint32_t)saved;
}

static uint32_t ui_set_field(const struct ui_field *f, int32_t t) {
    if (t < f->lo || t > f->hi) return UI_ERR_RANGE;
    int32_t cur = SS_I32(f->off);
    switch (f->how) {
    case ADJ_STEP:   if (cur != t) ui_adjust_field(f->id, t - cur); break;
    case ADJ_NEGATE: if (cur != t) ui_adjust_field(f->id, cur - t); break;
    case ADJ_TOGGLE: if (cur != t) ui_adjust_field(f->id, 1); break;
    default:         /* ADJ_CYCLE */
        for (int i = 0; i < 3 && SS_I32(f->off) != t; i++) ui_adjust_field(f->id, 1);
        break;
    }
    return UI_OK;
}

/*
 * Virtual key press. The scanner (10 ms tick) only writes the mailbox (g_ScopeState +0x90 type, +0x91 key) on a
 * release or a held key, so with nobody on the buttons it stays idle. We write a short press (type 1) here, in
 * the main loop, and the stock key consumer that the hook calls next dispatches it by screen exactly like a
 * button (including its beep). Key masks: 1 back, 2 select/Auto, 4 up/left, 0x10 down/right, 0x20 / 8 +/-.
 * Value 0x102 is a held select (type 0xFE): the consumer's hold branch opens the Settings screen.
 */
#define KEY_PRESS 1
#define KEY_HOLD  0xFE

static uint32_t ui_post_key(int32_t key) {
    uint8_t type = KEY_PRESS;
    if (key == UI_KEY_HOLD_SELECT) { key = 2; type = KEY_HOLD; }   /* held select: consumer opens Settings (mode 6) */
    if (key != 1 && key != 2 && key != 4 && key != 8 && key != 0x10 && key != 0x20) return UI_ERR_RANGE;
    uint32_t pm = irq_save(), rc = UI_OK;
    if (SS_U8(SS_KEY_TYPE) != 0) {
        rc = UI_ERR_KEY_BUSY;
    } else {
        SS_U8(SS_KEY_CODE) = (uint8_t)key;
        SS_U8(SS_KEY_TYPE) = type;
    }
    irq_restore(pm);
    return rc;
}

static uint32_t ui_set_runstop(int32_t t) {
    if (t < 0 || t > 1) return UI_ERR_RANGE;
    if (scope_get_run_stop() != t) {
        /* stock hold-key path at 0x08008074, minus the beep and the repeat latch */
        scope_set_run_stop((uint8_t)t);
        stock_refresh_runstop(0);
        if (t == 0) stock_widget_show(g_UiState[0x2c / 4], 1);
        if (SS_I32(SS_TRIGMODE) == 2) { stock_capture_start(); stock_single_wait(); }
    }
    return UI_OK;
}

static uint32_t ui_execute(uint32_t field, int32_t t) {
    if (field == UI_KEY) return ui_post_key(t);
    if (scope_get_mode() != SCOPE_MODE_LIVE || SCOPE_TESTER_FLAG != 0) {
        return UI_ERR_NOT_SCOPE;
    }
    if (field == UI_RUNSTOP) return ui_set_runstop(t);
    for (uint32_t i = 0; i < sizeof(s_ui_fields) / sizeof(s_ui_fields[0]); i++) {
        if (s_ui_fields[i].id == field) return ui_set_field(&s_ui_fields[i], t);
    }
    return UI_ERR_FIELD;
}

/* Run the queued request, if any. Called from extension_ui_hook. */
void ui_hook_step(void) {
    if (s_ui.state == UI_PENDING) {
        s_ui.result = ui_execute(s_ui.field, s_ui.value);
        s_ui.state = UI_DONE;
    }
}

/* 0x55 | field | value LE32 queues a request; field 0xFF polls: 0x55 0xFF state result field value. */
bool handle_ui(const uint8_t *req, uint32_t len) {
    uint8_t field;
    uint32_t val = 0;
    if (len >= 6) {
        field = req[1];
        val = get_u32(&req[2]);
    } else if (len >= 2 && req[1] == UI_POLL) {
        field = UI_POLL;
    } else {
        return false;
    }

    s_resp_buf[0] = RESP_UI;
    s_resp_buf[1] = field;
    if (field == UI_POLL) {
        s_resp_buf[2] = (uint8_t)s_ui.state;
        s_resp_buf[3] = (uint8_t)s_ui.result;
        s_resp_buf[4] = (uint8_t)s_ui.field;
        put_u32(&s_resp_buf[5], (uint32_t)s_ui.value);
        ext_reply(9);
        if (s_ui.state == UI_DONE) s_ui.state = UI_IDLE;
        return true;
    }
    if (s_ui.state == UI_PENDING) {
        s_resp_buf[2] = UI_ERR_BUSY;
    } else {
        s_ui.field = field;
        s_ui.value = (int32_t)val;
        mem_barrier();
        s_ui.state = UI_PENDING;
        s_resp_buf[2] = UI_OK;          /* queued, not yet executed: poll for the result */
    }
    ext_reply(3);
    return true;
}

uint32_t extension_ui_hook(uint32_t *arg) {
    ext_init();
    screen_hook_step();
    gen_hook_step();
    cfg_hook_step();
    ui_hook_step();
    return stock_key_consumer(arg);
}
