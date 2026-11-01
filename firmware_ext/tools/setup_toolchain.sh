#!/usr/bin/env bash
# Make firmware_ext/tools/toolchain/bin point at a GCC that is pinned against garbage
# collection. firmware_ext/tools/toolchain/ is git-ignored, so run this after a fresh clone
# or if `make` reports a missing arm-none-eabi-gcc.
#
# The compiler is pkgsCross.arm-embedded.buildPackages.gcc from the pinned
# nixpkgs of the RE shell flake (GCC 16.2.0). The newer vendored binary release
# (gcc-arm-embedded-15.x) segfaults on this machine's kernel, so it is not used.
set -euo pipefail
cd "$(dirname "$0")/toolchain"
if [ -x bin/arm-none-eabi-gcc ] && bin/arm-none-eabi-gcc --version >/dev/null 2>&1; then
  echo "toolchain ok: $(readlink -f bin/arm-none-eabi-gcc)"
  exit 0
fi
if [ -n "${RE_FLAKE:-}" ]; then
  EXPR="let flake = builtins.getFlake(\"$RE_FLAKE\"); pkgs = import flake.inputs.nixpkgs { system = \"x86_64-linux\"; }; in pkgs.pkgsCross.arm-embedded.buildPackages.gcc"
else
  EXPR="with import <nixpkgs> {}; pkgsCross.arm-embedded.buildPackages.gcc"
fi
nix build --impure --expr "$EXPR" -o gcc-root
ln -sfn gcc-root/bin bin
echo "toolchain ready: $(readlink -f bin/arm-none-eabi-gcc)"
