# Flashing the T1 Pro mainboard with current Klipper

FLSun ships the T1 Pro's mainboard with Klipper firmware from 2022 (`v0.10.0-439-gdfa5afdf`). Upstream Klipper (FLSUN_klipper) still runs with it, but:

- every start shows two warnings: the firmware is "missing feature `spi_set_sw_bus`" and "`STEPPER_STEP_BOTH_EDGE`";
- the ADXL345 can't be used, so `MEASURING_RESONANCES` / `SHAPER_CALIBRATE` don't work.

This folder has everything needed to flash the mainboard with current Klipper firmware from the printer itself, over SSH. It doesn't involve opening the printer, an ST-Link or a PC toolchain. A guided script backs up the mainboard first and puts a rollback image on the mainboard's card, then flashes one step at a time and asks before each step.

## Tested on real hardware

All of this was done on a real FLSun T1 Pro (FLSun firmware 1.0.0.6, mainboard firmware `v0.10.0-439-gdfa5afdf`), not just in theory:

- **Backup:** the whole 512 KB flash (bootloader and firmware) was read through Klipper and checked against checksums.
- **Card access:** the mainboard's microSD card (a 64 GB FAT32 card) was read and written through the mainboard, with read-back checks.
- **Flash:** this exact image (`firmware/klipper.bin`, sha256 `8724815976c14f36…`) was written to the card. After the power cycle the bootloader had renamed it to `ROBIN_NANO35.CUR`, and the mainboard reported `v0.13.0-816-gee2c6fb6b`.
- **After the flash:** Klipper came up ready with no firmware warnings, and the ADXL345 answered `ACCELEROMETER_QUERY`.
- **Input shaper:** `MEASURING_RESONANCES` ran and saved the result, EI 40.6 Hz for X and MZV 34.6 Hz for Y.

## What you need

- A T1 Pro running upstream Klipper through [FLSUN_klipper](../README.md), which needs [FLSUN_moonraker](https://github.com/DIEHARDave/FLSUN_moonraker). `sudo ~/FLSUN_klipper/scripts/switch_klipper.sh status` should say `Set up: upstream Klipper`.
- A **FAT32 microSD card in the mainboard's own slot**. That's not the slot on the printer's Linux board: the mainboard can't see that one. The card used here is 64 GB. MKS bootloaders can be picky about large cards, so if the flash isn't picked up, try one of 32 GB or less.
- SSH access as `root`, or as `pi` with `sudo`, and access to the printer's power switch.
- About 30 minutes. The printer can't print during the update.

## Updating

```sh
sudo python3 ~/FLSUN_klipper/mcu/mcu_update.py
```

The script does one step and asks before the next. It remembers where it is: after the power cycle, or after any interruption, run the same command again and it carries on. `... mcu_update.py status` shows where it's at.

| Step | What happens | Klipper stopped |
|---|---|---|
| 1. Checks | Checks the bundled image's checksums, the tools, that Klipper is ready and idle, and the firmware the mainboard runs now. Then it reads the card's file list. A firmware file already on the card, which the bootloader would flash at the next power-on, is removed if you agree. | about 30 s |
| 2. Backup | Reads the whole 512 KB flash, the option bytes and the chip ID through Klipper. It saves them to `~/printer_data/config/mcu_backup/<date>/` with checksums, plus the firmware cut out of it and the bootloader. It compares both with the tested T1 Pro's. **Download that folder** (Mainsail or Fluidd, config files): it's the only copy of your mainboard's bootloader. | about 12 min |
| 3. Rollback image | Writes `ROLLBACK.BIN` (your current firmware, ready for the bootloader), `MCUFULL.BIN` (the full backup) and `ROLLBACK.TXT` to the card. Each file is read back and checked. None of them is a name the bootloader flashes. | about 1 min |
| 4. Flash | Writes the new firmware to the card as `Robin_nano35.bin`. Nothing changes yet. | about 1 min |
| 5. Power cycle | **You switch the printer off with its switch, wait 10 seconds and switch it on.** The bootloader only flashes at power-on; a reboot or Klipper restart isn't enough. The bootloader flashes the file and renames it `ROBIN_NANO35.CUR`. After about 2 minutes, run the script again. It checks the version the mainboard reports. | |
| 6. Checks | Restarts Klipper once, so that `flsun_klipper/adxl.cfg` gets included. Then it checks: the firmware version, that the two warnings are gone, that the version is recorded, that `adxl.cfg` is included, that numpy is installed, and that `ACCELEROMETER_QUERY` gets an answer. | |
| 7. `PROBE_ACCURACY` | Runs `G28` and `PROBE_ACCURACY` and shows the results. Clear the bed and clean the nozzle first. The first run after the printer has been idle can read low, so run it again until it's steady (answer `n` to repeat). | |
| 8. `MEASURING_RESONANCES` | Homes, measures with the ADXL345, saves the input shaper to `printer_upstream.cfg` and restarts Klipper. Shows the fitted shapers and the recommendation. | |

## Rolling back

Any of these puts back the firmware the mainboard had before (FLSun's, normally):

1. **Over SSH:**

   ```sh
   sudo python3 ~/FLSUN_klipper/mcu/mcu_update.py rollback
   ```

   It writes the firmware from your backup to the card. If there's no backup, it writes FLSun's stock image from `stock/`. Then it asks you to power-cycle. Run the same command again afterwards to check. The first start on the old firmware can stop with "Command format mismatch: query_adxl345" while Klipper drops `adxl.cfg`; the script restarts Klipper once to clear that.
2. **By hand, if the printer won't talk to the mainboard:** take the card out of the mainboard. On a computer, rename `ROLLBACK.BIN` to `Robin_nano35.bin`, put the card back and switch the printer on. `stock/stock_klipper_app.bin` is FLSun's firmware unconverted; `ROLLBACK.BIN` on the card is already converted.
3. **ST-Link, last resort:** nothing here writes the bootloader, but if it were ever damaged, `MCUFULL.BIN` / `mcu_flash_full_512k.bin` is the whole chip. The STM32 is read-protected (RDP level 1), so removing the protection erases it first. Then write the backup at `0x08000000`.

After rolling back, `switch_klipper.sh stock` works again; it refuses while the mainboard runs newer firmware.

## What's in this folder

| File | |
|---|---|
| `mcu_update.py` | The guided update and rollback. Python 3.7, standard library only. |
| `firmware/klipper.bin` | Klipper `v0.13.0-816-gee2c6fb6b` for the T1 Pro mainboard, plain, **not** converted for the bootloader. |
| `firmware/klipper.dict` | Its command dictionary. |
| `firmware/klipper.config` | The `make menuconfig` settings it was built with. |
| `stock/stock_klipper_app.bin` | FLSun's mainboard firmware (`v0.10.0-439-gdfa5afdf`, plain), read from a T1 Pro: the fallback rollback image. It's unmodified upstream Klipper v0.10, GPLv3; FLSun didn't change Klipper's `src/`. |
| `*/SHA256SUMS` | Checksums; the script checks them before using anything. |

The tools it uses, in `../tools/`:

- `mcu_read_flash.py`: reads the mainboard's flash through Klipper's `debug_read`.
- `t1_sd_card.py`: lists, reads and writes files on the mainboard's card, without converting them, with read-back checks. It refuses the `Robin_nano*.bin` names.
- `t1_spi_flash.py`: upstream Klipper's `spi_flash` with the T1 Pro's board definition; this is what writes firmware.

`../wheels/` has numpy for `SHAPER_CALIBRATE`. FLSUN_klipper installs it automatically.

## Building the firmware yourself

```sh
git clone https://github.com/Klipper3d/klipper && cd klipper
git checkout ee2c6fb6b                          # or a newer Klipper
cp ~/FLSUN_klipper/mcu/firmware/klipper.config .config
make olddefconfig && make
```

The settings: STM32F103 (`stm32f103xe`), 28 KiB bootloader (application at `0x08007000`), 8 MHz crystal, serial on USART3 (PB11/PB10), 250000 baud. Use `out/klipper.bin` as it is. `t1_spi_flash.py` converts it for the MKS bootloader (`scripts/update_mks_robin.py`) by itself, so an image converted beforehand would be converted twice and wouldn't run. To flash your own build with the script, replace `firmware/klipper.bin` and `klipper.dict` and update `firmware/SHA256SUMS`.

## Details worth knowing

- **The mainboard:** STM32F103VE with 512 KB flash, a CH340 USB-serial chip, and FLSun's MKS Robin Nano 28 KiB bootloader. At power-on the bootloader looks on its microSD card for `Robin_nano35.bin` (it also knows `Robin_nano.bin`), flashes it and renames it to `.CUR`. The card sits on the STM32's SDIO pins; Klipper drives them as software SPI (`PC8, PD2, PC12`, CS `PC11`).
- **Only a power-on flashes.** `spi_flash` resets the mainboard and prints "This board requires a manual reboot". It means switching the power off and on.
- **`t1_spi_flash.py -c` always reports success.** It only shows that the card can be read.
- **FLSun's Klipper can't talk to new firmware.** FLSUN_klipper records the version the mainboard reports (`~/printer_data/flsun_klipper_mcu_version`). While it isn't FLSun's, `switch_klipper.sh stock`, `uninstall.sh` and FLSUN_Updater's `[restore]` keep upstream Klipper running. That way the printer doesn't end up on a Klipper that can't connect.
- **The ADXL345 config follows the firmware.** `adxl.cfg` (FLSun's pins `PE11`–`PE14` and its `[resonance_tester]`) is only included while the mainboard runs newer firmware. When the firmware changes either way, Klipper rewrites the includes and asks for one restart.
