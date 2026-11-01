"""SCPI server for the DSO-TC3 (see tc3_scpi.py): device wrapper, command interpreter, generator commands, Rigol emulation, transports."""
from .device import DeviceError, Device, DemoDevice
from .interpreter import Scpi, IDN
from .rigol import Rigol, RIGOL_IDN, RIGOL_POINTS, tbrep_seconds, tbrep_to_index
from .scales import DIV_MV, tdiv_seconds, tdiv_index, vdiv_index, vdiv_volts
from .server import run_stdio, run_tcp, main
