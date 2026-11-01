"""Scale tables and conversions shared by the SCPI layers."""
import math

# Volts/div divisor table (mV at an X1 probe), g_ScopeState+0x28 index 0..9.
DIV_MV = (10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000)
FRAME_TDIV_MIN, FRAME_TDIV_MAX = 0, 14     # T/div indices that produce burst frames
ROLL_WINDOW = 300                           # samples on the device screen in roll mode


def tdiv_seconds(idx):
    """T/div index 0..21 = 1us, 2us, 5us, 10us ... 10s."""
    return (1, 2, 5)[idx % 3] * 10.0 ** (-6 + idx // 3)


def tdiv_index(seconds):
    return min(range(22), key=lambda i: abs(tdiv_seconds(i) - seconds))


def vdiv_volts(idx, probe_mult):
    return DIV_MV[idx] / 1000.0 * probe_mult


def vdiv_index(volts, probe_mult):
    """The V/div step nearest to `volts` per division at this probe multiplier."""
    want = volts / probe_mult * 1000.0
    return min(range(len(DIV_MV)), key=lambda i: abs(DIV_MV[i] - want))
