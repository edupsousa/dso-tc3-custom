"""Host library for the DSO-TC3 custom firmware extension. Every function takes an open serial connection (`open_conn`)."""
from .wire import *
from .link import Tc3Error, UiError, FrameError, RollError, find_device_port, candidate_ports, open_conn
from .memory import read_mem, read_gain
from .volts import PROBE_MULT, GAIN_BY_VDIV, raw_to_volts, cal_volts, roll_volts, to_volts
from .ui import ui_apply, get_mode, menu_cursor, press, wait_mode, goto_screen, get_volume, set_volume
from .gen import gen_get, gen_set, gen_log
from .screen import rgb565_to_rgb, write_png, capture_screen
from .frames import INTERLEAVED_RATE_KSPS, frame_header, fetch_frame, frame_volts
from .roll import roll_header, roll_read, roll_last, roll_collect
from .params import get_param, set_param, snap
