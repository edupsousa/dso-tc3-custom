"""The PASS/FAIL harness the offline tests share. usage: `from testkit import check, finish`; call finish() last."""
import sys

fails = 0


def check(name, cond, extra=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + (" " + str(extra) if extra else ""))
    fails += not cond
    return bool(cond)


def finish():
    print("FAILS:", fails)
    sys.exit(1 if fails else 0)
