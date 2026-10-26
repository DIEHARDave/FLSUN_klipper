# Reads and writes files on the microSD card in the FLSun T1 Pro's mainboard
# slot, through the mainboard (Klipper firmware's SPI commands), with upstream
# Klipper's spi_flash code. Files are copied as they are, not converted; for
# firmware use t1_spi_flash.py.
#
# Run with Klipper stopped:
#   ~/klippy-env-upstream/bin/python ~/FLSUN_klipper/tools/t1_sd_card.py ls
#   ... t1_sd_card.py put <name on card> <local file> [<name> <file> ...]
#   ... t1_sd_card.py get <name on card> <local file>
#   ... t1_sd_card.py rm <name on card>
#
# put reads every file back and compares checksums. It refuses the names the
# bootloader flashes at power-on (Robin_nano*.bin), so nothing written here
# changes the firmware by accident.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import hashlib, logging, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from t1_spi_flash import BOARD, DEVICE, spi_flash

FIRMWARE_NAMES = ('robin_nano.bin', 'robin_nano35.bin')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class CardSession:
    """Connects to the mainboard, mounts the card, runs ops, resets the MCU."""
    def __init__(self, ops):
        self.ops = ops
        self.cfg = dict(BOARD, fast_spi=False)
        self.path = spi_flash.translate_serial_to_tty(DEVICE)[1]
        self.error = None
        self.needed_restart = False

    def run(self):
        for _ in range(2):
            self.needed_restart = False
            self._reactor_task(self._task)
            if self.error:
                raise self.error
            if not self.needed_restart:
                return
            # The MCU was configured (Klipper ran); it resets, then try again.
            time.sleep(3.)
        raise spi_flash.SPIFlashError("The mainboard kept needing a restart")

    def _reactor_task(self, cb):
        k_reactor = spi_flash.reactor.Reactor()
        self.conn = spi_flash.MCUConnection(k_reactor, self.path, 250000,
                                            self.cfg)
        k_reactor.register_callback(cb)
        try:
            k_reactor.run()
        finally:
            self.conn.disconnect()
            k_reactor.finalize()

    def _task(self, eventtime):
        conn = self.conn
        try:
            conn.connect()
            if conn.check_need_restart():
                self.needed_restart = True
                conn.reset()
                return
            conn.configure_mcu(printfunc=lambda msg: None)
            for op in self.ops:
                op(conn.fatfs)
        except Exception as e:
            logging.exception("t1_sd_card")
            self.error = e
        try:
            conn.reset()
        except Exception:
            conn.reactor.end()


def op_ls(fatfs):
    for f in fatfs.list_sd_directory(""):
        name = f['name'].decode('utf-8', 'replace')
        print("%-28s %10s  %s" % (name + ("/" if f['is_dir'] else ""),
                                  "" if f['is_dir'] else f['size'],
                                  f['modified']))


def op_put(name, local):
    def op(fatfs):
        with open(local, 'rb') as f:
            data = f.read()
        with fatfs.open_file(name, 'wb') as sd_f:
            for i in range(0, len(data), 4096):
                sd_f.write(data[i:i + 4096])
        back = read_card(fatfs, name)
        if sha256(back) != sha256(data):
            raise spi_flash.SPIFlashError(
                "%s on the card doesn't match %s" % (name, local))
        print("Wrote %s (%d bytes, sha256 %s), read back OK"
              % (name, len(data), sha256(data)))
    return op


def op_get(name, local):
    def op(fatfs):
        data = read_card(fatfs, name)
        with open(local, 'wb') as f:
            f.write(data)
        print("Read %s (%d bytes, sha256 %s)" % (name, len(data), sha256(data)))
    return op


def op_rm(name):
    def op(fatfs):
        fatfs.remove_item(name)
        print("Removed %s" % (name,))
    return op


def read_card(fatfs, name):
    chunks = []
    with fatfs.open_file(name, 'r') as sd_f:
        while True:
            buf = sd_f.read(4096)
            if not buf:
                break
            chunks.append(buf)
    return b''.join(chunks)


def main():
    args = sys.argv[1:]
    logging.basicConfig(level=logging.CRITICAL)
    if args[:1] == ['ls'] and len(args) == 1:
        ops = [op_ls]
    elif args[:1] == ['put'] and len(args) >= 3 and len(args) % 2 == 1:
        pairs = list(zip(args[1::2], args[2::2]))
        for name, local in pairs:
            if name.lower() in FIRMWARE_NAMES:
                sys.exit("%s is flashed by the bootloader at power-on; use "
                         "t1_spi_flash.py for firmware" % (name,))
            if not os.path.isfile(local):
                sys.exit("No file %s" % (local,))
        ops = [op_put(n, l) for n, l in pairs]
    elif args[:1] == ['get'] and len(args) == 3:
        ops = [op_get(args[1], args[2])]
    elif args[:1] == ['rm'] and len(args) == 2:
        ops = [op_rm(args[1])]
    else:
        sys.exit(__doc__ or "usage: t1_sd_card.py ls | put <name> <file> ... "
                 "| get <name> <file> | rm <name>")
    try:
        CardSession(ops).run()
    except Exception as e:
        sys.exit("ERROR: %s" % (e,))


if __name__ == '__main__':
    main()
