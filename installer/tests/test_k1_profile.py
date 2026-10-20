"""K1 identity and dynamic runtime state through actual installer entrypoints."""
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

TOP = Path(__file__).resolve().parents[2]
PROFILE = {'profile': 'k1', 'android_models': ['angelcare K1']}


class K1(unittest.TestCase):
    def adapter(self, slot='_a', encrypted=False, model='angelcare K1', board='ums9157_2h10'):
        adapter = routing.MethodRouting().adapter()
        properties = adapter.adb.properties()
        properties.update({'ro.product.model': model, 'ro.product.device': board,
                           'ro.boot.slot_suffix': slot,
                           'ro.crypto.state': 'encrypted' if encrypted else 'unencrypted',
                           'ro.crypto.type': 'file' if encrypted else ''})
        adapter.adb.properties = Mock(return_value=properties)
        adapter.legacy.package_profile.return_value = dict(PROFILE)
        adapter.package['profile'] = 'k1'
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

    def test_wrong_model_board_and_unknown_slot_stop_before_partition_reads(self):
        for args, message in (({'model': 'VIPME M8'}, '机型'),
                              ({'board': 'ums9158_1h10'}, 'K1'),
                              ({'slot': ''}, '槽位')):
            with self.subTest(args=args):
                adapter = self.adapter(**args)
                with self.assertRaisesRegex(ValueError, message):
                    adapter.probe(routing.SERIAL, 'part')
                adapter.read_gpt.assert_not_called()
                self.assertFalse(any(command.startswith('set -e; d=') for command in adapter.adb.commands))

    def test_cli_detects_both_slots_and_preserves_model_checks(self):
        checks = runpy.run_path(str(TOP / 'openwrt/bundle/flash.py'))['android_checks']
        for slot in ('_a', '_b'):
            properties = {'ro.product.model': 'angelcare K1', 'ro.product.device': 'ums9157_2h10',
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
                with self.assertRaisesRegex(ValueError, 'Wrong K1 board'):
                    checks()
                su.assert_not_called()

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
            self.assertEqual(package['profile'], 'k1')
            self.assertTrue(compatible(package, {**package, 'path': 'D:/moved'}))
            manifest.write_text(json.dumps({**PROFILE, 'android_models': ['VIPME M8']}))
            with self.assertRaisesRegex(ValueError, 'K1'):
                inspect_package(root)
            with self.assertRaisesRegex(ValueError, 'profile'):
                compatible(package, {**package, 'profile': 'm8'})


if __name__ == '__main__':
    unittest.main()
