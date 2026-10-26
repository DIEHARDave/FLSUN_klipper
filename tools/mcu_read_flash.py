# Reads the T1 Pro mainboard's flash through Klipper's debug_read command.
#
# FLSun's STM32F103 is read-protected, so an ST-Link can't read it; Klipper,
# running on the chip, can. Klipper must be stopped (backup_mcu.sh does that).
#
#   python mcu_read_flash.py <klippy dir> <out file> <start> <length>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, sys, struct, time

# A stopped Klipper leaves the MCU configured, so it keeps sending sensor
# reports; upstream's serialhdl logs each unhandled one as a warning.
logging.basicConfig(level=logging.ERROR)

klippy_dir, out = sys.argv[1], sys.argv[2]
start, length = int(sys.argv[3], 0), int(sys.argv[4], 0)
sys.path.insert(0, klippy_dir)
import reactor, serialhdl

PORT = '/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0'

r = reactor.Reactor()
ser = serialhdl.SerialReader(r)
result = []


def run(eventtime):
    t0 = time.time()
    try:
        ser.connect_uart(PORT, 250000)
        with open(out, 'wb') as f:
            for addr in range(start, start + length, 4):
                p = ser.send_with_response(
                    'debug_read order=2 addr=%d' % addr, 'debug_result')
                f.write(struct.pack('<I', p['val'] & 0xffffffff))
        print('Read %d bytes in %.0f s' % (length, time.time() - t0))
        result.append(True)
    except Exception as e:
        print('ERROR: %r' % (e,))
    finally:
        ser.disconnect()
        r.end()


r.register_callback(run)
r.run()
sys.exit(0 if result else 1)
