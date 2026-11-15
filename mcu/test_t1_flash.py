import json
import os
import struct
import tempfile
import unittest

import t1_flash


class FlashEmulator:
    """Protocol-level STM32 model for host workflow tests, not an ARM CPU."""

    def __init__(self, flash, uid):
        self.flash = bytearray(flash)
        self.ram = bytearray(t1_flash.RAM_SIZE)
        self.uid = bytes.fromhex(uid)
        self.option_bytes = bytes(range(t1_flash.OPTION_BYTES_SIZE))
        self.flash_cr = 0x80
        self.flash_sr = 0
        self.key_step = 0
        self.reset_triggered = False

    def read32(self, address):
        if address == t1_flash.FLASH_CR:
            return self.flash_cr
        if address == t1_flash.FLASH_SR:
            return self.flash_sr
        if t1_flash.MCU_UID_ADDR <= address < t1_flash.MCU_UID_ADDR + len(self.uid):
            offset = address - t1_flash.MCU_UID_ADDR
            return struct.unpack_from("<I", self.uid, offset)[0]
        if (t1_flash.OPTION_BYTES_ADDR <= address <
                t1_flash.OPTION_BYTES_ADDR + len(self.option_bytes)):
            offset = address - t1_flash.OPTION_BYTES_ADDR
            return struct.unpack_from("<I", self.option_bytes, offset)[0]
        if t1_flash.FLASH_BASE <= address < t1_flash.FLASH_BASE + t1_flash.FLASH_SIZE:
            offset = address - t1_flash.FLASH_BASE
            return struct.unpack_from("<I", self.flash, offset)[0]
        if t1_flash.RAM_BASE <= address < t1_flash.RAM_BASE + t1_flash.RAM_SIZE:
            offset = address - t1_flash.RAM_BASE
            return struct.unpack_from("<I", self.ram, offset)[0]
        raise AssertionError("unexpected read at 0x%08X" % address)

    def write32(self, address, value):
        value &= 0xFFFFFFFF
        if address == t1_flash.FLASH_KEYR:
            expected = (0x45670123, 0xCDEF89AB)
            if value == expected[self.key_step]:
                self.key_step += 1
                if self.key_step == 2:
                    self.flash_cr &= ~0x80
                    self.key_step = 0
            else:
                self.key_step = 0
            return
        if address == t1_flash.FLASH_CR:
            self.flash_cr = value
            return
        if address == t1_flash.FLASH_SR:
            self.flash_sr &= ~(value & t1_flash.FLASH_CLEAR_STATUS)
            return
        if address == t1_flash.SCB_VTOR:
            self.execute_ramflash(value)
            return
        if t1_flash.RAM_BASE <= address < t1_flash.RAM_BASE + t1_flash.RAM_SIZE:
            offset = address - t1_flash.RAM_BASE
            struct.pack_into("<I", self.ram, offset, value)
            return
        raise AssertionError("unexpected write at 0x%08X" % address)

    def write16(self, address, value):
        if t1_flash.FLASH_BASE <= address < t1_flash.FLASH_BASE + t1_flash.FLASH_SIZE:
            if self.flash_cr & 0x80 or not self.flash_cr & t1_flash.FLASH_PG:
                raise AssertionError("flash programming without unlock/PG")
            offset = address - t1_flash.FLASH_BASE
            old = struct.unpack_from("<H", self.flash, offset)[0]
            if value | old != old:
                raise AssertionError("attempted flash 0-to-1 programming")
            struct.pack_into("<H", self.flash, offset, old & value)
            return
        if t1_flash.RAM_BASE <= address < t1_flash.RAM_BASE + t1_flash.RAM_SIZE:
            offset = address - t1_flash.RAM_BASE
            struct.pack_into("<H", self.ram, offset, value)
            return
        raise AssertionError("unexpected halfword write at 0x%08X" % address)

    def wait_for_reset(self):
        self.reset_triggered = True

    def execute_ramflash(self, vector_address):
        self.assert_vector_table(vector_address)
        with open(os.path.join(t1_flash.MCU_DIR, "t1_nobl", "ramflash.ops"),
                  encoding="ascii") as stream:
            ops_address = int(stream.read().strip(), 16)
        offset = ops_address - t1_flash.RAM_BASE
        self.flash_cr &= ~0x80
        for index in range(8):
            op, a, b, c = struct.unpack_from("<IIII", self.ram,
                                             offset + index * 16)
            if op == 0:
                break
            if op == 1:
                self.erase(a, b)
            elif op == 2:
                self.copy(a, b, c)
            else:
                raise AssertionError("unknown flasher operation %d" % op)
        else:
            raise AssertionError("RAM flasher operation list has no terminator")
        self.flash_cr |= 0x80
        self.reset_triggered = True

    def assert_vector_table(self, address):
        if address != t1_flash.RAM_VECTOR_ADDR:
            raise AssertionError("unexpected VTOR 0x%08X" % address)
        vector = self.read32(address)
        handler = t1_flash.RAMFLASH_ADDR + 1
        if vector != t1_flash.RAM_STACK_TOP:
            raise AssertionError("invalid initial stack pointer")
        for index in range(1, t1_flash.RAM_VECTOR_COUNT):
            if self.read32(address + index * 4) != handler:
                raise AssertionError("invalid vector handler at index %d" % index)

    def erase(self, address, pages):
        if self.flash_cr & 0x80:
            raise AssertionError("page erase while flash is locked")
        if address % t1_flash.FLASH_PAGE_SIZE:
            raise AssertionError("unaligned page erase")
        start = address - t1_flash.FLASH_BASE
        end = start + pages * t1_flash.FLASH_PAGE_SIZE
        self.flash[start:end] = b"\xff" * (end - start)

    def copy(self, source, destination, length):
        if self.flash_cr & 0x80:
            raise AssertionError("flash copy while locked")
        source_offset = source - t1_flash.RAM_BASE
        destination_offset = destination - t1_flash.FLASH_BASE
        for offset in range(0, length, 2):
            value = struct.unpack_from("<H", self.ram, source_offset + offset)[0]
            if value == 0xFFFF:
                continue
            old = struct.unpack_from("<H", self.flash, destination_offset + offset)[0]
            if value | old != old:
                raise AssertionError("copy attempted flash 0-to-1 programming")
            struct.pack_into("<H", self.flash, destination_offset + offset,
                             old & value)


def original_dump():
    dump = bytearray(b"\xff" * t1_flash.FLASH_SIZE)
    struct.pack_into("<II", dump, 0, 0x20005000, t1_flash.FLASH_BASE + 0x41)
    dump[8:0x6800] = bytes((index * 17) & 0xFF for index in range(8, 0x6800))
    return bytes(dump)


def klipper_image():
    image = bytearray(b"\xff" * 256)
    struct.pack_into("<II", image, 0, 0x20005000,
                     t1_flash.FLASH_BASE + t1_flash.APP_START + 9)
    image[8:64] = bytes(range(56))
    return bytes(image)


class T1FlashTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.uid = "00112233445566778899aabb"
        self.backup_path = os.path.join(self.temp.name, "full.bin")
        self.backup = original_dump()
        self.device = FlashEmulator(self.backup, self.uid)
        with open(self.backup_path, "wb") as stream:
            stream.write(self.backup)
        with open(t1_flash.metadata_path(self.backup_path), "w",
                  encoding="utf-8") as stream:
            json.dump({
                "flash_size": t1_flash.FLASH_SIZE,
                "mcu_uid": self.uid,
                "option_bytes": self.device.option_bytes.hex(),
                "sha256": t1_flash.sha256(self.backup),
            }, stream)
        self.image_path = os.path.join(self.temp.name, "klipper.bin")
        with open(os.path.join(t1_flash.FIRMWARE_DIR, "klipper.bin"),
                  "rb") as source:
            image = source.read()
        with open(self.image_path, "wb") as stream:
            stream.write(image)

    def tearDown(self):
        self.temp.cleanup()

    def test_backup_saves_full_flash_and_unique_id(self):
        output = os.path.join(self.temp.name, "new-backup.bin")

        t1_flash.make_backup(self.device, output)
        backup, metadata = t1_flash.read_backup(output)

        self.assertEqual(backup, self.backup)
        self.assertEqual(metadata["mcu_uid"], self.uid)
        self.assertEqual(metadata["option_bytes"],
                         self.device.option_bytes.hex())

    def test_live_config_guard_checks_target_constants(self):
        t1_flash.validate_live_firmware(t1_flash.T1_CONFIG)
        wrong = dict(t1_flash.T1_CONFIG)
        wrong["SERIAL_BAUD"] = 115200

        with self.assertRaisesRegex(t1_flash.FlashError, "SERIAL_BAUD"):
            t1_flash.validate_live_firmware(wrong)

    def test_install_stages_verifies_and_rewrites_page_zero(self):
        t1_flash.install(
            self.device, self.backup_path, self.image_path,
            confirmation=lambda prompt, phrase: self.assertEqual(phrase, "INSTALL T1"))

        self.assertTrue(self.device.reset_triggered)
        with open(os.path.join(t1_flash.MCU_DIR, "t1_nobl", "shim.bin"),
                  "rb") as stream:
            shim = stream.read()
        with open(self.image_path, "rb") as stream:
            image = stream.read()
        self.assertEqual(self.device.flash[:len(shim)], shim)
        self.assertEqual(
            self.device.flash[t1_flash.APP_START:
                              t1_flash.APP_START + len(image)],
            image)
        self.assertEqual(self.device.flash[len(shim):t1_flash.FLASH_PAGE_SIZE],
                         b"\xff" * (t1_flash.FLASH_PAGE_SIZE - len(shim)))

    def test_install_can_resume_after_image_staging(self):
        def decline(prompt, phrase):
            raise t1_flash.FlashError("user deferred page-zero rewrite")

        with self.assertRaisesRegex(t1_flash.FlashError, "deferred"):
            t1_flash.install(
                self.device, self.backup_path, self.image_path,
                confirmation=decline)
        self.assertFalse(self.device.reset_triggered)

        t1_flash.install(
            self.device, self.backup_path, self.image_path,
            confirmation=lambda prompt, phrase: True)

        self.assertTrue(self.device.reset_triggered)

    def test_rollback_restores_the_full_original_flash(self):
        self.device.flash[t1_flash.APP_START:t1_flash.APP_START + 256] = \
            klipper_image()
        t1_flash.rollback(
            self.device, self.backup_path,
            confirmation=lambda prompt, phrase: self.assertEqual(
                phrase, "ROLL BACK T1"))

        self.assertTrue(self.device.reset_triggered)
        self.assertEqual(bytes(self.device.flash), self.backup)

    def test_install_refuses_backup_for_another_board_before_writing(self):
        self.device.uid = bytes.fromhex("ffeeddccbbaa998877665544")
        before = bytes(self.device.flash)

        with self.assertRaisesRegex(t1_flash.FlashError, "different STM32"):
            t1_flash.install(
                self.device, self.backup_path, self.image_path,
                confirmation=lambda prompt, phrase: True)

        self.assertEqual(bytes(self.device.flash), before)
        self.assertFalse(self.device.reset_triggered)

    def test_install_refuses_bad_image_vector_before_writing(self):
        with open(self.image_path, "wb") as stream:
            stream.write(b"\x00" * 32)
        before = bytes(self.device.flash)

        with self.assertRaisesRegex(t1_flash.FlashError, "stack pointer"):
            t1_flash.install(
                self.device, self.backup_path, self.image_path,
                confirmation=lambda prompt, phrase: True)

        self.assertEqual(bytes(self.device.flash), before)

    def test_install_refuses_unmanifested_image_before_writing(self):
        with open(self.image_path, "wb") as stream:
            stream.write(klipper_image())
        before = bytes(self.device.flash)

        with self.assertRaisesRegex(t1_flash.FlashError, "SHA256SUMS"):
            t1_flash.install(
                self.device, self.backup_path, self.image_path,
                confirmation=lambda prompt, phrase: True)

        self.assertEqual(bytes(self.device.flash), before)

    def test_stage_refuses_nonblank_conflicting_flash(self):
        image = klipper_image()
        struct.pack_into("<H", self.device.flash,
                         t1_flash.APP_START, 0)
        before = bytes(self.device.flash)

        with self.assertRaisesRegex(t1_flash.FlashError, "conflicts"):
            t1_flash.stage_image(self.device, image)

        self.assertEqual(bytes(self.device.flash), before)


if __name__ == "__main__":
    unittest.main()
