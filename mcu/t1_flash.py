#!/usr/bin/env python3
"""Back up, install, and roll back firmware on a no-bootloader FLSun T1.

Run on the printer's Linux board with Klipper's Python environment. The
destructive page-zero rewrite is kept separate from image staging and requires
an explicit typed confirmation.
"""

import argparse
import hashlib
import json
import os
import platform
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

MCU_DIR = os.path.dirname(os.path.realpath(__file__))
FIRMWARE_DIR = os.path.join(MCU_DIR, "firmware")
FLASH_BASE = 0x08000000
FLASH_SIZE = 0x80000
FLASH_PAGE_SIZE = 0x800
APP_START = 0x7000
RAM_BASE = 0x20000000
RAM_SIZE = 0x10000
RAMFLASH_ADDR = 0x20008100
RAMFLASH_OPS_ADDR = 0x200081B0
RAM_VECTOR_ADDR = 0x20007E00
RAM_VECTOR_COUNT = 76  # STM32F103 high-density: 16 core + 60 external.
RAM_SOURCE_ADDR = 0x20009000
RAM_STACK_TOP = RAM_BASE + RAM_SIZE
FLASH_KEYR = 0x40022004
FLASH_SR = 0x4002200C
FLASH_CR = 0x40022010
FLASH_AR = 0x40022014
FLASH_PG = 0x01
FLASH_ERROR_MASK = 0x14  # WRPRTERR | PGERR
FLASH_CLEAR_STATUS = 0x34  # EOP | WRPRTERR | PGERR
SCB_VTOR = 0xE000ED08
MCU_UID_ADDR = 0x1FFFF7E8
OPTION_BYTES_ADDR = 0x1FFFF800
OPTION_BYTES_SIZE = 0x10
DEVICE = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
BAUD = 250000
KLIPPY_DIR = "/home/pi/klipper-upstream/klippy"
MOONRAKER = "http://127.0.0.1:7125"
RAMFLASH_SHA256 = "f3c7692ff4d7f5b9d4e51b794d0527f15c9180560bc3f9ee398cd5255700b7c0"
SHIM_SHA256 = "28c889747194324c5274d77313c375a347399e6656831578c98393156f82bfac"
T1_CONFIG = {
    "MCU": "stm32f103xe",
    "CLOCK_FREQ": 72000000,
    "SERIAL_BAUD": 250000,
    "RESERVE_PINS_serial": "PB11,PB10",
}


class FlashError(Exception):
    """A validation, transport, or device operation failed."""


class KlipperDebugLink:
    """Small adapter over Klipper's serialhdl debug_read/debug_write API."""

    def __init__(self, serial, reactor):
        self.serial = serial
        self.reactor = reactor

    def read32(self, address):
        response = self.serial.send_with_response(
            "debug_read order=2 addr=%d" % address, "debug_result")
        return int(response["val"]) & 0xFFFFFFFF

    def write32(self, address, value):
        self.serial.send("debug_write order=2 addr=%d val=%d" %
                         (address, value & 0xFFFFFFFF))

    def write16(self, address, value):
        self.serial.send("debug_write order=1 addr=%d val=%d" %
                         (address, value & 0xFFFF))

    def wait_for_reset(self):
        # debug_write has no debug_result response. Give the queued VTOR write
        # time to reach the MCU and the first interrupt time to enter RAM.
        self.reactor.pause(self.reactor.monotonic() + 1.0)


def read_bytes(link, address, length, progress=None):
    if address % 4 or length % 4:
        raise FlashError("debug_read ranges must be 4-byte aligned")
    output = bytearray()
    for offset in range(0, length, 4):
        output.extend(struct.pack("<I", link.read32(address + offset)))
        if progress and (offset + 4) % 0x10000 == 0:
            print("%s: %d / %d KB" %
                  (progress, (offset + 4) // 1024, length // 1024),
                  flush=True)
    return bytes(output)


def write_words(link, address, data):
    if address % 4 or len(data) % 4:
        raise FlashError("debug_write RAM ranges must be 4-byte aligned")
    for offset in range(0, len(data), 4):
        value = struct.unpack_from("<I", data, offset)[0]
        link.write32(address + offset, value)
        if offset % 256 == 252:
            # A debug_read is an ordered serial barrier for preceding writes.
            link.read32(address + offset)
    if data:
        link.read32(address + len(data) - 4)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def load_firmware_bundle(image_path=None, image=None):
    try:
        with open(os.path.join(FIRMWARE_DIR, "klipper.dict"),
                  encoding="utf-8") as stream:
            dictionary = json.load(stream)
        with open(os.path.join(FIRMWARE_DIR, "SHA256SUMS"),
                  encoding="ascii") as stream:
            sums = {}
            for line in stream:
                if line.strip():
                    digest, name = line.split(None, 1)
                    sums[name.strip().lstrip("*")] = digest
    except (OSError, ValueError) as exc:
        raise FlashError("cannot read firmware dictionary/checksums: %s" % exc)
    if not isinstance(dictionary, dict):
        raise FlashError("firmware dictionary is not a JSON object")

    for name in ("klipper.dict", "klipper.bin"):
        expected = sums.get(name)
        path = os.path.join(FIRMWARE_DIR, name)
        if expected is None:
            raise FlashError("firmware SHA256SUMS has no entry for %s" % name)
        if name == "klipper.dict":
            with open(path, "rb") as stream:
                contents = stream.read()
        elif image_path is None:
            with open(path, "rb") as stream:
                contents = stream.read()
        elif image is not None:
            contents = image
        else:
            raise FlashError("an image is required to validate its checksum")
        if expected.strip().lstrip("*").lower() != sha256(contents):
            raise FlashError("%s does not match firmware/SHA256SUMS" % name)

    config = dictionary.get("config", {})
    if not isinstance(config, dict):
        raise FlashError("firmware dictionary has no config object")
    for key, expected in T1_CONFIG.items():
        if str(config.get(key)) != str(expected):
            raise FlashError("bundled firmware has unexpected %s" % key)
    commands = dictionary.get("commands", {})
    if not any(name.startswith("debug_read ") for name in commands):
        raise FlashError("bundled firmware dictionary has no debug_read")
    if not any(name.startswith("debug_write ") for name in commands):
        raise FlashError("bundled firmware dictionary has no debug_write")
    return config


def validate_live_firmware(constants):
    expected = load_firmware_bundle()
    for key, value in T1_CONFIG.items():
        if str(constants.get(key)) != str(value):
            raise FlashError(
                "connected MCU %s is %r; T1 image requires %r" %
                (key, constants.get(key), expected[key]))


def metadata_path(backup_path):
    return backup_path + ".json"


def validate_backup_contents(data):
    if len(data) != FLASH_SIZE:
        raise FlashError("backup must be exactly %d bytes" % FLASH_SIZE)
    if data[APP_START:] != b"\xff" * (FLASH_SIZE - APP_START):
        raise FlashError(
            "backup is not the expected blank-tail, no-bootloader T1 layout")
    sp, reset = struct.unpack_from("<II", data)
    reset &= ~1
    if not (RAM_BASE < sp <= RAM_STACK_TOP and sp % 4 == 0):
        raise FlashError("backup has an invalid initial stack pointer")
    if not (FLASH_BASE <= reset < FLASH_BASE + APP_START):
        raise FlashError("backup does not contain FLSun firmware at page 0")


def validate_image(image):
    if len(image) < 8 or len(image) > FLASH_SIZE - APP_START:
        raise FlashError("firmware image size is outside the T1 flash range")
    sp, reset = struct.unpack_from("<II", image)
    reset &= ~1
    if not (RAM_BASE < sp <= RAM_STACK_TOP and sp % 4 == 0):
        raise FlashError("firmware image has an invalid initial stack pointer")
    if not (FLASH_BASE + APP_START <= reset <
            FLASH_BASE + APP_START + len(image)):
        raise FlashError("firmware reset vector is outside the image")
    if not struct.unpack_from("<I", image, 4)[0] & 1:
        raise FlashError("firmware reset vector is not a Thumb address")


def read_backup(backup_path):
    try:
        with open(backup_path, "rb") as stream:
            data = stream.read()
        with open(metadata_path(backup_path), encoding="utf-8") as stream:
            metadata = json.load(stream)
    except (OSError, ValueError) as exc:
        raise FlashError("cannot read backup and metadata: %s" % exc)
    validate_backup_contents(data)
    if not isinstance(metadata, dict):
        raise FlashError("backup metadata is not a JSON object")
    if metadata.get("flash_size") != FLASH_SIZE:
        raise FlashError("backup metadata has an unexpected flash size")
    digest = metadata.get("sha256")
    if not isinstance(digest, str) or digest.lower() != sha256(data):
        raise FlashError("backup checksum does not match its metadata")
    uid = metadata.get("mcu_uid")
    if (not isinstance(uid, str) or len(uid) != 24
            or any(c not in "0123456789abcdefABCDEF" for c in uid)):
        raise FlashError("backup metadata has no valid MCU unique ID")
    option_bytes = metadata.get("option_bytes")
    if (not isinstance(option_bytes, str)
            or len(option_bytes) != OPTION_BYTES_SIZE * 2
            or any(c not in "0123456789abcdefABCDEF" for c in option_bytes)):
        raise FlashError("backup metadata has no valid option-byte dump")
    return data, metadata


def verify_live_board(link, backup, metadata, staged_image):
    uid = read_bytes(link, MCU_UID_ADDR, 12).hex()
    if uid.lower() != metadata["mcu_uid"].lower():
        raise FlashError("backup belongs to a different STM32 unique ID")
    options = read_bytes(link, OPTION_BYTES_ADDR, OPTION_BYTES_SIZE).hex()
    if options.lower() != metadata["option_bytes"].lower():
        raise FlashError("STM32 option bytes differ from the saved backup")
    print("Checking the live 512 KB flash against the backup...")
    live = read_bytes(link, FLASH_BASE, FLASH_SIZE, "Flash comparison")
    start = APP_START
    staged = padded_image(staged_image, 4)
    end = start + len(staged)
    if live[:start] != backup[:start] or live[end:] != backup[end:]:
        raise FlashError(
            "live flash differs from the backup outside the staged image")
    for offset in range(0, len(staged), 2):
        current = live[start + offset:start + offset + 2]
        desired = staged[offset:offset + 2]
        if current not in (b"\xff\xff", desired):
            raise FlashError(
                "live flash contains conflicting staged data at 0x%08X" %
                (FLASH_BASE + start + offset))


def make_backup(link, backup_path):
    if os.path.exists(backup_path) or os.path.exists(metadata_path(backup_path)):
        raise FlashError("refusing to overwrite an existing backup")
    parent = os.path.dirname(os.path.abspath(backup_path))
    os.makedirs(parent, exist_ok=True)
    print("Reading the full 512 KB flash...")
    data = read_bytes(link, FLASH_BASE, FLASH_SIZE, "Backup")
    validate_backup_contents(data)
    uid = read_bytes(link, MCU_UID_ADDR, 12).hex()
    option_bytes = read_bytes(
        link, OPTION_BYTES_ADDR, OPTION_BYTES_SIZE).hex()
    metadata = {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "flash_size": FLASH_SIZE,
        "mcu_uid": uid,
        "option_bytes": option_bytes,
        "sha256": sha256(data),
    }
    temp_data = None
    temp_meta = None
    try:
        fd, temp_data = tempfile.mkstemp(
            prefix=os.path.basename(backup_path) + ".", dir=parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        fd, temp_meta = tempfile.mkstemp(
            prefix=os.path.basename(metadata_path(backup_path)) + ".",
            dir=parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write((json.dumps(metadata, indent=2, sort_keys=True)
                          + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_data, backup_path)
        temp_data = None
        os.replace(temp_meta, metadata_path(backup_path))
        temp_meta = None
    finally:
        for path in (temp_data, temp_meta):
            if path and os.path.exists(path):
                os.remove(path)
    chown_backup(backup_path)
    print("Saved full backup and MCU identity:")
    print("  %s" % backup_path)
    print("  %s" % metadata_path(backup_path))
    print("  MCU UID: %s" % uid)


def chown_backup(path):
    uid = os.environ.get("SUDO_UID")
    gid = os.environ.get("SUDO_GID")
    if uid and gid:
        os.chown(path, int(uid), int(gid))
        os.chown(metadata_path(path), int(uid), int(gid))


def padded_image(image, alignment):
    return image + b"\xff" * (-len(image) % alignment)


def stage_image(link, image):
    image_length = len(image)
    image = padded_image(image, 4)
    address = FLASH_BASE + APP_START
    old = read_bytes(link, address, len(image))
    for offset in range(0, len(image), 2):
        current = old[offset:offset + 2]
        desired = image[offset:offset + 2]
        if current not in (b"\xff\xff", desired):
            raise FlashError(
                "staged image conflicts with existing flash at 0x%08X; "
                "use rollback before retrying" % (address + offset))

    link.write32(FLASH_KEYR, 0x45670123)
    link.write32(FLASH_KEYR, 0xCDEF89AB)
    if link.read32(FLASH_CR) & 0x80:
        raise FlashError("STM32 flash controller did not unlock")
    link.write32(FLASH_SR, FLASH_CLEAR_STATUS)
    link.write32(FLASH_CR, FLASH_PG)
    link.read32(FLASH_CR)
    count = 0
    try:
        for offset in range(0, len(image), 2):
            desired = image[offset:offset + 2]
            if desired == b"\xff\xff" or old[offset:offset + 2] == desired:
                continue
            value = struct.unpack("<H", desired)[0]
            link.write16(address + offset, value)
            count += 1
            if count % 64 == 0:
                status = link.read32(FLASH_SR)
                if status & FLASH_ERROR_MASK:
                    raise FlashError("STM32 flash status error 0x%08X" % status)
                link.write32(FLASH_SR, FLASH_CLEAR_STATUS)
                link.read32(FLASH_SR)
        status = link.read32(FLASH_SR)
        if status & FLASH_ERROR_MASK:
            raise FlashError("STM32 flash status error 0x%08X" % status)
        link.write32(FLASH_SR, FLASH_CLEAR_STATUS)
        link.read32(FLASH_SR)
    finally:
        link.write32(FLASH_CR, 0)
        link.write32(FLASH_CR, 0x80)
        link.read32(FLASH_CR)

    if read_bytes(link, address, len(image)) != image:
        raise FlashError("firmware read-back did not match the input image")
    print("Staged and verified %d firmware bytes at 0x%08X" %
          (image_length, address))


def load_flash_assets():
    assets = os.path.join(MCU_DIR, "t1_nobl")
    paths = {
        "ramflash": os.path.join(assets, "ramflash.bin"),
        "shim": os.path.join(assets, "shim.bin"),
        "ops": os.path.join(assets, "ramflash.ops"),
    }
    try:
        with open(paths["ramflash"], "rb") as stream:
            ramflash = stream.read()
        with open(paths["shim"], "rb") as stream:
            shim = stream.read()
        with open(paths["ops"], encoding="ascii") as stream:
            ops_address = int(stream.read().strip(), 16)
    except (OSError, ValueError) as exc:
        raise FlashError("cannot read assembled RAM flasher assets: %s" % exc)
    if sha256(ramflash) != RAMFLASH_SHA256 or sha256(shim) != SHIM_SHA256:
        raise FlashError("assembled flasher asset checksum mismatch")
    if ops_address != RAMFLASH_OPS_ADDR:
        raise FlashError("ramflash.ops address does not match the assembled image")
    ops_offset = ops_address - RAMFLASH_ADDR
    if ops_offset < 0 or ops_offset + 8 * 16 > len(ramflash):
        raise FlashError("ramflash.ops is outside the assembled RAM flasher")
    shim_sp, shim_reset = struct.unpack_from("<II", shim)
    shim_reset &= ~1
    if not (RAM_BASE < shim_sp <= RAM_STACK_TOP and shim_sp % 4 == 0):
        raise FlashError("jump shim has an invalid initial stack pointer")
    if not (FLASH_BASE <= shim_reset < FLASH_BASE + FLASH_PAGE_SIZE):
        raise FlashError("jump shim reset vector is outside page 0")
    if not struct.unpack_from("<I", shim, 4)[0] & 1:
        raise FlashError("jump shim reset vector is not a Thumb address")
    return ramflash, shim, ops_offset


def operation_blob(operations):
    records = list(operations) + [(0, 0, 0, 0)]
    if len(records) > 8:
        raise FlashError("RAM flasher supports at most 8 operations")
    output = bytearray(8 * 16)
    for index, record in enumerate(records):
        struct.pack_into("<IIII", output, index * 16, *record)
    return bytes(output)


def execute_ramflash(link, operations, source_data=b"", source_address=None):
    ramflash, _, ops_offset = load_flash_assets()
    if source_data and source_address is None:
        source_address = RAM_SOURCE_ADDR
    if source_data and source_address + len(source_data) > RAM_STACK_TOP:
        raise FlashError("RAM flash source does not fit in STM32 SRAM")

    code = bytearray(ramflash)
    ops = operation_blob(operations)
    code[ops_offset:ops_offset + len(ops)] = ops
    write_words(link, RAMFLASH_ADDR, bytes(code))
    if read_bytes(link, RAMFLASH_ADDR, len(code)) != bytes(code):
        raise FlashError("RAM flasher read-back failed")

    if source_data:
        source = padded_image(source_data, 4)
        if source_address + len(source) > RAM_STACK_TOP:
            raise FlashError("padded RAM source does not fit in STM32 SRAM")
        write_words(link, source_address, source)
        if read_bytes(link, source_address, len(source)) != source:
            raise FlashError("RAM source read-back failed")

    handler = RAMFLASH_ADDR + 1
    vectors = [RAM_STACK_TOP, handler] + [handler] * (RAM_VECTOR_COUNT - 2)
    vector_data = struct.pack("<%dI" % len(vectors), *vectors)
    write_words(link, RAM_VECTOR_ADDR, vector_data)
    if read_bytes(link, RAM_VECTOR_ADDR, len(vector_data)) != vector_data:
        raise FlashError("RAM interrupt vector table read-back failed")

    print("Verified RAM flasher and vector table; triggering page rewrite.")
    link.write32(SCB_VTOR, RAM_VECTOR_ADDR)
    link.wait_for_reset()


def confirm(prompt, phrase):
    try:
        response = input("%s Type %r to continue: " % (prompt, phrase))
    except EOFError:
        raise FlashError("confirmation input is required")
    if response != phrase:
        raise FlashError("confirmation did not match; destructive rewrite cancelled")


def install(link, backup_path, image_path, confirmation=confirm):
    backup, metadata = read_backup(backup_path)
    with open(image_path, "rb") as stream:
        image = stream.read()
    validate_image(image)
    load_firmware_bundle(image_path, image)
    verify_live_board(link, backup, metadata, image)
    stage_image(link, image)
    confirmation(
        "The new image is staged and verified. Rewrite page 0 now?",
        "INSTALL T1")
    _, shim, _ = load_flash_assets()
    execute_ramflash(
        link,
        [(1, FLASH_BASE, 1, 0),
         (2, RAM_SOURCE_ADDR, FLASH_BASE, len(shim))],
        source_data=shim,
    )


def rollback(link, backup_path, confirmation=confirm):
    backup, metadata = read_backup(backup_path)
    uid = read_bytes(link, MCU_UID_ADDR, 12).hex()
    if uid.lower() != metadata["mcu_uid"].lower():
        raise FlashError("backup belongs to a different STM32 unique ID")
    options = read_bytes(link, OPTION_BYTES_ADDR, OPTION_BYTES_SIZE).hex()
    if options.lower() != metadata["option_bytes"].lower():
        raise FlashError("STM32 option bytes differ from the saved backup")
    confirmation(
        "This erases all 512 KB and restores this board's original firmware.",
        "ROLL BACK T1")
    execute_ramflash(
        link,
        [(1, FLASH_BASE, FLASH_SIZE // FLASH_PAGE_SIZE, 0),
         (2, RAM_SOURCE_ADDR, FLASH_BASE, APP_START)],
        source_data=backup[:APP_START],
    )


def moonraker_print_state():
    url = MOONRAKER + "/printer/objects/query?print_stats=state"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            result = json.load(response)["result"]["status"]
        return result["print_stats"]["state"]
    except Exception as exc:
        raise FlashError("cannot verify printer idle state via Moonraker: %s" %
                         exc)


def wait_klipper_ready(timeout=120):
    deadline = time.time() + timeout
    last_error = "not ready"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(MOONRAKER + "/printer/info",
                                        timeout=5) as response:
                info = json.load(response)["result"]
            if info.get("state") == "ready":
                return
            last_error = info.get("state_message", info.get("state", "unknown"))
        except Exception as exc:
            last_error = str(exc)
        time.sleep(2)
    raise FlashError("Klipper did not become ready after restart: %s" %
                     last_error)


class KlipperStopped:
    """Stop the host service around direct serial access, then restore it."""

    def __enter__(self):
        if platform.system() != "Linux":
            raise FlashError("direct printer flashing must run on its Linux board")
        if moonraker_print_state() in ("printing", "paused"):
            raise FlashError("a print is running; wait until it has finished")
        active = subprocess.run(
            ["systemctl", "is-active", "--quiet", "klipper"]).returncode == 0
        if not active:
            raise FlashError("Klipper service is not active; refusing to proceed")
        show = subprocess.run(
            ["systemctl", "show", "klipper", "-p", "NeedDaemonReload"],
            check=True, stdout=subprocess.PIPE, universal_newlines=True)
        if "=yes" in show.stdout:
            subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "stop", "klipper"], check=True)
        self.stopped = True
        time.sleep(2)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if getattr(self, "stopped", False):
            try:
                subprocess.run(["systemctl", "start", "klipper"], check=True)
                wait_klipper_ready()
            except Exception as start_error:
                raise FlashError(
                    "Klipper could not be restored after direct serial access: "
                    "%s" % start_error) from exc_value
        return False


def run_serial(args, operation):
    sys.path.insert(0, args.klippy_dir)
    try:
        import reactor
        import serialhdl
    except ImportError as exc:
        raise FlashError(
            "cannot import Klipper serial libraries from %s: %s" %
            (args.klippy_dir, exc))

    reactor_instance = reactor.Reactor()
    serial = serialhdl.SerialReader(reactor_instance)
    results = []
    errors = []

    def run_callback(eventtime):
        try:
            serial.connect_uart(args.device, args.baud)
            parser = serial.get_msgparser()
            validate_live_firmware(parser.get_constants())
            parser.lookup_command("debug_read order=%c addr=%u")
            parser.lookup_command("debug_write order=%c addr=%u val=%u")
            results.append(operation(KlipperDebugLink(serial, reactor_instance)))
        except Exception as exc:
            errors.append(exc)
        finally:
            serial.disconnect()
            reactor_instance.end()

    reactor_instance.register_callback(run_callback)
    reactor_instance.run()
    if errors:
        raise FlashError(str(errors[0])) from errors[0]
    if not results:
        raise FlashError("serial operation did not complete")
    return results[0]


def run_guarded(args, operation):
    with KlipperStopped():
        return run_serial(args, operation)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default=DEVICE)
    parser.add_argument("--baud", type=int, default=BAUD)
    parser.add_argument("--klippy-dir", default=KLIPPY_DIR)
    commands = parser.add_subparsers(dest="command", required=True)

    backup_parser = commands.add_parser("backup", help="save full flash and MCU ID")
    backup_parser.add_argument("output", help="new full-backup .bin path")
    install_parser = commands.add_parser("install", help="stage and install firmware")
    install_parser.add_argument("--backup", required=True)
    install_parser.add_argument("--image", required=True)
    rollback_parser = commands.add_parser("rollback", help="restore the saved firmware")
    rollback_parser.add_argument("--backup", required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == "backup":
            run_guarded(args, lambda link: make_backup(link, args.output))
        elif args.command == "install":
            run_guarded(args, lambda link: install(
                link, args.backup, args.image))
        else:
            run_guarded(args, lambda link: rollback(link, args.backup))
    except (FlashError, OSError, subprocess.SubprocessError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
