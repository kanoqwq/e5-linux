"""Hardware-independent power sequencing regression tests."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('power', Path(__file__).with_name('infoscreen') / 'screen-power.py')
power = importlib.util.module_from_spec(spec)
spec.loader.exec_module(power)


class ScreenPowerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bl = self.root / 'class/backlight/panel/brightness'
        self.touch = self.root / 'class/input/input7/inhibited'
        for p in [self.bl, self.touch]:
            p.parent.mkdir(parents=True)
            p.write_text('0\n')
        (self.touch.parent / 'name').write_text('tlsc6x_touch\n')
        (self.touch.parent / 'properties').write_text('2\n')
        self.sys = patch.object(power, 'SYS', self.root)
        self.lock = patch.object(power, 'LOCK', self.root / 'power.lock')
        self.sys.start(); self.lock.start()

    def tearDown(self):
        self.sys.stop(); self.lock.stop(); self.tmp.cleanup()

    def test_dark_inhibits_and_wake_restores(self):
        for _ in range(5):
            power.reconcile(0, True)
            self.assertEqual(self.touch.read_text().strip(), '1')
            power.reconcile(180, True)
            self.assertEqual(self.touch.read_text().strip(), '0')
            self.assertEqual(self.bl.read_text().strip(), '180')

    def test_direct_panel_discovery_is_independent_of_driver_name(self):
        for name in ('tlsc6x_touch', 'fts_ts', 'generic ft5x06 (79)'):
            (self.touch.parent / 'name').write_text(name + '\n')
            self.assertEqual(power.devices()[1], [self.touch])
        # An indirect pointing device is not a panel, even with the donor name.
        (self.touch.parent / 'properties').write_text('0\n')
        with self.assertRaises(RuntimeError):
            power.devices()

    def test_explicit_touch_off_survives_wake(self):
        power.reconcile(0, False)
        power.reconcile(255, False)
        self.assertEqual(self.touch.read_text().strip(), '1')
        power.reconcile(allowed=True)
        self.assertEqual(self.touch.read_text().strip(), '0')

    def test_external_backlight_and_service_restart_reconcile(self):
        self.bl.write_text('0\n'); self.touch.write_text('0\n')
        power.reconcile(allowed=True)
        self.assertEqual(self.touch.read_text().strip(), '1')
        self.bl.write_text('120\n')
        power.reconcile(allowed=True)
        self.assertEqual(self.touch.read_text().strip(), '0')

    def test_inhibit_precedes_backlight_write(self):
        events=[]
        original=Path.write_text
        def track(path, text, *args, **kwargs):
            events.append((path.name,text.strip()))
            return original(path,text,*args,**kwargs)
        with patch.object(Path, 'write_text', track):
            power.reconcile(0, True)
            power.reconcile(255, True)
        self.assertEqual(events,[('inhibited','1'),('brightness','0'),('inhibited','0'),('brightness','255')])

    def test_cached_paths_still_repair_external_changes(self):
        paths = power.devices()
        with patch.object(power, 'devices', side_effect=AssertionError('rescanned')):
            for level in [0, 180, 0, 255]:
                self.bl.write_text(str(level))
                self.touch.write_text('0' if level == 0 else '1')
                power.reconcile(allowed=True, paths=paths)
                self.assertEqual(self.touch.read_text().strip(), '1' if level == 0 else '0')

    def test_config_cache_observes_commits_unsaved_deltas_and_removal(self):
        config, delta = self.root/'config', self.root/'delta'
        config.write_text('touch=1')
        setting = power.TouchSetting()
        with patch.object(power, 'CONFIG', (config, delta)), \
                patch.object(power, 'touch_allowed', side_effect=[True, False, True, False]) as load:
            self.assertTrue(setting.get())
            for _ in range(100): self.assertTrue(setting.get())
            self.assertEqual(load.call_count, 1)
            delta.write_text('touch=0')
            self.assertFalse(setting.get())
            delta.unlink()
            self.assertTrue(setting.get())
            replacement = self.root/'new'
            replacement.write_text('touch=0'); replacement.replace(config)
            self.assertFalse(setting.get())
            self.assertEqual(load.call_count, 4)


if __name__ == '__main__':
    unittest.main()
