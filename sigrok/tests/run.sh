#!/bin/sh
# Build libsigrok and sigrok-cli with the driver through Nix, then run test_driver.py against them.
# usage: sigrok/tests/run.sh
# PKGS selects nixpkgs (a Nix expression, default: import <nixpkgs> {}); e.g.
#   PKGS='(builtins.getFlake "nixpkgs").legacyPackages.x86_64-linux'
# Needs python3 with pyserial (the workspace shell has it) and a C compiler for the shim (cc, else nix-shell -p gcc).
set -e
here=$(cd "$(dirname "$0")" && pwd)
python3 "$here/check_protocol.py"
PKGS=${PKGS:-'import <nixpkgs> {}'}

lib=$(nix-build "$here/.." -A libsigrok --arg pkgs "$PKGS" --no-out-link)
cli=$(nix-build "$here/.." -A sigrok-cli --arg pkgs "$PKGS" --no-out-link)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
if command -v cc >/dev/null 2>&1; then
	cc -shared -fPIC -O2 -o "$tmp/ptyshim.so" "$here/ptyshim.c" -ldl
else
	nix-shell -p gcc --run "cc -shared -fPIC -O2 -o '$tmp/ptyshim.so' '$here/ptyshim.c' -ldl"
fi

PATH=$cli/bin:$PATH TC3_SIGROK_LIB=$lib/lib TC3_PTYSHIM=$tmp/ptyshim.so python3 "$here/test_driver.py"
