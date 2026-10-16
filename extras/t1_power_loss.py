# Power loss recovery for upstream Klipper on the FLSun T1 Pro.
#
# FLSun's Klipper saved the print's state at every layer change and offered
# to continue after the power came back. This does the same:
#
#   - At each layer change (the slicer's SET_PRINT_STATS_INFO CURRENT_LAYER=)
#     while printing, the file, the position in it, the toolhead position and
#     the temperatures, fan, offsets and mesh are saved to a small JSON file.
#     It's written to a temporary file, flushed and renamed, so a power cut
#     leaves either the old checkpoint or the new one, never a broken file.
#   - When a print completes or is cancelled, the checkpoint is removed.
#   - After a restart, printer.t1_power_loss.interrupted is True until the
#     print is resumed (RESUME_INTERRUPTED, in power_loss.cfg) or discarded
#     (PLR_DISCARD).
#
# The interrupted layer is printed again from its start.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import json, logging, os, threading

DEFAULT_PATH = '~/printer_data/flsun_klipper_power_loss.json'


class T1PowerLoss:
    def __init__(self, config):
        self.printer = printer = config.get_printer()
        self.reactor = printer.get_reactor()
        self.path = os.path.expanduser(config.get('path', DEFAULT_PATH))
        self.state = self._load()
        # True only for a checkpoint left from before this start of Klipper.
        self.interrupted = bool(self.state)
        self.lock = threading.Lock()
        self.watch_timer = self.reactor.register_timer(self._watch)
        self.watching = False
        # SET_PRINT_STATS_INFO belongs to print_stats; load it first so the
        # command exists to wrap.
        printer.load_object(config, 'print_stats')
        gcode = self.gcode = printer.lookup_object('gcode')
        # Checkpoint at each layer change: wrap the built-in command. A
        # gcode_macro that renames it (pause at layer does) still ends here.
        self.base_set_info = gcode.register_command('SET_PRINT_STATS_INFO', None)
        gcode.register_command('SET_PRINT_STATS_INFO', self.cmd_SET_PRINT_STATS_INFO,
                               desc=self.cmd_SET_PRINT_STATS_INFO_help)
        gcode.register_command('PLR_DISCARD', self.cmd_PLR_DISCARD,
                               desc=self.cmd_PLR_DISCARD_help)
        gcode.register_command('PLR_CONTINUE_FILE', self.cmd_PLR_CONTINUE_FILE,
                               desc=self.cmd_PLR_CONTINUE_FILE_help)
        printer.register_event_handler('klippy:ready', self._handle_ready)

    def _handle_ready(self):
        self.vsd = self.printer.lookup_object('virtual_sdcard')
        self.print_stats = self.printer.lookup_object('print_stats')
        self.gcode_move = self.printer.lookup_object('gcode_move')
        self.toolhead = self.printer.lookup_object('toolhead')

    # --- the checkpoint file --------------------------------------------------
    def _load(self):
        try:
            with open(self.path) as f:
                state = json.load(f)
            return state if isinstance(state, dict) and state else None
        except FileNotFoundError:
            return None
        except Exception:
            logging.exception("t1_power_loss: ignoring unreadable %s", self.path)
            return None

    def _write(self, state):
        def write_out():
            with self.lock:
                try:
                    if state is None:
                        if os.path.exists(self.path):
                            os.remove(self.path)
                    else:
                        tmp = self.path + '.tmp'
                        with open(tmp, 'w') as f:
                            json.dump(state, f)
                            f.flush()
                            os.fsync(f.fileno())
                        os.replace(tmp, self.path)
                    dfd = os.open(os.path.dirname(self.path), os.O_RDONLY)
                    try:
                        os.fsync(dfd)
                    finally:
                        os.close(dfd)
                except Exception:
                    logging.exception("t1_power_loss: can't write %s", self.path)
        # Off the reactor, so a slow eMMC write doesn't stall the print.
        threading.Thread(target=write_out, daemon=True).start()

    # --- saving ---------------------------------------------------------------
    cmd_SET_PRINT_STATS_INFO_help = (
        "Pass layer information to print_stats (and save a power loss checkpoint)")

    def cmd_SET_PRINT_STATS_INFO(self, gcmd):
        self.base_set_info(gcmd)
        if gcmd.get_int('CURRENT_LAYER', None) is not None:
            self._checkpoint()

    def _checkpoint(self):
        eventtime = self.reactor.monotonic()
        stats = self.print_stats.get_status(eventtime)
        if stats['state'] != 'printing':
            return
        vsd = self.vsd.get_status(eventtime)
        if not vsd.get('file_path'):
            return
        move = self.gcode_move.get_status(eventtime)
        pos = move['gcode_position']
        heaters = {}
        for name in ('extruder', 'heater_bed'):
            obj = self.printer.lookup_object(name, None)
            heaters[name] = obj.get_status(eventtime)['target'] if obj else 0.
        extruder = self.printer.lookup_object('extruder', None)
        fan = self.printer.lookup_object('fan', None)
        mesh = self.printer.lookup_object('bed_mesh', None)
        tmc = self.printer.lookup_object('tmc5160 extruder', None)
        info = stats.get('info') or {}
        state = {
            'file_path': vsd['file_path'],
            # The position of this SET_PRINT_STATS_INFO line: the start of
            # the layer.
            'file_position': vsd['file_position'],
            'x': pos.x, 'y': pos.y, 'z': pos.z, 'e': pos.e,
            'absolute_coordinates': move['absolute_coordinates'],
            'absolute_extrude': move['absolute_extrude'],
            'speed_factor': move['speed_factor'],
            'extrude_factor': move['extrude_factor'],
            'z_offset': move['homing_origin'].z,
            'extruder_target': heaters['extruder'],
            'bed_target': heaters['heater_bed'],
            'fan_speed': fan.get_status(eventtime)['speed'] if fan else 0.,
            'pressure_advance': (extruder.get_status(eventtime)
                                 .get('pressure_advance', 0.) if extruder else 0.),
            'extruder_current': (tmc.get_status(eventtime).get('run_current')
                                 if tmc else None),
            'mesh': (mesh.get_status(eventtime)['profile_name'] if mesh else ''),
            'current_layer': info.get('current_layer'),
            'total_layer': info.get('total_layer'),
            'print_duration': stats['print_duration'],
        }
        self.state = state
        self.interrupted = False
        self._write(state)
        if not self.watching:
            self.watching = True
            self.reactor.update_timer(self.watch_timer, self.reactor.NOW)

    def _watch(self, eventtime):
        """Remove the checkpoint when the print ends normally."""
        state = self.print_stats.get_status(eventtime)['state']
        if state in ('complete', 'cancelled'):
            logging.info("t1_power_loss: print %s, checkpoint removed", state)
            self._clear()
        if state in ('printing', 'paused'):
            return eventtime + 2.
        self.watching = False
        return self.reactor.NEVER

    def _clear(self):
        self.state = None
        self.interrupted = False
        self._write(None)

    # --- resuming -------------------------------------------------------------
    cmd_PLR_DISCARD_help = "Forget the interrupted print"

    def cmd_PLR_DISCARD(self, gcmd):
        self._clear()
        gcmd.respond_info("Interrupted print discarded")

    cmd_PLR_CONTINUE_FILE_help = (
        "Continue the interrupted print's file from its checkpoint "
        "(used by RESUME_INTERRUPTED)")

    def cmd_PLR_CONTINUE_FILE(self, gcmd):
        state = self.state
        if not state:
            raise gcmd.error("There is no interrupted print to resume")
        path = state['file_path']
        root = os.path.realpath(os.path.expanduser(self.vsd.sdcard_dirname))
        if not os.path.realpath(path).startswith(root + os.sep):
            raise gcmd.error("%s isn't in the G-code folder" % (path,))
        if not os.path.exists(path):
            raise gcmd.error("%s no longer exists" % (path,))
        rel = os.path.relpath(path, root)
        gcmd.respond_info("Continuing %s from byte %d (layer %s)" % (
            rel, state['file_position'], state.get('current_layer')))
        # The checkpoint stays until the resumed print completes, so a second
        # power cut can be recovered too.
        self.interrupted = False
        self.gcode.run_script_from_command(
            "M23 %s\nM26 S%d\nM24" % (rel, state['file_position']))
        # Opening the file reset print_stats' layer info, and upstream ignores
        # CURRENT_LAYER until TOTAL_LAYER is set, so restore both now.
        if state.get('total_layer'):
            self.gcode.run_script_from_command(
                "SET_PRINT_STATS_INFO TOTAL_LAYER=%d CURRENT_LAYER=%d" % (
                    state['total_layer'], state.get('current_layer') or 0))

    def get_status(self, eventtime):
        return {'interrupted': self.interrupted and bool(self.state),
                'checkpoint': dict(self.state or {})}


def load_config(config):
    return T1PowerLoss(config)
