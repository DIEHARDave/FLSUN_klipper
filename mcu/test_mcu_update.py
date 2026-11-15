import hashlib
import json
import os
import struct
import tempfile
import unittest
from unittest import mock

import mcu_update
import t1_flash


class T1DetectionTests(unittest.TestCase):
    def test_detects_known_regular_t1_version(self):
        self.assertTrue(mcu_update.is_t1_no_bootloader_version(
            "v1.0.6.3-18-g9d78b20"))
        self.assertFalse(mcu_update.is_t1_no_bootloader_version(
            "v1.0.6.30-18-g9d78b20"))
        self.assertFalse(mcu_update.is_t1_no_bootloader_version(None))

    def test_check_routes_regular_t1_before_pro_card_checks(self):
        state = {}
        patches = (
            mock.patch.object(mcu_update.os, "geteuid", return_value=0,
                              create=True),
            mock.patch.object(mcu_update, "check_sums", return_value=[]),
            mock.patch.object(mcu_update.os.path, "exists", return_value=True),
            mock.patch.object(mcu_update, "firmware_version",
                              return_value="upstream-version"),
            mock.patch.object(mcu_update, "klipper_state",
                              return_value=("ready", "")),
            mock.patch.object(mcu_update, "print_state", return_value="standby"),
            mock.patch.object(mcu_update, "mcu_version",
                              return_value="v1.0.6.3-18-g9d78b20"),
            mock.patch.object(mcu_update, "hardware_mismatches",
                              return_value=[]),
            mock.patch.object(mcu_update, "heading"),
            mock.patch.object(mcu_update, "say"),
            mock.patch.object(mcu_update, "ok"),
            mock.patch.object(mcu_update, "note"),
        )
        with patches[0], patches[1] as check_sums, patches[2], patches[3], \
                patches[4], patches[5], patches[6], patches[7], patches[8], \
                patches[9], patches[10], patches[11]:
            self.assertTrue(mcu_update.step_check(state))

        self.assertEqual(state["step"], "t1_backup")
        self.assertEqual(state["board_type"], mcu_update.T1_BOARD_TYPE)
        check_sums.assert_called_once_with(mcu_update.FIRMWARE_DIR)

    def test_detects_no_bootloader_flash_layout(self):
        dump = bytearray(b"\xff" * mcu_update.FLASH_SIZE)
        struct.pack_into("<II", dump, 0, 0x20010000, 0x08000101)
        self.assertTrue(mcu_update.is_t1_no_bootloader_backup(bytes(dump)))

        dump[mcu_update.APP_START] = 0
        self.assertFalse(mcu_update.is_t1_no_bootloader_backup(bytes(dump)))

    def test_finds_saved_t1_backup(self):
        dump = bytearray(b"\xff" * mcu_update.FLASH_SIZE)
        struct.pack_into("<II", dump, 0, 0x20010000, 0x08000101)
        for relative_path in (
                "t1-full.bin",
                os.path.join("20260101-000000", "mcu_flash_full_512k.bin")):
            with tempfile.TemporaryDirectory() as root:
                backup = os.path.join(root, relative_path)
                os.makedirs(os.path.dirname(backup), exist_ok=True)
                with open(backup, "wb") as stream:
                    stream.write(dump)
                with mock.patch.object(mcu_update, "BACKUP_ROOT", root):
                    found, found_dump = \
                        mcu_update.find_t1_no_bootloader_backup()
            self.assertEqual(found, backup)
            self.assertEqual(found_dump, bytes(dump))

    def test_writes_metadata_compatible_with_t1_flash(self):
        dump = bytearray(b"\xff" * mcu_update.FLASH_SIZE)
        struct.pack_into("<II", dump, 0, 0x20010000, 0x08000101)
        uid = bytes(range(12))
        options = bytes(range(16))
        with tempfile.TemporaryDirectory() as folder:
            backup = os.path.join(folder, "mcu_flash_full_512k.bin")
            with open(backup, "wb") as stream:
                stream.write(dump)
            with open(os.path.join(folder, "mcu_id.bin"), "wb") as stream:
                stream.write(b"\x00" * 8 + uid)
            with open(os.path.join(folder, "mcu_optionbytes.bin"),
                      "wb") as stream:
                stream.write(options)

            mcu_update.write_t1_backup_metadata(backup, bytes(dump))

            with open(backup + ".json", encoding="utf-8") as stream:
                metadata = json.load(stream)
            saved_dump, saved_metadata = t1_flash.read_backup(backup)
        self.assertEqual(metadata["flash_size"], mcu_update.FLASH_SIZE)
        self.assertEqual(metadata["mcu_uid"], uid.hex())
        self.assertEqual(metadata["option_bytes"], options.hex())
        self.assertEqual(metadata["sha256"], hashlib.sha256(dump).hexdigest())
        self.assertEqual(saved_dump, bytes(dump))
        self.assertEqual(saved_metadata, metadata)

    def test_backup_layout_routes_to_t1_installer(self):
        dump = bytearray(b"\xff" * mcu_update.FLASH_SIZE)
        struct.pack_into("<II", dump, 0, 0x20010000, 0x08000101)
        with tempfile.TemporaryDirectory() as root:
            state = {"from_version": "unrecognized-version"}

            def read_mock(command):
                path = command[-3]
                address = command[-2]
                if address == "0x08000000":
                    contents = dump
                elif address == "0x1FFFF800":
                    contents = b"\x22" * 0x10
                else:
                    contents = b"\x00" * 8 + bytes(range(12))
                with open(path, "wb") as stream:
                    stream.write(contents)
                return 0

            patches = (
                mock.patch.object(mcu_update, "ask", return_value=True),
                mock.patch.object(mcu_update, "BACKUP_ROOT", root),
                mock.patch.object(mcu_update.time, "strftime",
                                  return_value="backup-stamp"),
                mock.patch.object(mcu_update, "KlipperStopped",
                                  return_value=mock.MagicMock()),
                mock.patch.object(mcu_update, "run", side_effect=read_mock),
                mock.patch.object(mcu_update, "chown_tree"),
                mock.patch.object(mcu_update, "heading"),
                mock.patch.object(mcu_update, "say"),
                mock.patch.object(mcu_update, "note"),
            )
            with patches[0], patches[1], patches[2], patches[3], \
                    patches[4], patches[5], patches[6], patches[7], patches[8]:
                self.assertTrue(mcu_update.step_backup(state))

            self.assertEqual(state["step"], "t1_install")
            self.assertEqual(state["board_type"], mcu_update.T1_BOARD_TYPE)
            self.assertTrue(os.path.isfile(state["backup"] + ".json"))


class T1StepTests(unittest.TestCase):
    def quiet(self):
        return [mock.patch.object(mcu_update, f)
                for f in ("heading", "say", "ok", "note", "bad")]

    def test_t1_backup_checksums_and_hands_folder_to_pi(self):
        with tempfile.TemporaryDirectory() as root:
            def fake_backup(command):
                with open(command[-1], "wb") as stream:
                    stream.write(b"dump")
                with open(command[-1] + ".json", "w") as stream:
                    stream.write("{}")
                return 0
            state = {}
            with mock.patch.object(mcu_update, "BACKUP_ROOT", root), \
                    mock.patch.object(mcu_update, "ask", return_value=True), \
                    mock.patch.object(mcu_update.subprocess, "call",
                                      side_effect=fake_backup), \
                    mock.patch.object(mcu_update, "chown_tree") as chown:
                for p in self.quiet():
                    p.start()
                try:
                    self.assertTrue(mcu_update.step_t1_backup(state))
                finally:
                    mock.patch.stopall()
            chown.assert_called_once_with(root)
            folder = os.path.dirname(state["backup"])
            with open(os.path.join(folder, "SHA256SUMS")) as stream:
                sums = stream.read()
            self.assertIn("mcu_flash_full_512k.bin", sums)
            self.assertIn("mcu_flash_full_512k.bin.json", sums)
        self.assertEqual(state["step"], "t1_install")

    def test_t1_install_goes_on_to_the_shared_checks(self):
        state = {"backup": "/x/mcu_flash_full_512k.bin"}
        with mock.patch.object(mcu_update, "ask", return_value=True), \
                mock.patch.object(mcu_update.subprocess, "call",
                                  return_value=0) as call:
            for p in self.quiet():
                p.start()
            try:
                self.assertTrue(mcu_update.step_t1_install(state))
            finally:
                mock.patch.stopall()
        self.assertEqual(state["step"], "verify")
        self.assertIn("install", call.call_args[0][0])

    def test_failed_t1_install_stays_put(self):
        state = {"backup": "/x/b.bin", "step": "t1_install"}
        with mock.patch.object(mcu_update, "ask", return_value=True), \
                mock.patch.object(mcu_update.subprocess, "call",
                                  return_value=1):
            for p in self.quiet():
                p.start()
            try:
                self.assertFalse(mcu_update.step_t1_install(state))
            finally:
                mock.patch.stopall()
        self.assertEqual(state["step"], "t1_install")


if __name__ == "__main__":
    unittest.main()
