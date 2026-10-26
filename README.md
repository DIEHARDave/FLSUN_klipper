# FLSUN_klipper

Runs current upstream [Klipper](https://github.com/Klipper3d/klipper) on the **FLSun T1 Pro**, in place of FLSun's 2022 fork, and updates it from Mainsail's or Fluidd's update manager like any other Klipper.

The mainboard isn't flashed: upstream Klipper talks to FLSun's mainboard firmware as it is. FLSun's Klipper, its `printer.cfg` and its service stay on the printer untouched, and one command switches between the two.

## What it does

1. **Installs upstream Klipper beside FLSun's.** It goes into `~/klipper-upstream`, a git checkout of Klipper's `master`, with its own Python environment in `~/klippy-env-upstream`. FLSun's `~/klipper` and `~/klippy-env` aren't touched.
2. **Adds a config for upstream Klipper.** `printer_upstream.cfg` is made from your `printer.cfg`: your `[probe]` section and your calibration (delta calibration, bed meshes, PID, input shaper), plus the heater PID and delta calibration from `printer.cfg`'s main sections if they aren't in that block. If `printer_upstream.cfg` ever lacks a SAVE_CONFIG block, the one in `printer.cfg` is copied in when Klipper starts. The hardware and FLSun's macros come from this repo (`flsun_klipper/t1_pro.cfg`), so fixes arrive with updates.
3. **Points the klipper service at upstream Klipper** with a systemd drop-in. FLSun's `klipper.service` and `klipper.env` aren't edited. If upstream Klipper doesn't start, the installer switches back to FLSun's Klipper and stops with the error.

Plugins add their `[include]` lines to `printer.cfg`. Each time upstream Klipper starts, those lines are copied into `flsun_klipper_includes.cfg`, so plugins work with both Klippers. The few plugin files that only work with FLSun's Klipper are swapped for upstream versions (FLSUN_Macros' pause at layer).

### What FLSun's Klipper did that this keeps

FLSun changed about 900 lines of Klipper and added a closed-source library. These behaviors are rebuilt as macros and a small module:

| FLSun's Klipper | Here |
|---|---|
| Switched the hotend heater's relay (PD6) on whenever the nozzle got a target | `M104`, `M109`, `SET_HEATER_TEMPERATURE` and `PID_CALIBRATE` do the same. The relay now also switches off if Klipper stops with an error. |
| Paused for a filament runout only if it lasted 1 second | Same 1-second check (`_FLSUN_RUNOUT`) |
| `F106 S0/S1` chamber light | Same command, from `extras/t1_box_light.py` |
| Loaded the `default` bed mesh at startup | Same |
| Power loss recovery: saved the print at each layer change and offered to continue | Same, with a prompt in Mainsail and Fluidd ([below](#power-loss-recovery)) |
| Used the slicer's `ACCEL_TO_DECEL` | Upstream dropped it and ignores it, which made prints 15–30% slower. `SET_VELOCITY_LIMIT` turns it into the equivalent `MINIMUM_CRUISE_RATIO`. |
| Looser heater checks for the extruder | Same values (`[verify_heater extruder]`) |

### What's different

- **No ADXL345 (resonance measurement) on FLSun's mainboard firmware,** which speaks an older protocol. Your input shaper values keep working; to measure again, [flash the mainboard](mcu/README.md) or see [Input shaping](#input-shaping).
- **No size calibration** (`SIZE_ANALYZE`, `M101`, `x_offset`/`y_offset` in `[printer]`). It does nothing unless you ran FLSun's size calibration.
- **No FLSun bed-mesh adjustments.** FLSun's Klipper added a fixed 0.08 mm pattern to every new 7×7 mesh and lowered Z slightly along moves longer than 10 mm. Meshes made with FLSun's Klipper already include the first. If first layers look different from before, this is the place to look.
- **FLSun-only commands are gone:** `F100`, `F102`–`F104`, `F108`, `F109`, `M108`, `M101`, `M205`, `M665`, `PRINTER_WORKMODE`. The stock macros no longer use them.
- The extruder's `min_temp` is 0 °C instead of -200 °C, so a broken thermistor stops the printer.
- With FLSun's mainboard firmware, Klipper reports two warnings at every start: the firmware is missing `spi_set_sw_bus` and `STEPPER_STEP_BOTH_EDGE`. They're harmless; [flashing the mainboard](mcu/README.md) removes them.

## Requirements

- A T1 Pro with FLSun's firmware 1.0.0.6 (FLSun's Klipper reports `1.0.1.5`; the mainboard reports `v0.10.0-439-gdfa5afdf`).
- [FLSUN_moonraker](https://github.com/DIEHARDave/FLSUN_moonraker). Upstream Klipper's Python packages need Python 3.10 or newer and a working compiler, and FLSun's image has neither; FLSUN_moonraker installs both.
- About 300 MB free.

## Install

With [FLSUN_Updater](https://github.com/DIEHARDave/FLSUN_Updater): set `flsun_klipper: yes` in `flsun_plugins.cfg`, or run `ENABLE_PLUGIN NAME=flsun_klipper`.

By hand, with the printer idle, SSH in as `pi` and run:

```sh
cd ~
git clone https://github.com/DIEHARDave/FLSUN_klipper.git
~/FLSUN_klipper/install.sh
```

The install takes about 10 minutes, most of it compiling Klipper's Python packages. Klipper is down only for the last few seconds. It can be run again at any time. Klipper stays at whatever version the update manager last installed; `KLIPPER_REF=<commit, tag or branch>` before the command switches it.

## After installing

- **Check homing and the probe first:** `G28`, then `PROBE_ACCURACY` in the middle of the bed. The probe is a pressure sensor that only triggers while moving, so `QUERY_PROBE` always shows `open`. The very first probe after the printer has been idle can read low; run `PROBE_ACCURACY` until its results are steady before calibrating.
- **Calibration** (`BED_LEVEL_1`, `BED_LEVEL_2`, `PID_EXTRUDER`, `PID_BED`) works as before. `SAVE_CONFIG` writes to `printer_upstream.cfg`, so FLSun's `printer.cfg` keeps its own calibration.
- **Your own changes** go in `printer_upstream.cfg`, below the `[include]` lines. Don't edit `flsun_klipper/*.cfg`; updates replace them.
- **Updates:** Klipper and FLSUN_klipper both show in the update manager. Upstream Klipper still supports FLSun's mainboard firmware, but a future version could drop it; Klipper would then stop with a protocol or "command format mismatch" error. If that happens, switch back to FLSun's Klipper, then go back to the previous version with `git -C ~/klipper-upstream checkout <commit>` and `KLIPPER_REF=<commit> ~/FLSUN_klipper/install.sh`.

## Switching

```sh
sudo ~/FLSUN_klipper/scripts/switch_klipper.sh stock      # FLSun's Klipper with printer.cfg
sudo ~/FLSUN_klipper/scripts/switch_klipper.sh upstream   # upstream Klipper with printer_upstream.cfg
~/FLSUN_klipper/scripts/switch_klipper.sh status
```

Switching restarts Klipper and refuses to run during a print.

## Power loss recovery

Like FLSun's, it saves where the print is at each layer change: the file and the position in it, the toolhead position, the temperatures, fan, Z offset, bed mesh and pressure advance. The checkpoint is written to a new file that then replaces the old one, so a power cut never leaves a broken checkpoint. When the print completes or is cancelled, it's removed.

After the power comes back, Mainsail and Fluidd ask whether to continue the print. Check that the part is still stuck to the bed first. **Resume** (or `RESUME_INTERRUPTED`) warms the bed and the nozzle and homes; on a delta that moves the nozzle up, away from the part. Then it heats to the print's temperatures, restores the offset, mesh and fan, moves over the part and carries on. **Discard** (or `PLR_DISCARD`) forgets the print.

- The layer that was printing when the power went is printed again from its start, so there may be a mark at that height.
- The slicer has to report layers. In Orca, the printer's **Layer change G-code** needs `SET_PRINT_STATS_INFO CURRENT_LAYER={layer_num + 1}`, and the **Machine start G-code** `SET_PRINT_STATS_INFO TOTAL_LAYER=[total_layer_count]`. Without them nothing is saved.
- If the bed cooled down long enough for the part to come loose, resuming won't help.
- Moonraker's history shows the resumed part as a new job.

## Input shaping

**With the mainboard flashed** (see [The mainboard](#the-mainboard-optional)), upstream Klipper uses the ADXL345 itself: `MEASURING_RESONANCES` homes, runs `SHAPER_CALIBRATE` and saves the result to `printer_upstream.cfg`, as on FLSun's Klipper. `config/adxl.cfg` holds the ADXL345 and `[resonance_tester]` settings from FLSun's `printer.cfg`. It's only included while the mainboard runs newer firmware than FLSun's, since FLSun's firmware can't run it. When the firmware changes either way, Klipper rewrites the includes and asks for a `RESTART`. `SHAPER_CALIBRATE` needs numpy, and none on PyPI or piwheels runs on FLSun's Debian 10, so `wheels/` has one built on a T1 Pro for FLSUN_moonraker's Python 3.13 (`wheels/build_numpy.sh` shows how). `install.sh` installs it, and so does each start of Klipper while the mainboard is flashed and numpy is missing. After flashing FLSun's firmware back, the first start can stop with "Command format mismatch: query_adxl345"; `RESTART` once.

**With FLSun's mainboard firmware**, FLSun's Klipper can still use the ADXL345:

1. `sudo ~/FLSUN_klipper/scripts/switch_klipper.sh stock`
2. Run FLSun's `MEASURING_RESONANCES` (or `SHAPER_CALIBRATE`). The results are saved to `printer.cfg`.
3. `sudo ~/FLSUN_klipper/scripts/switch_klipper.sh upstream`, then copy the `shaper_type_x`, `shaper_freq_x`, `shaper_type_y` and `shaper_freq_y` values from the end of `printer.cfg` into the `[input_shaper]` section at the end of `printer_upstream.cfg`, and restart Klipper.

## The mainboard (optional)

FLSUN_klipper works with FLSun's mainboard firmware as it is. Flashing the mainboard with current Klipper firmware removes the two firmware warnings and makes the ADXL345 work (`MEASURING_RESONANCES`). **[mcu/README.md](mcu/README.md)** has the tested firmware image, a guided script that backs up the mainboard, puts a rollback image on its card and flashes one confirmed step at a time, and the ways back. It was all done on a real T1 Pro.

```sh
sudo python3 ~/FLSUN_klipper/mcu/mcu_update.py
```

## Testing changes

`test/check_config.sh` loads the config with upstream Klipper on any Linux PC (WSL works), against the command dictionary of FLSun's mainboard firmware, and runs `test/commands.gcode`. It catches config errors and commands the stock mainboard firmware doesn't have, without a printer:

```sh
test/check_config.sh ~/klipper ~/klippy-env/bin/python
```

## Uninstall

With FLSUN_Updater: `DISABLE_PLUGIN NAME=flsun_klipper`. By hand:

```sh
~/FLSUN_klipper/uninstall.sh
rm -rf ~/FLSUN_klipper
```

This switches back to FLSun's Klipper and removes `~/klipper-upstream` and `~/klippy-env-upstream`. `printer_upstream.cfg` is kept, with the calibration made under upstream Klipper, and a later install uses it again.

## Credits

- [Klipper](https://github.com/Klipper3d/klipper)
- [BehaviorBean/flsun-t1pro-debian12-klipper](https://github.com/BehaviorBean/flsun-t1pro-debian12-klipper): showed that upstream Klipper works with FLSun's mainboard firmware, and the hardware notes (hotend relay, probe, chamber light)
- [mulcmu/T1-pyro](https://github.com/mulcmu/T1-pyro): mainboard and bootloader research

## License

GPL-3.0, like Klipper. See [LICENSE](LICENSE). The config is derived from FLSun's stock `printer.cfg`.
