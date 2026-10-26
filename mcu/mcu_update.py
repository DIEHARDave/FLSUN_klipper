#!/usr/bin/env python3
"""Guided mainboard (MCU) firmware update for the FLSun T1 Pro.

    sudo python3 ~/FLSUN_klipper/mcu/mcu_update.py             next step
    sudo python3 ~/FLSUN_klipper/mcu/mcu_update.py status      where it's at
    sudo python3 ~/FLSUN_klipper/mcu/mcu_update.py rollback    back to the backup

One step at a time, each after you confirm: checks, backup of the whole
mainboard flash, rollback image onto the mainboard's microSD card, the new
firmware onto the card, power cycle, checks, PROBE_ACCURACY and
MEASURING_RESONANCES. It remembers where it is, so run it again after the
power cycle (or any interruption) and it carries on. See mcu/README.md.

Standard library only, and Python 3.7, so FLSun's system Python can run it.
"""

import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MCU_DIR = os.path.dirname(os.path.realpath(__file__))
REPO_DIR = os.path.dirname(MCU_DIR)
TOOLS = os.path.join(REPO_DIR, "tools")
FIRMWARE_DIR = os.path.join(MCU_DIR, "firmware")
STOCK_DIR = os.path.join(MCU_DIR, "stock")
HOME = "/home/pi"
KLIPPER_DIR = HOME + "/klipper-upstream"
KLIPPY_PY = HOME + "/klippy-env-upstream/bin/python"
DATA = HOME + "/printer_data"
STATE_FILE = DATA + "/flsun_klipper_mcu_update.json"
MCU_VERSION_FILE = DATA + "/flsun_klipper_mcu_version"
INCLUDES_CFG = DATA + "/config/flsun_klipper_includes.cfg"
UPSTREAM_CFG = DATA + "/config/printer_upstream.cfg"
# In the config folder, so Mainsail and Fluidd can download it.
BACKUP_ROOT = DATA + "/config/mcu_backup"
WORK = DATA + "/mcu_update_work"
DROPIN = "/etc/systemd/system/klipper.service.d/flsun_klipper_upstream.conf"
DEVICE = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
MOONRAKER = "http://127.0.0.1:7125"

STOCK_VERSION = "v0.10.0-439-gdfa5afdf"
# FLSun's MKS Robin Nano bootloader (0x08000000-0x08006FFF) on the T1 Pro this
# was developed on; the application starts at 0x08007000.
APP_START = 0x7000
FLASH_SIZE = 0x80000
STOCK_BOOTLOADER_SHA256 = \
    "3142e1847cf4bfa70cdd73af831b047f9f8056b28ac19ea337dccc27379d7f11"

STEPS = ["check", "backup", "card", "flash", "power_cycle", "verify",
         "probe", "resonances", "done"]


# --- Output and questions ------------------------------------------------------------

def say(msg=""):
    print(msg, flush=True)


def heading(n, title):
    say("\n\033[1;36m== Step %d of %d: %s\033[0m" % (n, len(STEPS) - 1, title))


def ok(msg):
    say("  \033[32mOK\033[0m   %s" % msg)


def bad(msg):
    say("  \033[31mFAIL\033[0m %s" % msg)


def note(msg):
    say("  \033[33m!\033[0m    %s" % msg)


def ask(question):
    try:
        answer = input("\n%s [y/N] " % question).strip().lower()
    except EOFError:
        answer = ""
    return answer in ("y", "yes")


def stop(msg):
    say("\n\033[31m%s\033[0m" % msg)
    sys.exit(1)


# --- State ---------------------------------------------------------------------------

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"step": "check"}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, STATE_FILE)
    os.chown(STATE_FILE, *pi_ids())


def pi_ids():
    st = os.stat(HOME)
    return st.st_uid, st.st_gid


def chown_tree(path):
    uid, gid = pi_ids()
    for root, dirs, files in os.walk(path):
        for name in [root] + [os.path.join(root, n) for n in dirs + files]:
            os.chown(name, uid, gid)


# --- Files ---------------------------------------------------------------------------

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def check_sums(folder):
    """Checks a folder's SHA256SUMS; returns the files that don't match."""
    wrong = []
    with open(os.path.join(folder, "SHA256SUMS")) as f:
        for line in f:
            if line.strip():
                want, name = line.split(None, 1)
                path = os.path.join(folder, name.strip().lstrip("*"))
                if not os.path.exists(path) or sha256_file(path) != want:
                    wrong.append(path)
    return wrong


def firmware_version():
    with open(os.path.join(FIRMWARE_DIR, "klipper.dict")) as f:
        return json.load(f)["version"]


# --- Moonraker and Klipper ------------------------------------------------------------

def api(path, data=None, timeout=10):
    req = urllib.request.Request(MOONRAKER + path,
                                 data=b"" if data is not None else None)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)["result"]


def query(objects):
    try:
        return api("/printer/objects/query?" + objects)["status"]
    except Exception:
        return {}


def klipper_state():
    try:
        info = api("/printer/info")
        return info.get("state"), info.get("state_message", "")
    except Exception as e:
        return None, str(e)


def print_state():
    return query("print_stats=state").get("print_stats", {}).get("state")


def mcu_version():
    return query("mcu=mcu_version").get("mcu", {}).get("mcu_version")


def wait_ready(timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        state, _ = klipper_state()
        if state == "ready":
            return True
        time.sleep(2)
    return False


def gcode(script, timeout=60):
    """Runs G-code through Moonraker; returns (ok, error message)."""
    try:
        api("/printer/gcode/script?script=" + urllib.parse.quote(script),
            data=True, timeout=timeout)
        return True, ""
    except urllib.error.HTTPError as e:
        try:
            return False, json.load(e)["error"]["message"]
        except Exception:
            return False, str(e)
    except Exception as e:
        return False, str(e)


def console_since(t0):
    """Klipper's console output since t0 (Moonraker's G-code store)."""
    try:
        store = api("/server/gcode_store?count=1000")["gcode_store"]
    except Exception:
        return []
    return [g["message"] for g in store
            if g["time"] >= t0 and g["type"] == "response"]


def restart_klipper():
    """Klipper's RESTART, through Moonraker so it also works when Klipper
    stopped with an error (as after the firmware changed)."""
    try:
        api("/printer/restart", data=True)
    except Exception as e:
        note("restart request: %s" % e)
    time.sleep(5)
    return wait_ready()


def refuse_if_printing():
    if print_state() in ("printing", "paused"):
        stop("A print is running. Run this again when it has finished.")


class KlipperStopped:
    """Stops Klipper for the with-block and always starts it again."""
    def __enter__(self):
        refuse_if_printing()
        say("  Stopping Klipper")
        subprocess.call(["systemctl", "stop", "klipper"])
        time.sleep(2)

    def __exit__(self, *exc):
        say("  Starting Klipper")
        subprocess.call(["systemctl", "start", "klipper"])
        if not wait_ready():
            note("Klipper isn't ready yet: %s" % (klipper_state(),))
        return False


def run(cmd, cwd=None):
    say("  $ %s" % " ".join(cmd))
    return subprocess.call(cmd, cwd=cwd)


def run_capture(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, universal_newlines=True)
    say("\n".join("  | " + l for l in p.stdout.rstrip().split("\n")))
    return p.returncode, p.stdout


def card(*args):
    return run_capture([KLIPPY_PY, os.path.join(TOOLS, "t1_sd_card.py")]
                       + list(args), cwd=KLIPPER_DIR)


def flash_image(image):
    """Writes a plain (unconverted) Klipper image to the card as
    Robin_nano35.bin. A copy in WORK, because spi_flash writes the converted
    file next to its input."""
    os.makedirs(WORK, exist_ok=True)
    work_bin = os.path.join(WORK, "klipper.bin")
    shutil.copyfile(image, work_bin)
    for old in glob.glob(os.path.join(WORK, "Robin_nano*.bin")):
        os.remove(old)
    rc, out = run_capture([KLIPPY_PY, os.path.join(TOOLS, "t1_spi_flash.py"),
                           DEVICE, "flsun-t1-pro", work_bin], cwd=KLIPPER_DIR)
    return rc == 0 and "Firmware Upload Complete" in out


# --- Steps ---------------------------------------------------------------------------

def step_check(state):
    heading(1, "Checks (nothing is changed)")
    problems = 0
    if os.geteuid() != 0:
        stop("Run it with sudo: sudo python3 %s" % os.path.realpath(__file__))
    for folder in (FIRMWARE_DIR, STOCK_DIR):
        wrong = check_sums(folder)
        if wrong:
            bad("checksum mismatch: %s" % ", ".join(wrong))
            problems += 1
    target = firmware_version()
    ok("firmware image %s, checksums match" % target)
    for path in (KLIPPY_PY, KLIPPER_DIR + "/scripts/update_mks_robin.py",
                 DROPIN, DEVICE):
        if os.path.exists(path):
            ok("found %s" % path)
        else:
            bad("missing %s (is FLSUN_klipper installed and upstream Klipper "
                "switched on?)" % path)
            problems += 1
    state_now, msg = klipper_state()
    if state_now == "ready":
        ok("Klipper is ready")
    else:
        bad("Klipper isn't ready (%s: %s)" % (state_now, msg))
        problems += 1
    if print_state() in ("printing", "paused"):
        bad("a print is running")
        problems += 1
    current = mcu_version()
    say("  Mainboard firmware now: %s" % current)
    if current == target:
        note("The mainboard already runs %s." % target)
        if ask("Skip to the checks after flashing (verify, PROBE_ACCURACY, "
               "MEASURING_RESONANCES)?"):
            state["step"] = "verify"
            return True
        return False
    if current != STOCK_VERSION:
        note("That isn't FLSun's %s. The update still works; the rollback "
             "image will be whatever is on the mainboard now." % STOCK_VERSION)
    if problems:
        stop("Fix the problems above, then run this again.")
    say("\nNext, the microSD card in the mainboard's slot is checked. That "
        "stops Klipper for about 30 seconds.")
    if not ask("Check the card?"):
        return False
    with KlipperStopped():
        rc, out = card("ls")
    if rc != 0:
        stop("The card can't be read. Is a FAT32 microSD card in the "
             "mainboard's slot (not the Linux board's)?")
    ok("the mainboard's card can be read")
    names = [l.split()[1].lower() for l in out.split("\n")
             if l.startswith("  | ") and len(l.split()) > 1]
    pending = [n for n in names if n in ("robin_nano35.bin", "robin_nano.bin")]
    if pending:
        note("The card holds %s, which the bootloader would flash at the "
             "next power-on." % ", ".join(pending))
        if not ask("Remove it?"):
            stop("Remove it from the card before continuing.")
        with KlipperStopped():
            for n in pending:
                card("rm", n)
    state["step"] = "backup"
    state["from_version"] = current
    return True


def step_backup(state):
    heading(2, "Back up the whole mainboard flash")
    say("Reads the mainboard's 512 KB flash (bootloader and firmware) and its "
        "option bytes,\nthrough the mainboard itself: FLSun's STM32 is "
        "read-protected, so this is the only\nway to copy it. Klipper is "
        "stopped for about 12 minutes.")
    if not ask("Start the backup?"):
        return False
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = os.path.join(BACKUP_ROOT, stamp)
    os.makedirs(out)
    read = [KLIPPY_PY, os.path.join(TOOLS, "mcu_read_flash.py"),
            KLIPPER_DIR + "/klippy"]
    full = os.path.join(out, "mcu_flash_full_512k.bin")
    with KlipperStopped():
        rcs = [run(read + [full, "0x08000000", "0x80000"]),
               run(read + [os.path.join(out, "mcu_optionbytes.bin"),
                           "0x1FFFF800", "0x10"]),
               run(read + [os.path.join(out, "mcu_id.bin"),
                           "0x1FFFF7E0", "0x14"])]
    if any(rcs) or os.path.getsize(full) != FLASH_SIZE:
        stop("The backup failed; nothing was changed. See the messages above.")
    with open(full, "rb") as f:
        dump = f.read()
    app = dump[APP_START:].rstrip(b"\xff")
    app += b"\xff" * (-len(app) % 4)
    app_file = os.path.join(out, "mcu_app_%s.bin" % state.get("from_version"))
    with open(app_file, "wb") as f:
        f.write(app)
    with open(os.path.join(out, "mcu_bootloader.bin"), "wb") as f:
        f.write(dump[:APP_START])
    ok("read %d bytes; firmware %d bytes from 0x08007000" % (len(dump), len(app)))
    boot_sha = hashlib.sha256(dump[:APP_START]).hexdigest()
    if boot_sha == STOCK_BOOTLOADER_SHA256:
        ok("bootloader identical to FLSun's on the T1 Pro this was tested on")
    else:
        note("the bootloader differs from the tested T1 Pro's (sha256 %s). "
             "Continue only if you know your board." % boot_sha[:16])
    stock_sha = sha256_file(os.path.join(STOCK_DIR, "stock_klipper_app.bin"))
    if hashlib.sha256(app).hexdigest() == stock_sha:
        ok("firmware identical to FLSun's stock %s (mcu/stock)" % STOCK_VERSION)
    else:
        note("the firmware differs from FLSun's stock image; the rollback "
             "uses your own backup")
    # A Cortex-M vector table: initial stack pointer in RAM, reset handler in
    # the application's flash.
    sp = int.from_bytes(app[0:4], "little")
    reset = int.from_bytes(app[4:8], "little") & ~1
    if not (0x20000000 < sp <= 0x20010000
            and 0x08000000 + APP_START <= reset < 0x08000000 + FLASH_SIZE):
        stop("The firmware part of the backup doesn't look like firmware; "
             "stopping.")
    with open(os.path.join(out, "SHA256SUMS"), "w") as f:
        for name in sorted(os.listdir(out)):
            if name != "SHA256SUMS":
                f.write("%s  %s\n" % (sha256_file(os.path.join(out, name)), name))
    chown_tree(BACKUP_ROOT)
    say("\nSaved in %s:" % out)
    for name in sorted(os.listdir(out)):
        say("  %s" % name)
    say("\n\033[1mDownload this folder to your computer now\033[0m: in Mainsail "
        "or Fluidd, open the\nconfig files, folder mcu_backup/%s. It's the "
        "only copy of your mainboard's\nbootloader and firmware." % stamp)
    state["backup"] = out
    state["rollback_image"] = app_file
    state["step"] = "card"
    return True


def step_card(state):
    heading(3, "Rollback image onto the mainboard's card")
    say("Writes to the card in the mainboard's slot, beside nothing the "
        "bootloader would flash:\n"
        "  ROLLBACK.BIN  your current firmware, converted for the bootloader\n"
        "  MCUFULL.BIN   the whole 512 KB backup\n"
        "  ROLLBACK.TXT  how to use them\n"
        "To go back by hand later, rename ROLLBACK.BIN to Robin_nano35.bin on "
        "a computer\nand power the printer on with the card in the mainboard.")
    if not ask("Write them to the card?"):
        return False
    os.makedirs(WORK, exist_ok=True)
    rollback = os.path.join(WORK, "ROLLBACK.BIN")
    if run([KLIPPY_PY, KLIPPER_DIR + "/scripts/update_mks_robin.py",
            state["rollback_image"], rollback]) != 0:
        stop("Couldn't convert the rollback image.")
    txt = os.path.join(WORK, "ROLLBACK.TXT")
    with open(txt, "w") as f:
        f.write(
            "FLSun T1 Pro mainboard backup, made by FLSUN_klipper's mcu_update.py\n"
            "on %s, before flashing %s.\r\n\r\n"
            "ROLLBACK.BIN: the firmware the mainboard had (%s), converted for\r\n"
            "the MKS Robin Nano bootloader. To put it back by hand: on a computer,\r\n"
            "rename it to Robin_nano35.bin, put the card back in the MAINBOARD's\r\n"
            "slot and switch the printer on. The bootloader flashes it and renames\r\n"
            "it ROBIN_NANO35.CUR. Or run: sudo python3 ~/FLSUN_klipper/mcu/"
            "mcu_update.py rollback\r\n\r\n"
            "MCUFULL.BIN: the whole 512 KB flash (bootloader at 0x08000000,\r\n"
            "firmware at 0x08007000). Only for an ST-Link restore after removing\r\n"
            "the read protection, which erases the chip.\r\n"
            % (time.strftime("%Y-%m-%d %H:%M"), firmware_version(),
               state.get("from_version")))
    with KlipperStopped():
        rc, _ = card("put", "ROLLBACK.BIN", rollback,
                     "MCUFULL.BIN", os.path.join(state["backup"],
                                                 "mcu_flash_full_512k.bin"),
                     "ROLLBACK.TXT", txt)
        if rc == 0:
            card("ls")
    if rc != 0:
        stop("Writing to the card failed; nothing was flashed.")
    ok("rollback image and backup are on the card, read back and checked")
    state["step"] = "flash"
    return True


def step_flash(state):
    heading(4, "New firmware onto the card")
    target = firmware_version()
    say("Writes Klipper %s to the card as Robin_nano35.bin. Nothing changes "
        "until the\nprinter is switched off and on: then the bootloader "
        "flashes it." % target)
    if not ask("Write the new firmware to the card?"):
        return False
    with KlipperStopped():
        done = flash_image(os.path.join(FIRMWARE_DIR, "klipper.bin"))
    if not done:
        stop("Writing the firmware failed. The mainboard still runs its old "
             "firmware; run this again to retry.")
    ok("Robin_nano35.bin is on the card")
    state["step"] = "power_cycle"
    state["flashed_at"] = time.time()
    save_state(state)
    say("\n\033[1mNow switch the printer OFF with its power switch, wait 10 "
        "seconds, and switch it\nback ON.\033[0m A reboot or restart isn't "
        "enough: the bootloader only flashes at power-on.\nGive it about 2 "
        "minutes, connect again and run:\n\n"
        "  sudo python3 %s\n" % os.path.realpath(__file__))
    return False


def step_power_cycle(state):
    heading(5, "After the power cycle")
    target = firmware_version()
    if not wait_ready(60):
        state_now, msg = klipper_state()
        note("Klipper isn't ready: %s %s" % (state_now, msg))
    current = mcu_version()
    if current == target:
        ok("the mainboard runs %s" % target)
        state["step"] = "verify"
        return True
    bad("the mainboard still reports %s" % current)
    say("Checking the card (Klipper stops for about 30 seconds)")
    with KlipperStopped():
        _, out = card("ls")
    if "ROBIN_NANO35.CUR" in out.upper() and "ROBIN_NANO35.BIN" not in out.upper():
        note("The bootloader took the file (ROBIN_NANO35.CUR) but the "
             "mainboard reports %s." % current)
    elif "ROBIN_NANO35.BIN" in out.upper():
        say("Robin_nano35.bin is still on the card, so the bootloader didn't "
            "flash it. Switch\nthe printer fully off and on again (not a "
            "reboot). If that doesn't work, use a\nFAT32 card of 32 GB or less.")
    else:
        note("Robin_nano35.bin isn't on the card any more.")
    say("\nRun this again after the next power cycle, or roll back with "
        "'rollback'.")
    return False


def step_verify(state):
    heading(6, "Check the new firmware")
    target = firmware_version()
    # First start on new firmware: t1_mcu_version rewrote the includes for the
    # ADXL345 and asks for a RESTART.
    warnings = query("configfile=warnings").get("configfile", {}).get(
        "warnings", [])
    if any("firmware changed" in w.get("message", "") for w in warnings) \
            or klipper_state()[0] != "ready":
        say("  Restarting Klipper to load the config for the new firmware")
        if not restart_klipper():
            bad("Klipper isn't ready: %s" % (klipper_state(),))
            return False
    failures = 0
    current = mcu_version()
    if current == target:
        ok("mainboard firmware %s" % current)
    else:
        bad("mainboard firmware %s, expected %s" % (current, target))
        failures += 1
    warnings = query("configfile=warnings").get("configfile", {}).get(
        "warnings", [])
    mcu_warnings = [w for w in warnings if w.get("type") == "deprecated_mcu_code"]
    if mcu_warnings:
        bad("firmware warnings: %s" % "; ".join(w["message"] for w in mcu_warnings))
        failures += 1
    else:
        ok("no 'deprecated code' firmware warnings")
    try:
        recorded = open(MCU_VERSION_FILE).read().strip()
    except OSError:
        recorded = None
    if recorded == target:
        ok("recorded firmware %s (switching to FLSun's Klipper is now blocked)"
           % recorded)
    else:
        bad("recorded firmware is %s" % recorded)
        failures += 1
    try:
        adxl = "adxl.cfg" in open(INCLUDES_CFG).read()
    except OSError:
        adxl = False
    if adxl:
        ok("adxl.cfg is included")
    else:
        bad("adxl.cfg isn't included in %s" % INCLUDES_CFG)
        failures += 1
    if subprocess.call([KLIPPY_PY, "-c", "import numpy"],
                       stderr=subprocess.DEVNULL) != 0:
        say("  Installing numpy")
        run(["su", "pi", "-s", "/bin/sh", "-c",
             "python3 %s/scripts/flsun_klipper_cfg.py numpy" % REPO_DIR])
    if subprocess.call([KLIPPY_PY, "-c", "import numpy"],
                       stderr=subprocess.DEVNULL) == 0:
        ok("numpy is installed (for SHAPER_CALIBRATE)")
    else:
        bad("numpy isn't installed")
        failures += 1
    t0 = time.time()
    done, err = gcode("ACCELEROMETER_QUERY")
    values = [m for m in console_since(t0) if "accelerometer values" in m]
    if done and values:
        ok("ADXL345 answers: %s" % values[-1].lstrip("/ "))
    else:
        bad("ACCELEROMETER_QUERY failed: %s" % (err or "no answer"))
        failures += 1
    if failures:
        say("\nSome checks failed. The rollback is: sudo python3 %s rollback"
            % os.path.realpath(__file__))
        return False
    state["step"] = "probe"
    return True


def step_probe(state):
    heading(7, "PROBE_ACCURACY")
    say("Homes, then probes the middle of the bed 10 times. Make sure the bed "
        "is clear and\nthe nozzle is clean (no blob of filament). The probe "
        "is a pressure sensor; the first\nrun after the printer has been idle "
        "can read low, so run it again until it's steady.")
    while True:
        if not ask("Run G28 and PROBE_ACCURACY now?"):
            return False
        refuse_if_printing()
        t0 = time.time()
        done, err = gcode("G28", timeout=180)
        if done:
            done, err = gcode("PROBE_ACCURACY", timeout=300)
        lines = [m.lstrip("/ ") for m in console_since(t0)
                 if "probe accuracy results" in m or "probe at" in m]
        for l in lines:
            say("  " + l)
        if not done:
            bad("failed: %s" % err)
        if done and ask("Do the results look right (range of a few hundredths "
                        "of a mm, as before\nthe update)? 'n' runs it again"):
            state["probe_accuracy"] = lines[-1] if lines else ""
            state["step"] = "resonances"
            return True


def step_resonances(state):
    heading(8, "MEASURING_RESONANCES")
    say("Homes, shakes the printer at 20 mm above the bed center with the "
        "ADXL345 measuring,\nworks out the input shaper (about 1 minute on "
        "this board), saves it with\nSAVE_CONFIG and restarts Klipper. It's "
        "loud; that's normal.")
    if not ask("Run MEASURING_RESONANCES now?"):
        return False
    refuse_if_printing()
    t0 = time.time()
    done, err = gcode("MEASURING_RESONANCES", timeout=1800)
    # SAVE_CONFIG restarts Klipper, which can end the request early.
    time.sleep(5)
    wait_ready(180)
    results = [m.lstrip("/ ") for m in console_since(t0)
               if "Recommended shaper" in m or "Fitted shaper" in m]
    for l in results:
        say("  " + l)
    recommended = [l for l in results if l.startswith("Recommended")]
    if len(recommended) < 2:
        bad("no result (%s)" % (err or "see the console"))
        return False
    ok("input shaper measured and saved to printer_upstream.cfg")
    state["resonances"] = recommended
    state["step"] = "done"
    return True


def step_done(state):
    say("\n\033[1;36m== Done\033[0m")
    say("The mainboard runs Klipper %s.\n" % firmware_version())
    for key in ("backup", "probe_accuracy"):
        if state.get(key):
            say("  %s: %s" % (key, state[key]))
    for l in state.get("resonances", []):
        say("  %s" % l)
    say("\nKeep the backup folder on your computer. Rollback: sudo python3 %s "
        "rollback" % os.path.realpath(__file__))
    return False


STEP_FUNCS = {"check": step_check, "backup": step_backup, "card": step_card,
              "flash": step_flash, "power_cycle": step_power_cycle,
              "verify": step_verify, "probe": step_probe,
              "resonances": step_resonances, "done": step_done}


# --- Rollback ------------------------------------------------------------------------

def rollback(state):
    if os.geteuid() != 0:
        stop("Run it with sudo.")
    if state.get("step") == "rollback_power_cycle":
        # The first start on the old firmware stops at the ADXL345 ("Command
        # format mismatch") after dropping adxl.cfg; a restart then works.
        if not wait_ready(60):
            restart_klipper()
        current = mcu_version()
        if current and current != firmware_version():
            ok("the mainboard runs %s" % current)
            say("Klipper: %s" % (klipper_state(),))
            state.update(step="check", rolled_back_at=time.time())
            save_state(state)
            say("Rolled back. Klipper's config drops adxl.cfg by itself; if "
                "Klipper reports\n'Command format mismatch', run RESTART once.")
            return
        bad("the mainboard still reports %s; switch the printer off and on "
            "again." % current)
        return
    image = state.get("rollback_image")
    if not image or not os.path.exists(image):
        backups = sorted(glob.glob(BACKUP_ROOT + "/*/mcu_app_*.bin"))
        image = backups[-1] if backups else os.path.join(
            STOCK_DIR, "stock_klipper_app.bin")
    say("Rollback image: %s" % image)
    if not ask("Write it to the mainboard's card as Robin_nano35.bin?"):
        return
    with KlipperStopped():
        done = flash_image(image)
    if not done:
        stop("Writing the rollback image failed.")
    state["step"] = "rollback_power_cycle"
    save_state(state)
    say("\n\033[1mSwitch the printer OFF, wait 10 seconds, switch it ON\033[0m, "
        "then run:\n  sudo python3 %s rollback" % os.path.realpath(__file__))


def show_status(state):
    say("Step: %s" % state.get("step"))
    say("Mainboard firmware: %s (bundled: %s)" % (mcu_version(),
                                                  firmware_version()))
    say("Klipper: %s" % (klipper_state(),))
    for key in ("backup", "rollback_image", "probe_accuracy"):
        if state.get(key):
            say("%s: %s" % (key, state[key]))


def main():
    state = load_state()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "next"
    if cmd == "status":
        return show_status(state)
    if cmd == "rollback":
        return rollback(state)
    if cmd != "next":
        sys.exit(__doc__)
    if state.get("step") not in STEP_FUNCS:
        state["step"] = "check"
    while True:
        step = state["step"]
        advanced = STEP_FUNCS[step](state)
        save_state(state)
        if state["step"] == step:
            if step != "done":
                say("\nStopped at step '%s'. Run this again to continue." % step)
            return
        if not advanced:
            # The step moved on but waits for something (the power cycle).
            return


if __name__ == "__main__":
    main()
