#!/bin/bash
# Back up the T1 Pro mainboard's whole flash (bootloader and firmware, 512 KB),
# plus its option bytes, before flashing anything to the mainboard.
#
#   sudo ~/FLSUN_klipper/tools/backup_mcu.sh [out dir]   (default ~/mcu_backup)
#
# Stops Klipper for about 12 minutes and starts it again. Copy the files to
# your computer afterwards: FLSun's STM32 is read-protected, so this is the
# only way to get its bootloader, and nothing else can restore it.
set -eu
TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-/home/pi/mcu_backup}"
STAMP="$(date +%Y%m%d-%H%M%S)"

[ "$(id -u)" = 0 ] || { echo "Run with sudo."; exit 1; }
case "$(curl -s -m 5 'http://127.0.0.1:7125/printer/objects/query?print_stats=state')" in
    *'"printing"'*|*'"paused"'*) echo "A print is running; try again when it has finished."; exit 1 ;;
esac

# FLSun's Klipper's serial code does the reading (it's always installed, and
# this was tested with it).
KLIPPY=/home/pi/klipper/klippy; PY=/home/pi/klippy-env/bin/python

mkdir -p "$OUT"
echo "Stopping Klipper (about 12 minutes)"
systemctl stop klipper
trap 'systemctl start klipper' EXIT
sleep 2
"$PY" "$TOOLS/mcu_read_flash.py" "$KLIPPY" "$OUT/t1_mcu_flash_512k_$STAMP.bin" 0x08000000 0x80000
"$PY" "$TOOLS/mcu_read_flash.py" "$KLIPPY" "$OUT/t1_mcu_optionbytes_$STAMP.bin" 0x1FFFF800 0x10
"$PY" "$TOOLS/mcu_read_flash.py" "$KLIPPY" "$OUT/t1_mcu_id_$STAMP.bin" 0x1FFFF7E0 0x14
chown -R pi:pi "$OUT"
cd "$OUT" && sha256sum ./*"$STAMP".bin | tee "SHA256SUMS_$STAMP"
echo "Saved in $OUT. Copy these files to your computer."
