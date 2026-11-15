#!/bin/sh
# Builds ramflash.bin and shim.bin (Arm GNU toolchain; run in WSL/Linux).
#   sh build.sh [path/to/arm-none-eabi-]
set -e
cd "$(dirname "$0")"
P=${1:-arm-none-eabi-}
"${P}as" -o ramflash.o ramflash.S
"${P}ld" -Ttext=0x20008100 -e ramflash -o ramflash.elf ramflash.o
"${P}objcopy" -O binary ramflash.elf ramflash.bin
"${P}as" -o shim.o shim.S
"${P}ld" -Ttext=0x08000040 --section-start=.vectors=0x08000000 -e reset -o shim.elf shim.o
"${P}objcopy" -O binary shim.elf shim.bin
# Where the operation list sits in ramflash.bin (t1_flash.py checks it).
"${P}nm" ramflash.elf | awk '$3=="ops"{print $1}' > ramflash.ops
rm -f *.o *.elf
sha256sum ramflash.bin shim.bin
