# Upstream Klipper's spi_flash.py with a board definition for the FLSun T1 Pro,
# to write firmware to the microSD card in the mainboard's slot (not the slot
# on the Linux board). The mainboard's MKS Robin Nano bootloader flashes
# Robin_nano35.bin from that card at the next power-on (a reset isn't enough).
#
# Run from ~/klipper-upstream with Klipper stopped:
#   ~/klippy-env-upstream/bin/python ~/FLSUN_klipper/tools/t1_spi_flash.py [-c] \
#       /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 flsun-t1-pro <klipper.bin>
# Give it the plain klipper.bin: it converts the image for the bootloader
# itself (scripts/update_mks_robin.py). -c only shows that the card can be
# read (it always reports success); it writes nothing.
#
# The card is on the STM32F103's SDIO pins, driven as software SPI. Flashing
# through it was done on a T1 Pro (mcu/README.md).
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, sys
KLIPPER_DIR = os.environ.get('KLIPPER_DIR', '/home/pi/klipper-upstream')
sys.path.insert(0, os.path.join(KLIPPER_DIR, 'scripts', 'spi_flash'))
import board_defs, spi_flash

DEVICE = '/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0'
BOARD = {
    'mcu': "stm32f103xe",
    'spi_bus': "swspi",
    'spi_pins': "PC8,PD2,PC12",
    'cs_pin': "PC11",
    'skip_verify': True,
    'conversion_script': "scripts/update_mks_robin.py",
    # The name mulcmu/T1-pyro flashes this bootloader with; it also knows
    # Robin_nano.bin.
    'firmware_path': os.environ.get('T1_FW_NAME', 'Robin_nano35.bin'),
    'current_firmware_path': os.environ.get('T1_CUR_NAME', 'ROBIN_NANO35.CUR'),
}
board_defs.BOARD_DEFS['flsun-t1-pro'] = BOARD

if __name__ == '__main__':
    spi_flash.main()
