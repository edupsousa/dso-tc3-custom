"""Deferred UI actions (0x55), virtual buttons, screen navigation, the Volume setting and the legacy mode read."""
import struct
import time

from .link import deferred, request, UiError, Tc3Error
from .memory import read_mem
from .wire import *


def ui_apply(ser, field, value, timeout=2.0):
    """Queue a UI action, then poll until the main loop has run it. Raises UiError."""
    deferred(ser, struct.pack("<BBi", CMD_UI, field, value), CMD_UI, bytes([CMD_UI, HEADER]), 9, timeout=timeout)


def get_mode(ser):
    try:
        r = request(ser, bytes([CMD_GET_PARAM, PARAMS["mode"]]), CMD_GET_PARAM, 6, {})
    except Tc3Error:
        raise UiError("could not read the screen mode")
    return struct.unpack_from("<I", r, 2)[0]


def menu_cursor(ser):
    return read_mem(ser, MENU_CURSOR_ADDR, 1)[0]


def press(ser, key, settle=0.4):
    """Post one virtual short press (a name from KEYS or a mask) and wait for the main loop to take it."""
    ui_apply(ser, UI_KEY, KEYS[key] if isinstance(key, str) else key)
    time.sleep(settle)


def wait_mode(ser, want, timeout=4.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if get_mode(ser) == want:
            return True
        time.sleep(0.1)
    return False


def goto_screen(ser, target):
    """Navigate to 'menu', 'mtester', 'scope', 'generator', 'tools' or 'settings' with virtual buttons. Returns the mode reached."""
    if target == "settings":
        if get_mode(ser) == 6:
            return 6
        press(ser, "hold-select")
        if not wait_mode(ser, 6, timeout=6.0):
            raise UiError(f"hold-select did not open the settings screen (mode {get_mode(ser)})")
        return 6
    if target != "menu" and target not in SCREEN_ITEMS:
        raise UiError(f"unknown screen '{target}' (menu, settings, {', '.join(SCREEN_ITEMS)})")
    mode = get_mode(ser)
    if target == "menu" and mode == 1:
        return mode
    if mode != 1:                                # every screen's back key is 0x01
        press(ser, "back")
        if not wait_mode(ser, 1):
            raise UiError(f"back from {MODE_NAMES.get(mode, mode)} did not reach the menu (mode {get_mode(ser)})")
    if target == "menu":
        return 1
    item, want = SCREEN_ITEMS[target]
    for _ in range(MENU_ITEMS):
        cur = menu_cursor(ser)
        if cur == item:
            break
        press(ser, "right" if (item - cur) % MENU_ITEMS <= MENU_ITEMS // 2 else "left")
    else:
        raise UiError(f"menu cursor never reached item {item} (at {menu_cursor(ser)})")
    press(ser, "select")
    if not wait_mode(ser, want, timeout=6.0):
        raise UiError(f"menu item {item} did not open the {target} screen (mode {get_mode(ser)})")
    return want


# Settings screen: rows 0 Logo, 1 Language, 2 Volume, ...; up/down move the row, left/right change the value.
def get_volume(ser):
    return read_mem(ser, VOLUME_ADDR, 1)[0]


def set_volume(ser, target, settle=0.12):
    """Set the stock Volume (0..100) through the Settings screen with virtual keys. Leaves the device on the menu.
    Returns the previous value. Silent beeps: set_volume(ser, 0), then restore the returned value."""
    if not 0 <= target <= 100:
        raise UiError("volume must be 0..100")
    goto_screen(ser, "settings")
    try:
        for _ in range(8):                                   # move to the Volume row
            row = read_mem(ser, SETTINGS_ROW_ADDR, 1)[0]
            if row == VOLUME_ROW:
                break
            press(ser, "down" if row < VOLUME_ROW else "up", settle=0.3)
        else:
            raise UiError("Settings list did not reach the Volume row")
        start = cur = get_volume(ser)
        while cur != target:
            press(ser, "right" if cur < target else "left", settle=settle)
            nxt = get_volume(ser)
            if nxt == cur:
                raise UiError(f"volume stuck at {cur} (wanted {target})")
            cur = nxt
    finally:
        goto_screen(ser, "menu")
    return start
