"""Exercise real deployment boundaries with simulated ADB and regular files."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import re
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
TOP = HERE.parents[1]
sys.path.insert(0, str(HERE))
import test_stock_backup as fixture

deploy = runpy.run_path(str(TOP / 'devices/common/probe-deploy.py'))
boot = runpy.run_path(str(TOP / 'boot/build-boot-image.py'))


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Adb(fixture.Adb):
    def __init__(self):
        super().__init__()
        self.remote = {}
        self.hashes = {}
        self.fail = None
        self.live = None

    def shell(self, command, root=False, timeout=20):
        self.commands.append(command)
        if command.startswith('getprop ') or command.startswith('cat /sys/class/block/mmcblk0/device/cid'):
            return super().shell(command, root)
        if command.startswith('test -b '):
            return super().shell(command, root)
        if command.startswith('readlink -f '):
            return '/dev/block/mmcblk0p' + ('36' if command.endswith('a') else '37')
        if command.startswith('cat /sys/class/block/mmcblk0p'):
            return 'PARTNAME=boot_' + ('a' if 'p36/' in command else 'b')
        if command.startswith('cat /sys/class/power_supply/battery/'):
            return {'capacity': '65', 'voltage_now': '4000000', 'status': 'Charging'}[command.split('/')[-1]]
        if command.startswith('sha256sum '):
            source = command[10:]
            return (self.hashes[source.split('/')[-1]] if source.startswith('/dev/block/') else digest(self.remote[source])) + '  file'
        if command.startswith('dd if=/dev/block/by-name/boot_a bs=1M count=56'):
            return self.hashes['boot_a'] + '  -'
        if command.startswith('blockdev --getsize64 '):
            return '4096'
        if command.startswith('umask 077; mkdir ') or command.startswith('chown -R '):
            return ''
        if '/bootchain.sh check' in command:
            return 'BOOTCHAIN-PLAN b a uboot sml teecfg trustos vbmeta'
        if '/bootchain.sh sync' in command:
            if self.fail == 'chain':
                return 'incomplete'
            return 'BOOTCHAIN-OK b a uboot sml teecfg trustos vbmeta'
        if ' of=/dev/block/by-name/boot_a ' in command:
            source = re.search(r'if=([^ ]+)', command)[1]
            self.hashes['boot_a'] = '0' * 64 if self.fail == 'boot' else digest(self.remote[source])
            return ''
        if ' of=/dev/block/by-name/misc ' in command:
            source = re.search(r'if=([^ ]+)', command)[1]
            self.live = self.live[:2048] + self.remote[source] + self.live[2080:]
            if self.fail == 'misc' and source.endswith('trial.bin'):
                self.live = self.live[:2048] + bytes(32) + self.live[2080:]
            return ''
        raise AssertionError('Unexpected command: ' + command)

    def read(self, command):
        if self.live is not None and 'by-name/misc' in command:
            return self.live
        return super().read(command)

    def call(self, *args, **kwargs):
        self.commands.append('ADB ' + ' '.join(args))
        if args[0] == 'push':
            self.remote[args[2]] = Path(args[1]).read_bytes()
            return ''
        if args[0] == 'reboot':
            raise ConnectionError('expected reboot disconnect')
        raise AssertionError(args)


class Deployment(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backup = self.root / 'backup'
        self.adb = Adb()
        def pull(call, shell, command, destination, expected_size=None):
            Path(destination).write_bytes(b't' * expected_size)
        with patch.dict(fixture.backup['collect'].__globals__, pull_binary=pull), contextlib.redirect_stdout(io.StringIO()):
            stock = fixture.backup['collect'](self.adb, self.backup, fixture.PROFILE, self.root / 'locks')
        for name in ('uboot', 'sml', 'teecfg', 'trustos', 'vbmeta'):
            for slot in ('a', 'b'):
                file = name + '_' + slot + '.img'
                data = file.encode()
                (self.backup / file).write_bytes(data)
                stock['files'][file] = {'bytes': len(data), 'sha256': digest(data)}
        (self.backup / 'manifest.json').write_text(json.dumps(stock))
        self.adb.hashes = {name.removesuffix('.img'): info['sha256']
                           for name, info in stock['files'].items() if name.endswith('.img')}
        self.adb.live = (self.backup / 'misc-head.bin').read_bytes()
        self.image = self.root / 'probe.img'
        self.image.write_bytes(b'i' * 4096)
        self.image.with_suffix('.init').write_bytes(b'test-only init')
        self.busybox = self.root / 'busybox'
        self.busybox.write_bytes(b'test-only BusyBox fixture')
        fallback, trial = boot['bootloader_control'](self.adb.live, 'b')
        info = {'profile': 'k1', 'probe': True, 'android_slot': 'b', 'linux_slot': 'a',
                'cid_sha256': stock['files']['cid.txt']['sha256'], 'sha256': digest(self.image.read_bytes()),
                'init_sha256': digest(self.image.with_suffix('.init').read_bytes()),
                'busybox_sha256': digest(self.busybox.read_bytes()),
                'misc_slot_a_hex': fallback.hex(), 'misc_slot_b_trial_hex': trial.hex()}
        self.image.with_suffix('.json').write_text(json.dumps(info))
        self.adb.commands = []

    def execute(self, **kwargs):
        return deploy['execute'](self.adb, self.backup, self.image, self.busybox,
                                 fixture.PROFILE, lock_base=self.root / 'locks', **kwargs)

    def writes(self):
        return [command for command in self.adb.commands if ' of=/dev/block/by-name/' in command]

    def test_preflight_has_no_partition_writes_and_protects_android_b(self):
        plan = deploy['preflight'](self.adb, self.backup, self.image, fixture.PROFILE)
        self.assertEqual(plan['boot'], 'boot_a')
        self.assertEqual(plan['android_slot'], 'b')
        self.assertEqual(plan['chain'], ['uboot', 'sml', 'teecfg', 'trustos', 'vbmeta'])
        self.assertEqual(self.writes(), [])
        self.assertFalse(self.image.with_suffix('.deployment.json').exists())

    def test_changed_device_slot_and_boot_alias_stop_before_writes(self):
        actual = self.adb.shell
        for command, wrong in (('getprop ro.boot.slot_suffix', '_a'),
                               ('cat /sys/class/block/mmcblk0/device/cid', 'c' * 32),
                               ('readlink -f /dev/block/by-name/boot_a', '/dev/block/mmcblk0p37')):
            def changed(value, **kwargs):
                return wrong if value == command else actual(value, **kwargs)
            with self.subTest(command=command), patch.object(self.adb, 'shell', side_effect=changed), self.assertRaises(ValueError):
                deploy['preflight'](self.adb, self.backup, self.image, fixture.PROFILE)
            self.assertEqual(self.writes(), [])

    def test_preflight_also_protects_android_a_and_targets_boot_b(self):
        self.adb.slot = 'a'
        self.adb.live = fixture.Adb('a').read('dd if=/dev/block/by-name/misc bs=4096 count=1 2>/dev/null')
        (self.backup / 'misc-head.bin').write_bytes(self.adb.live)
        stock = json.loads((self.backup / 'manifest.json').read_text())
        stock.update(android_slot='a', linux_slot='b')
        stock['files']['misc-head.bin']['sha256'] = digest(self.adb.live)
        (self.backup / 'manifest.json').write_text(json.dumps(stock))
        fallback, trial = boot['bootloader_control'](self.adb.live, 'a')
        info = json.loads(self.image.with_suffix('.json').read_text())
        info.update(android_slot='a', linux_slot='b', misc_slot_a_hex=fallback.hex(), misc_slot_b_trial_hex=trial.hex())
        self.image.with_suffix('.json').write_text(json.dumps(info))
        plan = deploy['preflight'](self.adb, self.backup, self.image, fixture.PROFILE)
        self.assertEqual(plan['boot'], 'boot_b')
        self.assertEqual(plan['android_slot'], 'a')
        self.assertEqual(self.writes(), [])

    def test_chain_then_verified_boot_then_misc_and_expected_reboot_disconnect(self):
        plan = self.execute(reboot=True)
        self.assertEqual(plan['status'], 'reboot_requested')
        writes = self.writes()
        self.assertEqual(len(writes), 2)
        self.assertIn('of=/dev/block/by-name/boot_a ', writes[0])
        self.assertIn('of=/dev/block/by-name/misc ', writes[1])
        self.assertIn('seek=2048', writes[1])
        self.assertFalse(any('of=/dev/block/by-name/boot_b' in c or 'userdata' in c for c in writes))
        self.assertLess(next(i for i, c in enumerate(self.adb.commands) if '/bootchain.sh sync' in c),
                        self.adb.commands.index(writes[0]))
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.execute()

    def test_chain_failure_cannot_write_boot_or_misc(self):
        self.adb.fail = 'chain'
        with self.assertRaisesRegex(ValueError, 'did not complete'):
            self.execute()
        self.assertEqual(self.writes(), [])

    def test_known_previous_probe_allows_only_its_head_and_verified_synced_chain(self):
        info = json.loads(self.image.with_suffix('.json').read_text())
        info['sha256_head56m'] = info['sha256']
        self.image.with_suffix('.json').write_text(json.dumps(info))
        self.adb.hashes['boot_a'] = info['sha256']
        for name in ('uboot', 'sml', 'teecfg', 'trustos', 'vbmeta'):
            self.adb.hashes[name + '_a'] = self.adb.hashes[name + '_b']
        self.adb.live = self.adb.live[:2048] + bytes.fromhex(info['misc_slot_a_hex']) + self.adb.live[2080:]
        plan = deploy['preflight'](self.adb, self.backup, self.image, fixture.PROFILE, previous_image=self.image)
        self.assertEqual(plan['chain'], [])
        self.assertEqual(plan['original_hex'], info['misc_slot_a_hex'])
        self.adb.hashes['boot_a'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'partition changed'):
            deploy['preflight'](self.adb, self.backup, self.image, fixture.PROFILE, previous_image=self.image)
        self.assertEqual(self.writes(), [])

    def test_boot_readback_failure_cannot_arm_misc(self):
        self.adb.fail = 'boot'
        with self.assertRaisesRegex(ValueError, 'readback'):
            self.execute()
        self.assertEqual(len(self.writes()), 1)

    def test_misc_failure_restores_original_control_and_never_reboots(self):
        original = self.adb.live[2048:2080]
        self.adb.fail = 'misc'
        with self.assertRaisesRegex(ValueError, 'original Android boot control restored'):
            self.execute(reboot=True)
        self.assertEqual(self.adb.live[2048:2080], original)
        self.assertNotIn('ADB reboot', self.adb.commands)


if __name__ == '__main__':
    unittest.main()
