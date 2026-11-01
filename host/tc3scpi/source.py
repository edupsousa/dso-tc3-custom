"""SOURce commands: the signal generator (0x58). FUNCtion, FREQuency, VOLTage[:AMPLitude], DCYCle."""
import tc3 as T
from .device import DeviceError

FUNCS = {"SIN": 0, "SQU": 1, "PULS": 2, "TRI": 3, "RAMP": 4, "DC": 5}
FUNC_NAMES = {v: k for k, v in FUNCS.items()}


def handle(scpi, k, query, arg):
    """`k` is the short-form key after "SOUR:"."""
    dev, num = scpi.dev, scpi.num
    if k.startswith("PULS:"):
        k = k[5:]
    if k == "FUNC":
        if query:
            return FUNC_NAMES[dev.gen_get()["wave"]]
        name = arg.upper()
        for n in (4, 3):
            if name not in FUNCS and name[:n] in FUNCS:
                name = name[:n]
        if name not in FUNCS:
            raise DeviceError(-224, "SOURce:FUNCtion must be SINusoid, SQUare, PULSe, TRIangle, RAMP or DC")
        dev.gen_set(wave=FUNCS[name])
        return None
    if k == "FREQ":
        if query:
            return num(float(dev.gen_get()["freq"]))
        f = float(arg)
        if not T.GEN_FREQ_RANGE[0] <= f <= T.GEN_FREQ_RANGE[1]:
            raise DeviceError(-222, "frequency must be 1..100000 Hz")
        dev.gen_set(freq=int(round(f)))
        return None
    if k in ("VOLT", "VOLT:AMPL"):
        if query:
            return num(dev.gen_get()["amp"])
        v = float(arg)
        if not T.GEN_AMP_RANGE[0] <= v <= T.GEN_AMP_RANGE[1]:
            raise DeviceError(-222, "amplitude must be 0.01..3.3 V (output swings 0..amplitude)")
        dev.gen_set(amp=v)
        return None
    if k == "DCYC":
        if query:
            return str(dev.gen_get()["duty"])
        d = float(arg)
        if not 0 <= d <= 100:
            raise DeviceError(-222, "duty cycle must be 0..100 %")
        dev.gen_set(duty=int(round(d)))
        return None
    raise DeviceError(-113, f"Undefined header: SOURce:{k}")
