#!/usr/bin/env python3
"""FLSun's own mainboard firmware is v0.10.0-439-gdfa5afdf on the T1 Pro and
v1.0.6.3-18-g9d78b20 on the regular T1. Both must count as FLSun's: no
adxl.cfg, and switch_klipper.sh may switch to FLSun's Klipper.

    python3 test/test_stock_versions.py
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOCK = ["v0.10.0-439-gdfa5afdf", "v1.0.6.3-18-g9d78b20", "v1.0.6.3"]
FLASHED = ["v0.13.0-816-gee2c6fb6b", "v1.0.6.30-1-gabc", "v0.10.0-439-gother"]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StockVersions(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ["FLSUN_KLIPPER_CONFIG_DIR"] = self.dir
        self.version_file = os.path.join(self.dir, "mcu_version")
        os.environ["FLSUN_KLIPPER_MCU_VERSION_FILE"] = self.version_file
        self.cfg = load("cfg", os.path.join(REPO, "scripts", "flsun_klipper_cfg.py"))
        self.ext = load("ext", os.path.join(REPO, "extras", "t1_mcu_version.py"))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_is_stock(self):
        for module in (self.cfg, self.ext):
            for v in STOCK:
                self.assertTrue(module.is_stock(v), v)
            for v in FLASHED:
                self.assertFalse(module.is_stock(v), v)

    def test_adxl_only_when_flashed(self):
        for v in STOCK + FLASHED:
            with open(self.version_file, "w") as f:
                f.write(v + "\n")
            with mock.patch.object(self.cfg, "cmd_numpy"):
                self.cfg.cmd_includes()
            with open(self.cfg.INCLUDES_CFG) as f:
                self.assertEqual("adxl.cfg" in f.read(), v in FLASHED, v)

    def identify(self, old, new):
        """(rewrote the includes, the RESTART warning) for a change old -> new."""
        if old is None:
            if os.path.exists(self.version_file):
                os.remove(self.version_file)
        else:
            with open(self.version_file, "w") as f:
                f.write(old + "\n")
        warnings = []
        mcu = types.SimpleNamespace(get_status=lambda: {"mcu_version": new})
        configfile = types.SimpleNamespace(runtime_warning=warnings.append)
        printer = types.SimpleNamespace(
            lookup_object=lambda n: mcu if n == "mcu" else configfile,
            register_event_handler=lambda *a: None)
        config = types.SimpleNamespace(
            get_printer=lambda: printer, get=lambda k, d: self.version_file)
        with mock.patch.object(self.ext.subprocess, "run") as run:
            self.ext.T1McuVersion(config)._identify()
        return run.called, warnings

    def test_identify_restarts_only_across_stock_and_flashed(self):
        t1, pro, new = "v1.0.6.3-18-g9d78b20", "v0.10.0-439-gdfa5afdf", FLASHED[0]
        self.assertEqual(self.identify(None, t1), (False, []))
        self.assertEqual(self.identify(pro, t1), (False, []))
        ran, warnings = self.identify(t1, new)
        self.assertTrue(ran)
        self.assertIn("adds the ADXL345", warnings[0])
        ran, warnings = self.identify(new, t1)
        self.assertTrue(ran)
        self.assertIn("removes the ADXL345", warnings[0])

    def test_switch_klipper_is_stock(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("no bash")
        script = os.path.join(REPO, "scripts", "switch_klipper.sh")
        # The variables and is_stock() from the script, without running it.
        src = open(script, encoding="utf-8").read()
        defs = "\n".join(l for l in src.split("\n") if l.startswith("STOCK_"))
        func = src[src.index("is_stock() {"):src.index("refuse_if_flashed() {")]
        for v in STOCK + FLASHED:
            rc = subprocess.call([bash, "-c", defs + "\n" + func + '\nis_stock "$0"', v])
            self.assertEqual(rc == 0, v in STOCK, v)


if __name__ == "__main__":
    sys.exit(unittest.main())
