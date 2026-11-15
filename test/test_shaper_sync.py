#!/usr/bin/env python3
"""Tests for the input shaper sync of scripts/flsun_klipper_cfg.py (sync_shaper):
a calibration made on FLSun's Klipper reaches printer_upstream.cfg on the next
start of upstream Klipper, and one made on upstream Klipper isn't undone.

    python3 test/test_shaper_sync.py
"""

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK = "#*# <---------------------- SAVE_CONFIG ---------------------->"


def block(*sections):
    out = [MARK, "#*# DO NOT EDIT THIS BLOCK OR BELOW. The contents are auto-generated.", "#*#"]
    for name, opts in sections:
        out.append("#*# [%s]" % name)
        out += ["#*# %s = %s" % kv for kv in opts]
        out.append("#*#")
    return "\n".join(out) + "\n"


def shaper(fx, fy, t="mzv"):
    return ("input_shaper", [("shaper_type_x", t), ("shaper_freq_x", fx),
                             ("shaper_type_y", t), ("shaper_freq_y", fy)])


PROBE = ("probe", [("z_offset", "20.1")])
MESH = ("bed_mesh default", [("version", "1"), ("points", "\n#*# 0.1, 0.2")])


class ShaperSync(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cfg = os.path.join(self.dir, "config")
        os.mkdir(self.cfg)
        os.environ["FLSUN_KLIPPER_CONFIG_DIR"] = self.cfg
        os.environ["FLSUN_KLIPPER_STOCK_SHAPER_FILE"] = os.path.join(self.dir, "seen")
        spec = importlib.util.spec_from_file_location(
            "cfg", os.path.join(REPO, "scripts", "flsun_klipper_cfg.py"))
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def put(self, name, text, age=0):
        path = os.path.join(self.cfg, name)
        with open(path, "w") as f:
            f.write(text)
        t = os.path.getmtime(path) - age
        os.utime(path, (t, t))

    def upstream(self):
        with open(self.m.UPSTREAM_CFG) as f:
            return f.read()

    def freqs(self):
        return self.m.shaper_values(self.m.split_saved(self.upstream())[1])

    def test_new_stock_calibration_is_copied(self):
        self.put("printer.cfg", "[printer]\n" + block(shaper("40.4", "39.8"), PROBE))
        self.put("printer_upstream.cfg", "[include x.cfg]\n" + block(shaper("40.4", "39.8"), PROBE, MESH))
        self.m.sync_shaper()                      # records, nothing to copy
        self.put("printer.cfg", "[printer]\n" + block(PROBE, shaper("55.2", "48.0", "ei")))
        self.m.sync_shaper()
        self.assertEqual(self.freqs(), "shaper_type_x = ei\nshaper_freq_x = 55.2\n"
                         "shaper_type_y = ei\nshaper_freq_y = 48.0")
        text = self.upstream()
        self.assertIn("#*# [probe]", text)
        self.assertIn("#*# 0.1, 0.2", text)       # the mesh survived
        self.assertTrue(text.startswith("[include x.cfg]\n"))
        # Every line of the block still starts with #*#.
        self.assertEqual(self.m.repair_saved(self.m.split_saved(text)[1])[1], 0)
        self.assertTrue([f for f in os.listdir(self.cfg) if ".bak-" in f])

    def test_upstream_calibration_is_kept(self):
        self.put("printer.cfg", "[printer]\n" + block(shaper("40.4", "39.8")))
        self.put("printer_upstream.cfg", "[x]\n" + block(shaper("40.4", "39.8")))
        self.m.sync_shaper()
        self.put("printer_upstream.cfg", "[x]\n" + block(shaper("61.0", "58.0")))
        self.put("printer.cfg", "[printer]\n[include y.cfg]\n" + block(shaper("40.4", "39.8")))
        self.m.sync_shaper()                      # printer.cfg changed, its shaper didn't
        self.assertIn("61.0", self.freqs())

    def test_first_run_keeps_upstream(self):
        # Even when printer.cfg is newer (a plugin installer added an include).
        self.put("printer_upstream.cfg", "[x]\n" + block(shaper("61.0", "58.0")), age=100)
        self.put("printer.cfg", "[printer]\n" + block(shaper("40.4", "39.8")))
        self.m.sync_shaper()
        self.assertIn("61.0", self.freqs())
        self.put("printer.cfg", "[printer]\n" + block(shaper("41.0", "39.8")))
        self.m.sync_shaper()                      # a later stock calibration wins
        self.assertIn("41.0", self.freqs())

    def test_added_to_block_without_shaper(self):
        self.put("printer.cfg", "[printer]\n" + block(shaper("40.4", "39.8")))
        self.put("printer_upstream.cfg", "[x]\n" + block(PROBE), age=100)
        self.m.sync_shaper()
        self.assertIn("40.4", self.freqs())
        self.assertIn("#*# z_offset = 20.1", self.upstream())

    def test_added_to_file_without_block(self):
        self.put("printer.cfg", "[printer]\n" + block(shaper("40.4", "39.8")))
        self.put("printer_upstream.cfg", "[x]\n", age=100)
        self.m.sync_shaper()
        self.assertIn("40.4", self.freqs())


if __name__ == "__main__":
    sys.exit(unittest.main())
