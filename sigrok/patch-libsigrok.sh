#!/bin/sh
# Add the fnirsi-dso-tc3 driver to a libsigrok source tree.
# usage: sigrok/patch-libsigrok.sh <libsigrok-source-dir> [driver-source-dir]
set -e
here=$(cd "$(dirname "$0")" && pwd)
drv=${2:-$here/fnirsi-dso-tc3}
src=${1:?usage: patch-libsigrok.sh <libsigrok-source-dir>}
dst=$src/src/hardware/fnirsi-dso-tc3

rm -rf "$dst"
mkdir -p "$dst"
cp "$drv"/*.c "$drv"/*.h "$dst"/
chmod -R u+w "$dst"

grep -q fnirsi-dso-tc3 "$src/configure.ac" || \
	sed -i '/^SR_DRIVER(\[Fluke 45\]/i SR_DRIVER([FNIRSI DSO-TC3], [fnirsi-dso-tc3], [serial_comm])' "$src/configure.ac"
grep -q fnirsi-dso-tc3 "$src/Makefile.am" || \
	sed -i '/^if HW_FLUKE_45$/i if HW_FNIRSI_DSO_TC3\nsrc_libdrivers_la_SOURCES += \\\n\tsrc/hardware/fnirsi-dso-tc3/protocol.h \\\n\tsrc/hardware/fnirsi-dso-tc3/protocol.c \\\n\tsrc/hardware/fnirsi-dso-tc3/api.c\nendif' "$src/Makefile.am"
grep -q fnirsi-dso-tc3 "$src/configure.ac" "$src/Makefile.am"

# New config key SR_CONF_TRIGGER_MODE (string: auto, normal, single), appended so the other key ids stay as they are.
grep -q SR_CONF_TRIGGER_MODE "$src/include/libsigrok/libsigrok.h" || \
	sed -i '/^\tSR_CONF_GATE_TIME,$/a \\n\t/** Trigger mode (auto, normal, single). */\n\tSR_CONF_TRIGGER_MODE,' "$src/include/libsigrok/libsigrok.h"
grep -q SR_CONF_TRIGGER_MODE "$src/src/hwdriver.c" || \
	sed -i '/"Gate time", NULL},/a \	{SR_CONF_TRIGGER_MODE, SR_T_STRING, "triggermode",\n\t\t"Trigger mode", NULL},' "$src/src/hwdriver.c"
grep -q SR_CONF_TRIGGER_MODE "$src/include/libsigrok/libsigrok.h" "$src/src/hwdriver.c"
