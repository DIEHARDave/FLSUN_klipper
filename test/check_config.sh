#!/bin/bash
# Offline check of FLSUN_klipper's config with upstream Klipper, on any Linux PC
# (WSL works), without a printer. Klipper runs in batch mode against the stock
# T1 Pro mainboard's command dictionary (read from its flash), so a config that
# needs commands the stock mainboard firmware lacks fails here.
#
#   test/check_config.sh <upstream klipper checkout> <python with Klipper's packages> [<dict>]
#
# With a third argument, it checks against that dictionary instead, for example
# out/klipper.dict of a firmware build for a flashed mainboard (adxl.cfg included).
#
# It builds printer_upstream.cfg from FLSun's stock printer.cfg (test/printer.cfg)
# the way install.sh does, then runs test/commands.gcode.
set -eu
TEST="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$TEST")"
KLIPPER="$(cd "$1" && pwd)"; PY="$2"
DICT="${3:-$TEST/stock_mcu_v0.10.0-439-gdfa5afdf.dict}"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
# The mainboard version extras/t1_mcu_version.py would have recorded.
export FLSUN_KLIPPER_MCU_VERSION_FILE="$WORK/flsun_klipper_mcu_version"
export FLSUN_KLIPPER_STOCK_SHAPER_FILE="$WORK/flsun_klipper_stock_shaper"
python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' "$DICT" > "$FLSUN_KLIPPER_MCU_VERSION_FILE"

cp "$TEST/printer.cfg" "$WORK/printer.cfg"
: > "$WORK/timelapse.cfg"                 # included by FLSun's printer.cfg
ln -s "$REPO/config" "$WORK/flsun_klipper"
FLSUN_KLIPPER_CONFIG_DIR="$WORK" python3 "$REPO/scripts/flsun_klipper_cfg.py" create
FLSUN_KLIPPER_CONFIG_DIR="$WORK" python3 "$REPO/scripts/flsun_klipper_cfg.py" includes
FLSUN_KLIPPER_KLIPPER_DIR="$KLIPPER" python3 "$REPO/scripts/flsun_klipper_cfg.py" extras

set +e
"$PY" "$KLIPPER/klippy/klippy.py" "$WORK/printer_upstream.cfg" -i "$TEST/commands.gcode" \
    -o "$WORK/out.serial" -v -d "$DICT" \
    -l "$WORK/klippy.log"
rc=$?
set -e
problems=$(grep -E "Unknown command|Error|Traceback" "$WORK/klippy.log" \
    | grep -vE "action_raise_error|device-tree|dmi/id|chamber light" || true)
if [ $rc -ne 0 ] || [ -n "$problems" ]; then
    echo "FAILED (klippy exit $rc)"; echo "$problems"; tail -20 "$WORK/klippy.log"; exit 1
fi
echo "OK: config loads and test/commands.gcode runs against $(cat "$FLSUN_KLIPPER_MCU_VERSION_FILE")"
grep -q adxl.cfg "$WORK/flsun_klipper_includes.cfg" && echo "    (with adxl.cfg)" || true

# A new input shaper calibration on FLSun's Klipper: SAVE_CONFIG rewrites
# printer.cfg's block, then switch_klipper.sh upstream runs "includes" before
# upstream Klipper starts. Upstream Klipper must then run with the new values.
sed -i -e 's/^#\*# shaper_type_x = .*/#*# shaper_type_x = ei/' \
       -e 's/^#\*# shaper_freq_x = .*/#*# shaper_freq_x = 52.6/' "$WORK/printer.cfg"
FLSUN_KLIPPER_CONFIG_DIR="$WORK" python3 "$REPO/scripts/flsun_klipper_cfg.py" includes
printf 'SET_INPUT_SHAPER\n' > "$WORK/shaper.gcode"
set +e
"$PY" "$KLIPPER/klippy/klippy.py" "$WORK/printer_upstream.cfg" -i "$WORK/shaper.gcode" \
    -o "$WORK/out2.serial" -v -d "$DICT" -l "$WORK/klippy2.log" > "$WORK/shaper.out" 2>&1
rc=$?
set -e
if [ $rc -ne 0 ] || ! grep -qE "shaper_type_x:ei .*shaper_freq_x:52\.6" "$WORK/klippy2.log" "$WORK/shaper.out"; then
    echo "FAILED: upstream Klipper didn't pick up the new input shaper (klippy exit $rc)"
    grep -hiE "shaper|error" "$WORK/klippy2.log" "$WORK/shaper.out" | tail -10; exit 1
fi
echo "OK: a new input shaper in printer.cfg reaches upstream Klipper ($(grep -hoE 'shaper_type_x:[^ ]+ shaper_freq_x:[^ ]+' "$WORK/klippy2.log" "$WORK/shaper.out" | tail -1))"
