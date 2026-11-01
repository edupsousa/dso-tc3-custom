#!/usr/bin/env bash
# Every offline test (no hardware): host library and tools, firmware (Unicorn), sigrok driver.
# usage: ./run_tests.sh      (set PKGS to the nixpkgs expression for the sigrok build, see sigrok/README.md; unset = skip sigrok)
cd "${TC3_ROOT:-$(dirname "$0")}" || exit 1
fail=0
run() { out=$("$@" 2>&1); rc=$?; printf '%-45s %s\n' "$*" "$([ $rc = 0 ] && echo "ok  ($(grep -c '^PASS' <<<"$out") checks)" || echo FAILED)"; [ $rc = 0 ] || { echo "$out" | tail -15; fail=1; }; }
for t in host/tests/check_protocol.py host/tests/test_client.py host/tests/test_cal.py host/tests/test_shots.py \
         host/tests/test_roll_host.py host/tests/test_rigol_emu.py firmware_ext/tests/test_ui_hook.py firmware_ext/tests/test_gen.py; do
    run python3 "$t"
done
if command -v uv >/dev/null; then run uv run --quiet --with pyvisa --with pyvisa-py python host/tests/test_pyvisa.py; fi
if [ -n "$PKGS" ]; then run bash sigrok/tests/run.sh; else echo "sigrok/tests/run.sh skipped (PKGS not set)"; fi
exit $fail
