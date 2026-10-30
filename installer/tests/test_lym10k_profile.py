"""LYM-10K identity and dynamic runtime state through actual installer entrypoints."""
import contextlib
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_method_routing as routing
from backend.package import inspect_package
from backend.service import plan_install
from backend.state import compatible, sha

from firmware_sources import firmware_root
TOP = firmware_root('e5')
PROFILE = {'profile': 'lym10k', 'android_models': ['LYM-10K']}


class LYM10K(unittest.TestCase):
    def adapter(self, slot='_a', encrypted=False, model='LYM-10K', board='RY5G'):
        adapter = routing.MethodRouting().adapter()
        properties = adapter.adb.properties()
        properties.update({'ro.product.model': model, 'ro.product.device': board,
                           'ro.boot.slot_suffix': slot,
                           'ro.crypto.state': 'encrypted' if encrypted else 'unencrypted',
                           'ro.crypto.type': 'file' if encrypted else ''})
        adapter.adb.properties = Mock(return_value=properties)
        adapter.legacy.package_profile.return_value = dict(PROFILE)
        adapter.package['profile'] = 'lym10k'
        return adapter

    def test_gui_current_slot_and_encryption_are_runtime_state(self):
        for slot in ('_a', '_b'):
            for encrypted in (False, True):
                with self.subTest(slot=slot, encrypted=encrypted):
                    adapter = self.adapter(slot, encrypted)
                    device = adapter.probe(routing.SERIAL, 'part')
                    self.assertEqual(device['slot'], slot)
                    self.assertEqual(device['encrypted'], encrypted)
                    self.assertEqual(adapter.legacy.ANDROID_SLOT, slot[1:])
                    self.assertEqual(adapter.legacy.LINUX_SLOT, 'b' if slot == '_a' else 'a')
                    # Ignore the existing root in the read-only fixture to test
                    # new-partition planning, including reset vs preservation.
                    device['existing'] = False
                    plan = plan_install(adapter.package, device, {'method': 'part', 'size_gib': 4})
                    self.assertEqual(plan['two_stage'], encrypted)
                    self.assertEqual(plan['wipe'], encrypted)

    def test_model_board_warn_but_unknown_slot_still_stops(self):
        for args in ({'model': 'VIPME M8'}, {'board': 'ums9158_1h10'}):
            adapter = self.adapter(**args)
            self.assertIn('model_mismatch', adapter.probe(routing.SERIAL, 'part'))
            self.assertFalse(any(' of=' in command for command in adapter.adb.commands))
        for args, message in (({'slot': ''}, '槽位'),):
            with self.subTest(args=args):
                adapter = self.adapter(**args)
                with self.assertRaisesRegex(ValueError, message):
                    adapter.probe(routing.SERIAL, 'part')
                adapter.read_gpt.assert_not_called()
                self.assertFalse(any(command.startswith('set -e; d=') for command in adapter.adb.commands))

    def test_cli_detects_both_slots_and_preserves_model_checks(self):
        checks = runpy.run_path(str(TOP / 'openwrt/bundle/flash.py'))['android_checks']
        for slot in ('_a', '_b'):
            properties = {'ro.product.model': 'LYM-10K', 'ro.product.device': 'RY5G',
                          'ro.boot.slot_suffix': slot, 'ro.boot.verifiedbootstate': 'orange'}
            def shell(command):
                if command == 'id -u':
                    return '0'
                return properties[command.removeprefix('getprop ')]
            su = Mock(return_value='ok')
            with self.subTest(slot=slot), patch.dict(checks.__globals__,
                    SERIAL='fixture', GUI=object(),
                    adb=Mock(return_value='List of devices attached\nfixture\tdevice\n'),
                    sh=shell, su=su, android_tools=Mock(),
                    package_profile=Mock(return_value=dict(PROFILE))), \
                    contextlib.redirect_stdout(io.StringIO()):
                checks()
                linux = 'b' if slot == '_a' else 'a'
                self.assertEqual(checks.__globals__['ANDROID_SLOT'], slot[1:])
                self.assertEqual(checks.__globals__['LINUX_SLOT'], linux)
                su.assert_called_once_with(f'[ -e /dev/block/by-name/boot_{linux} ] && [ -e /dev/block/by-name/l_agdsp_{slot[1:]} ] && echo ok')
                properties['ro.product.device'] = 'wrong-board'
                su.reset_mock()
                with patch.dict(checks.__globals__, ask=Mock(return_value='no')):
                    with self.assertRaisesRegex(ValueError, 'Model exception declined'):
                        checks()
                su.assert_not_called()
                with patch.dict(checks.__globals__, ask=Mock(return_value='yes')):
                    checks()
                su.assert_called_once()

    def test_package_identity_allows_moving_and_rejects_m8_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'files').mkdir()
            manifest = root / 'files/device-profile.json'
            manifest.write_text(json.dumps(PROFILE))
            (root / 'OpenWrt-Installer.py').write_text('# offline fixture\n')
            names = ['files/boot.img', 'files/openwrt.ext4.gz', 'files/ext4-tools.tar']
            for name in names:
                (root / name).write_bytes(b'test payload: ' + name.encode())
            (root / 'files/SHA256SUMS').write_text(''.join(sha(root / name) + '  ' + name + '\n' for name in names))
            package = inspect_package(root)
            self.assertEqual(package['profile'], 'lym10k')
            self.assertTrue(compatible(package, {**package, 'path': 'D:/moved'}))
            manifest.write_text(json.dumps({**PROFILE, 'android_models': ['VIPME M8']}))
            # Model-name differences are reviewed by typed consent, not a package allowlist.
            self.assertEqual(inspect_package(root)['profile'], 'lym10k')
            from backend.model_compat import android_warning
            self.assertIsNotNone(android_warning({**PROFILE,'android_models':['VIPME M8']},'VIPME M8','m8-board'))
            with self.assertRaisesRegex(ValueError, 'profile'):
                compatible(package, {**package, 'profile': 'm8'})


if __name__ == '__main__':
    unittest.main()
