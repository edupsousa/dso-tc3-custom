#ifndef PROTOCOL_H
#define PROTOCOL_H

#include <stdint.h>

#define EXTENSION_VERSION_MAJOR 1
#define EXTENSION_VERSION_MINOR 1
#define EXTENSION_VERSION_PATCH 0
#define EXTENSION_BANNER        "TC3-EXT:v1.1.0"   /* 0x5F reply: 3 version bytes, then this text (at most 24 characters) */

/* Command IDs */
#define CMD_READ_MEM    0x42  /* Handled by gate stub / extension (0x42 | addr LE32 | n LE16 -> 0x43 | n bytes) */
#define CMD_SNAP        0x50  /* Frame snapshot (F1) */
#define CMD_GET_PARAM   0x51  /* Read scope state parameters (F2) */
#define CMD_SET_PARAM   0x52  /* Write scope state parameters (F2) */
#define CMD_FRAME       0x54  /* Ring-buffered burst frames (F3): 0x54 | chunk | seq LE32 */
#define CMD_ROLL        0x57  /* Roll-mode sample stream (T/div 15..21): 0x57 0xFF = header, 0x57 0x01 | from LE32 = samples */
#define CMD_UI          0x55  /* Deferred UI action (main-loop hook): 0x55 | field | value LE32; field 0xFF = poll */
#define CMD_SCREEN      0x56  /* Screen capture control: 0x56 | op [| arg] (see handle_screen) */
#define CMD_GEN         0x58  /* Signal generator: 0x58 0x00 = get, 0x58 0x01 | wave | freq LE32 | duty | amp float32 LE = set */
#define CMD_EXT_INFO    0x5F  /* Extension query / ping */

/* Response Tags */
#define RESP_READ_MEM   0x43
#define RESP_SNAP       0x50
#define RESP_GET_PARAM  0x51
#define RESP_SET_PARAM  0x52
#define RESP_FRAME      0x54
#define RESP_ROLL       0x57
#define RESP_UI         0x55
#define RESP_SCREEN     0x56
#define RESP_GEN        0x58
#define RESP_EXT_INFO   0x5F
#define RESP_ERROR      0x7E

/* RESP_ERROR codes (byte 2) */
#define ERR_BAD_PARAM   0x01
#define ERR_BAD_CHUNK   0x02
#define ERR_RANGE       0x03  /* value out of range (GET/SET param) */
#define ERR_NO_FRAME    0x04  /* requested seq expired from the ring, or not captured yet */
#define ERR_EMPTY       0x05  /* ring empty: no burst captured since boot */

/* UI fields: ids of the stock menu cursor (g_UiState[1]) */
enum UiField {
    UI_POSITION = 1,   /* +0x24 vertical position, -99..99 */
    UI_VDIV     = 2,   /* +0x28 0..9 */
    UI_PROBE    = 3,   /* +0x2c 0 = X1, 1 = X10 */
    UI_TDIV     = 4,   /* +0x30 0..21 */
    UI_COUPLING = 5,   /* +0x34 0 = AC, 1 = DC */
    UI_TRIGGER  = 6,   /* +0x38 trigger level offset, -99..99 */
    UI_EDGE     = 7,   /* +0x40 trigger edge 0/1 */
    UI_TRIGMODE = 8,   /* +0x3c 0 Auto, 1 Normal, 2 Single (also clears STOP) */
    UI_RUNSTOP  = 0x10, /* +0xa1 0 = run, 1 = stop (stock hold-key path, no beep) */
    UI_KEY      = 0x20  /* value = key mask 1/2/4/8/0x10/0x20: a short press posted to the stock key mailbox, any screen;
                           UI_KEY_HOLD_SELECT (0x102) is the long press of select, which opens the settings screen */
};
#define UI_KEY_HOLD_SELECT 0x102
#define UI_POLL 0xFF

/* UI request states (poll reply byte 2) and results (byte 3) */
#define UI_IDLE 0
#define UI_PENDING 1
#define UI_DONE 2
#define UI_OK 0
#define UI_ERR_BUSY 6
#define UI_ERR_FIELD 7
#define UI_ERR_NOT_SCOPE 8
#define UI_ERR_RANGE 9
#define UI_ERR_KEY_BUSY 10   /* the stock key mailbox still holds an unconsumed event (a person is pressing buttons) */

/* Generator waveforms (stock 0x200006fc) */
enum GenWave { GEN_SINE = 0, GEN_SQUARE = 1, GEN_PULSE = 2, GEN_TRIANGLE = 3, GEN_RAMP = 4, GEN_DC = 5 };
#define GEN_GET 0
#define GEN_SET 1
#define GEN_LOG 2   /* 0x58 2 idx -> 0x58 2 idx total32 lr32 wave duty freq32: log of stock apply calls (idx 0 = newest) */
#define GEN_FREQ_MAX 100000
/* get reply: 0x58 0x00 state result wave freq32 duty amp32 (state/result as the UI request: UI_IDLE/PENDING/DONE) */
#define GEN_ERR_BUSY  UI_ERR_BUSY
#define GEN_ERR_RANGE UI_ERR_RANGE

/* Screen capture ops (byte 1 of 0x56) */
#define SCR_DISARM 0
#define SCR_ARM    1   /* arg byte 2: 1 = also force a full-screen redraw */
#define SCR_POLL   2   /* reply: 0x56 2 state armed seq32 x1 y1 x2 y2 (int16) px32 timeouts */
#define SCR_ACK    3   /* release the strip held in the flush callback */

/* Maximum CDC single-packet chunk size */
#define CDC_MAX_PACKET  64

/* Parameter IDs for GET/SET (0x51/0x52). Raw g_ScopeState fields: SET does not redraw anything (use 0x55 for that).
 * Ids 3 and 5 were named COUPLING and TRIGGER_EDGE before the UI map showed what the fields are; the wire ids are unchanged. */
enum ParamId {
    PARAM_MODE          = 0x01, /* +0x20: 1 menu, 2 M-Tester, 3 scope, 4 generator, 5 tools, 6 settings */
    PARAM_VDIV          = 0x02, /* +0x28: 0..9 */
    PARAM_PROBE         = 0x03, /* +0x2c: 0 = X1, 1 = X10 */
    PARAM_TDIV          = 0x04, /* +0x30: 0..21 */
    PARAM_COUPLING      = 0x05, /* +0x34: 0 = AC, 1 = DC */
    PARAM_TRIGGER_LEVEL = 0x06, /* +0x38: trigger level offset, -99..99 */
    PARAM_RUN_STOP      = 0x07, /* +0xa1: 0 = run, 1 = stop */
};

#endif /* PROTOCOL_H */
