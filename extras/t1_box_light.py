# FLSun T1 Pro chamber light for upstream Klipper.
#
# The light hangs on GPIO108 of the printer's Linux board, not on the mainboard.
# FLSun's box_light.py switched it with the command F106 S0/S1 by writing the
# sysfs GPIO that FLSun's system exports at boot; this does the same.
#
# This file may be distributed under the terms of the GNU GPLv3 license.

LIGHT_PATH = '/sys/class/gpio/gpio108/value'


class T1BoxLight:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.path = config.get('path', LIGHT_PATH)
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('F106', self.cmd_F106,
                               desc=self.cmd_F106_help)

    cmd_F106_help = "Turn the chamber light on (S1) or off (S0)"

    def cmd_F106(self, gcmd):
        value = gcmd.get_int('S', minval=0, maxval=1)
        try:
            with open(self.path, 'w') as f:
                f.write(str(value))
        except OSError as e:
            raise gcmd.error("Can't switch the chamber light: %s" % (e,))

    def get_status(self, eventtime):
        try:
            with open(self.path) as f:
                return {'value': int(f.read().strip())}
        except (OSError, ValueError):
            # Polled often; F106 reports the error when it matters.
            return {'value': None}


def load_config(config):
    return T1BoxLight(config)
