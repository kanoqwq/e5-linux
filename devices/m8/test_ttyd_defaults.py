"""Recover a missing packaged config without changing explicit ttyd choices."""
from pathlib import Path
import runpy
import tempfile
import unittest

ensure = runpy.run_path(str(Path(__file__).with_name('prepare-root.py')))['ensure_ttyd_config']


class TtydDefaultsTest(unittest.TestCase):
    def test_installed_package_missing_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'usr/bin').mkdir(parents=True)
            (root / 'usr/bin/ttyd').touch()
            ensure(root)
            config = root / 'etc/config/ttyd'
            self.assertIn("interface '@lan'", config.read_text())
            self.assertIn("command '/bin/login'", config.read_text())
            self.assertNotIn('/bin/sh', config.read_text())

    def test_explicit_configuration_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'usr/bin').mkdir(parents=True)
            (root / 'usr/bin/ttyd').touch()
            config = root / 'etc/config/ttyd'
            config.parent.mkdir(parents=True)
            original = "config ttyd\n option enable '0'\n option port '9000'\n"
            config.write_text(original)
            ensure(root)
            self.assertEqual(config.read_text(), original)

    def test_uninstalled_package_stays_unconfigured(self):
        with tempfile.TemporaryDirectory() as directory:
            ensure(directory)
            self.assertFalse((Path(directory) / 'etc/config/ttyd').exists())


if __name__ == '__main__':
    unittest.main()
