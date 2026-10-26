# Records the mainboard's firmware version for switch_klipper.sh and the includes.
#
# FLSun's Klipper only talks to FLSun's mainboard firmware (v0.10.0-439).
# Once the mainboard is flashed with newer firmware, switching the klipper
# service back to FLSun's Klipper would leave a Klipper that can't connect.
# Each time upstream Klipper identifies the mainboard, this writes the version
# it reports to a small file; switch_klipper.sh and FLSUN_Updater's restore
# read it and keep upstream Klipper when the firmware isn't FLSun's.
#
# The file also decides whether flsun_klipper_includes.cfg includes adxl.cfg,
# which FLSun's firmware can't run. When the firmware changes between FLSun's
# and newer, this rewrites the includes at once and asks for a RESTART. It runs
# at identify, before the mainboard is configured, so it's written even when
# the configure then fails (adxl.cfg still included after flashing back).
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, os, subprocess, sys

DEFAULT_PATH = os.environ.get('FLSUN_KLIPPER_MCU_VERSION_FILE',
                              '~/printer_data/flsun_klipper_mcu_version')
STOCK_MCU_VERSION = 'v0.10.0-439-gdfa5afdf'
CFG_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
    'scripts', 'flsun_klipper_cfg.py')


class T1McuVersion:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.path = os.path.expanduser(config.get('path', DEFAULT_PATH))
        self.printer.register_event_handler('klippy:mcu_identify',
                                            self._identify)

    def _identify(self):
        version = self.printer.lookup_object('mcu').get_status()['mcu_version']
        try:
            with open(self.path) as f:
                old = f.read().strip()
        except OSError:
            old = ''
        if old == version:
            return
        tmp = self.path + '.tmp'
        try:
            with open(tmp, 'w') as f:
                f.write(version + '\n')
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except OSError as e:
            logging.warning("t1_mcu_version: can't write %s: %s", self.path, e)
            return
        # Only a change between FLSun's firmware and newer changes the includes.
        # No file yet counts as FLSun's: that's what the includes assumed.
        if ((old or STOCK_MCU_VERSION) == STOCK_MCU_VERSION) \
                == (version == STOCK_MCU_VERSION):
            return
        try:
            subprocess.run([sys.executable, CFG_SCRIPT, 'includes'],
                           check=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as e:
            logging.warning("t1_mcu_version: %s includes failed: %s",
                            CFG_SCRIPT, e)
            return
        what = ("adds the ADXL345 (adxl.cfg)" if version != STOCK_MCU_VERSION
                else "removes the ADXL345 (adxl.cfg)")
        self.printer.lookup_object('configfile').runtime_warning(
            "The mainboard firmware changed to %s, which %s. Run RESTART to "
            "load the updated config." % (version, what))


def load_config(config):
    return T1McuVersion(config)
