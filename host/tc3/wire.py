"""Wire constants of the extension protocol (firmware_ext/include/protocol.h). tests/check_protocol.py compares them."""

VID, PID = 0x19F5, 0x5740

CMD_READ_MEM  = 0x42
CMD_SNAP      = 0x50
CMD_GET_PARAM = 0x51
CMD_SET_PARAM = 0x52
CMD_FRAME     = 0x54
CMD_UI        = 0x55
CMD_SCREEN    = 0x56
CMD_ROLL      = 0x57
CMD_GEN       = 0x58
CMD_CFG       = 0x59
CMD_EXT_INFO  = 0x5F
RESP_READ_MEM = 0x43
RESP_ERROR    = 0x7E          # 0x7e cmd code

READ_MEM_MAX = 60
HEADER = 0xFF                 # chunk / op selector of a frame or roll header, and the UI poll argument
ACTION_DONE = 2               # state byte of a deferred action reply

# Error codes in a 0x7e reply
ERR_BAD_CHUNK, ERR_NO_FRAME, ERR_EMPTY = 2, 4, 5
TIMEOUT = -2                  # not a wire code: the device did not answer in time
FRAME_ERRORS = {1: "bad param", ERR_BAD_CHUNK: "bad chunk", ERR_NO_FRAME: "frame expired or not captured",
                ERR_EMPTY: "ring empty (no burst since boot)"}
ROLL_ERRORS = {ERR_BAD_CHUNK: "bad op", ERR_NO_FRAME: "samples expired or before the current T/div",
               ERR_EMPTY: "no sample recorded since boot"}

# Deferred UI actions (0x55). Field ids are the stock menu-cursor ids.
UI_FIELDS = {"position": 1, "vdiv": 2, "probe": 3, "tdiv": 4, "coupling": 5,
             "trigger": 6, "edge": 7, "trigmode": 8, "runstop": 0x10}
UI_KEY = 0x20
UI_RESULTS = {0: "ok", 6: "busy", 7: "unknown field", 8: "not in the scope view", 9: "value out of range",
              10: "key mailbox busy (a button is being pressed?)"}
UI_NAMES = {
    "position": "vertical position -99..99", "vdiv": "V/div index 0..9", "probe": "0 = X1, 1 = X10",
    "tdiv": "T/div index 0..21", "coupling": "0 = AC, 1 = DC", "trigger": "trigger level offset -99..99",
    "edge": "trigger edge 0/1", "trigmode": "0 Auto, 1 Normal, 2 Single", "runstop": "0 run, 1 stop",
}

# Virtual buttons (UI field 0x20): a short press posted to the stock key mailbox.
KEYS = {"back": 0x01, "select": 0x02, "left": 0x04, "right": 0x10, "up": 0x20, "down": 0x08, "plus": 0x20, "minus": 0x08,
        "hold-select": 0x102}   # long press of select: opens Settings from any other screen

# Screens by mode (g_ScopeState+0x20) and the main-menu item that opens them.
MODE_NAMES = {1: "menu", 2: "mtester", 3: "scope", 4: "generator", 5: "tools", 6: "settings", 100: "mode100"}
SCREEN_ITEMS = {"mtester": (0, 2), "scope": (1, 3), "generator": (2, 4), "tools": (3, 5)}   # name -> (menu item, mode)
MENU_ITEMS = 4

# Memory the host reads directly. g_ScopeState and the offsets of the fields that have no GET parameter.
SCOPE_STATE = 0x20000668
OFF_PROBE, OFF_COUPLING, OFF_TRIGMODE, OFF_EDGE = 0x2C, 0x34, 0x3C, 0x40
GAIN_ADDR = 0x2000000C
MENU_CURSOR_ADDR = 0x20000684
VOLUME_ADDR = 0x2000067C
SETTINGS_ROW_ADDR = 0x200006FC
VOLUME_ROW = 2

# Legacy get/set parameter ids (0x51/0x52), named by what the scope state field really is
# (the older names were "coupling" for the probe and "edge" for the coupling).
PARAMS = {"mode": 1, "vdiv": 2, "probe": 3, "tdiv": 4, "coupling": 5, "level": 6, "run_stop": 7}

# Signal generator (0x58)
GEN_GET, GEN_SET, GEN_LOG = 0, 1, 2
GEN_WAVES = {"sine": 0, "square": 1, "pulse": 2, "triangle": 3, "ramp": 4, "dc": 5}
GEN_WAVE_NAMES = {v: k for k, v in GEN_WAVES.items()}
GEN_FREQ_RANGE = (1, 100000)
GEN_AMP_RANGE = (0.01, 3.3)   # volts, output swings 0..amp

SCR_W, SCR_H = 320, 240

ROLL_MIN_TDIV = 15
ROLL_OP_READ = 0x01
