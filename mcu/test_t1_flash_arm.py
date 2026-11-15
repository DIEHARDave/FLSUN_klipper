import os
import struct
import unittest

try:
    from unicorn import (
        Uc,
        UC_ARCH_ARM,
        UC_HOOK_CODE,
        UC_HOOK_MEM_WRITE,
        UC_MODE_MCLASS,
        UC_MODE_THUMB,
    )
except ImportError:
    Uc = None

import t1_flash


@unittest.skipIf(Uc is None, "optional Unicorn ARM emulator is not installed")
class RamFlasherCpuTests(unittest.TestCase):
    def setUp(self):
        from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_SP

        self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        self.uc.mem_map(t1_flash.FLASH_BASE, t1_flash.FLASH_SIZE)
        self.uc.mem_map(t1_flash.RAM_BASE, t1_flash.RAM_SIZE)
        self.uc.mem_map(0x40000000, 0x40000)
        self.uc.mem_map(0xE000E000, 0x2000)
        self.uc.mem_write(t1_flash.FLASH_BASE,
                          b"\xff" * t1_flash.FLASH_SIZE)
        self.uc.mem_write(0x40022010, struct.pack("<I", 0x80))
        self.uc.reg_write(UC_ARM_REG_SP, t1_flash.RAM_STACK_TOP - 16)
        self.uc.reg_write(UC_ARM_REG_LR, t1_flash.RAMFLASH_ADDR + 1)
        self.failures = []
        self.reset_requested = False
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self.on_write)
        self.uc.hook_add(UC_HOOK_CODE, self.on_code)

    def on_code(self, uc, address, size, user_data):
        if getattr(self, "app_entry", None) == address:
            self.shim_entered_app = True
            uc.emu_stop()

    def on_write(self, uc, access, address, size, value, user_data):
        if t1_flash.FLASH_BASE <= address < t1_flash.FLASH_BASE + t1_flash.FLASH_SIZE:
            if size != 2:
                self.failures.append("non-halfword flash write")
                uc.emu_stop()
                return
            cr = struct.unpack("<I", uc.mem_read(t1_flash.FLASH_CR, 4))[0]
            old = struct.unpack("<H", uc.mem_read(address, 2))[0]
            if cr & 0x80 or not cr & t1_flash.FLASH_PG:
                self.failures.append("flash write without unlocked PG mode")
            elif value | old != old:
                self.failures.append("flash attempted 0-to-1 programming")
        elif address == t1_flash.FLASH_KEYR:
            if value == 0x45670123:
                self.key_step = 1
            elif value == 0xCDEF89AB and getattr(self, "key_step", 0) == 1:
                self.uc.mem_write(t1_flash.FLASH_CR, struct.pack("<I", 0))
                self.key_step = 0
            else:
                self.key_step = 0
        elif address == t1_flash.FLASH_CR and value == 0x42:
            flash_address = struct.unpack(
                "<I", self.uc.mem_read(t1_flash.FLASH_AR, 4))[0]
            page = flash_address - t1_flash.FLASH_BASE
            self.uc.mem_write(
                flash_address, b"\xff" * t1_flash.FLASH_PAGE_SIZE)
            if page < 0 or page + t1_flash.FLASH_PAGE_SIZE > t1_flash.FLASH_SIZE:
                self.failures.append("page erase outside flash")
                uc.emu_stop()
        elif address == 0xE000ED0C and value == 0x05FA0004:
            self.reset_requested = True
            uc.emu_stop()

    def run_stub(self, operations, source=b""):
        ramflash, _, ops_offset = t1_flash.load_flash_assets()
        code = bytearray(ramflash)
        ops = t1_flash.operation_blob(operations)
        code[ops_offset:ops_offset + len(ops)] = ops
        self.uc.mem_write(t1_flash.RAMFLASH_ADDR, bytes(code))
        if source:
            source = t1_flash.padded_image(source, 4)
            self.uc.mem_write(t1_flash.RAM_SOURCE_ADDR, source)
        self.uc.emu_start(
            t1_flash.RAMFLASH_ADDR | 1,
            t1_flash.RAMFLASH_ADDR + len(ramflash),
            count=500000)
        self.assertTrue(self.reset_requested, "RAM routine did not request reset")
        self.assertFalse(self.failures, "; ".join(self.failures))

    def test_machine_code_installs_the_page_zero_shim(self):
        with open(os.path.join(t1_flash.MCU_DIR, "t1_nobl", "shim.bin"),
                  "rb") as stream:
            shim = stream.read()
        image = bytearray(b"\xff" * 128)
        struct.pack_into("<II", image, 0, 0x20005000,
                         t1_flash.FLASH_BASE + t1_flash.APP_START + 9)
        self.uc.mem_write(t1_flash.FLASH_BASE + t1_flash.APP_START,
                          bytes(image))

        self.run_stub(
            [(1, t1_flash.FLASH_BASE, 1, 0),
             (2, t1_flash.RAM_SOURCE_ADDR, t1_flash.FLASH_BASE, len(shim))],
            shim)

        self.assertEqual(bytes(self.uc.mem_read(t1_flash.FLASH_BASE, len(shim))),
                         shim)
        self.assertEqual(
            bytes(self.uc.mem_read(t1_flash.FLASH_BASE + t1_flash.APP_START,
                                   len(image))),
            bytes(image))

    def test_machine_code_erases_and_restores_full_original_dump(self):
        original = bytearray(b"\xff" * t1_flash.FLASH_SIZE)
        struct.pack_into("<II", original, 0, 0x20005000,
                         t1_flash.FLASH_BASE + 0x41)
        original[8:0x6800] = bytes(
            (index * 17) & 0xFF for index in range(8, 0x6800))
        self.uc.mem_write(t1_flash.FLASH_BASE + t1_flash.APP_START,
                          b"\x00" * 64)

        self.run_stub(
            [(1, t1_flash.FLASH_BASE,
              t1_flash.FLASH_SIZE // t1_flash.FLASH_PAGE_SIZE, 0),
             (2, t1_flash.RAM_SOURCE_ADDR, t1_flash.FLASH_BASE,
              t1_flash.APP_START)],
            bytes(original[:t1_flash.APP_START])
        )

        self.assertEqual(
            bytes(self.uc.mem_read(t1_flash.FLASH_BASE, t1_flash.FLASH_SIZE)),
            bytes(original))

    def test_jump_shim_sets_stack_and_branches_to_staged_image(self):
        from unicorn.arm_const import UC_ARM_REG_SP

        with open(os.path.join(t1_flash.MCU_DIR, "t1_nobl", "shim.bin"),
                  "rb") as stream:
            shim = stream.read()
        app_entry = t1_flash.FLASH_BASE + t1_flash.APP_START + 8
        image = bytearray(b"\xff" * 32)
        struct.pack_into("<II", image, 0, 0x20005000, app_entry | 1)
        image[8:10] = b"\xfe\xe7"
        self.uc.mem_write(t1_flash.FLASH_BASE, shim)
        self.uc.mem_write(t1_flash.FLASH_BASE + t1_flash.APP_START,
                          bytes(image))
        self.app_entry = app_entry
        self.shim_entered_app = False
        reset_handler = struct.unpack_from("<I", shim, 4)[0]

        self.uc.emu_start(reset_handler, t1_flash.FLASH_BASE + 0x100,
                          count=100)

        self.assertTrue(self.shim_entered_app)
        self.assertEqual(self.uc.reg_read(UC_ARM_REG_SP), 0x20005000)


if __name__ == "__main__":
    unittest.main()
