# Records the mainboard's firmware version for switch_klipper.sh.
#
# FLSun's Klipper only talks to FLSun's mainboard firmware (v0.10.0-439).
# Once the mainboard is flashed with newer firmware, switching the klipper
# service back to FLSun's Klipper would leave a Klipper that can't connect.
# Each time upstream Klipper connects, this writes the version the mainboard
# reports to a small file; switch_klipper.sh and FLSUN_Updater's restore read
# it and keep upstream Klipper when the firmware isn't FLSun's.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, os

DEFAULT_PATH = '~/printer_data/flsun_klipper_mcu_version'


class T1McuVersion:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.path = os.path.expanduser(config.get('path', DEFAULT_PATH))
        self.printer.register_event_handler('klippy:connect', self._connect)

    def _connect(self):
        version = self.printer.lookup_object('mcu').get_status()['mcu_version']
        try:
            with open(self.path) as f:
                if f.read().strip() == version:
                    return
        except OSError:
            pass
        tmp = self.path + '.tmp'
        try:
            with open(tmp, 'w') as f:
                f.write(version + '\n')
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except OSError as e:
            logging.warning("t1_mcu_version: can't write %s: %s", self.path, e)


def load_config(config):
    return T1McuVersion(config)
