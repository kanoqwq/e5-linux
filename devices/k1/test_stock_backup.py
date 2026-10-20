"""Run the real recovery collector against temporary files and a fake ADB."""
import contextlib
import gzip
import io
import json
from pathlib import Path
import runpy
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

TOP = Path(__file__).resolve().parents[2]
backup = runpy.run_path(str(TOP / 'devices/common/stock-backup.py'))
PROFILE = json.loads((TOP / 'devices/k1/profile.json').read_text(encoding='utf-8'))


class Adb:
    serial = 'test-selected-k1'
    call = None

    def __init__(self, slot='b'):
        self.slot = slot
        self.model = 'angelcare K1'
        self.commands = []

    def root_status(self):
        return 'ready'

    def shell(self, command, root=False):
        self.commands.append(command)
        if command.startswith('getprop '):
            return {'ro.product.model': self.model, 'ro.product.device': 'ums9157_2h10',
                    'ro.boot.slot_suffix': '_' + self.slot, 'ro.boot.verifiedbootstate': 'orange',
                    'ro.build.display.id': 'K1-stock-fixture', 'ro.build.fingerprint': 'fixture'}[command[8:]]
        if command.startswith('readlink -f /dev/block/by-name/boot_'):
            return '/dev/block/mmcblk0p37'
        if command == 'cat /sys/class/block/mmcblk0/device/cid':
            return 'b' * 32
        if command.startswith('test -b /dev/block/by-name/'):
            return '0' if command.split('/')[-1].split(';')[0] in ('boot_a', 'boot_b') else '1'
        if command.startswith('blockdev --getsize64 '):
            return '4096'
        raise AssertionError(command)

    def read(self, command):
        self.commands.append(command)
        if command == 'cat /sys/firmware/fdt':
            return struct.pack('>II', 0xd00dfeed, 40) + bytes(32)
        if command == 'cat /proc/config.gz':
            return gzip.compress(b'CONFIG_TEST=y\n')
        if command.startswith('dd if=/dev/block/by-name/misc '):
            control = bytearray(32)
            control[:10] = b'_' + self.slot.encode() + b'\0\0BCAB\x01\x02'
            control[12 if self.slot == 'a' else 14] = 0x9f
            control[28:] = struct.pack('<I', zlib.crc32(control[:28]))
            return bytes(2048) + bytes(control) + bytes(2016)
        raise AssertionError(command)


class StockBackup(unittest.TestCase):
    def test_both_slots_verified_without_partition_writes(self):
        for slot in ('a', 'b'):
            with self.subTest(slot=slot), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                adb = Adb(slot)
                def pull(call, shell, command, path, expected_size=None):
                    self.assertIn(command, ('dd if=/dev/block/by-name/boot_a bs=4M 2>/dev/null',
                                            'dd if=/dev/block/by-name/boot_b bs=4M 2>/dev/null'))
                    Path(path).write_bytes(b't' * expected_size)
                with patch.dict(backup['collect'].__globals__, pull_binary=pull), contextlib.redirect_stdout(io.StringIO()):
                    result = backup['collect'](adb, root / 'backup', PROFILE, root / 'locks')
                self.assertTrue(result['complete'])
                self.assertEqual(result['android_slot'], slot)
                self.assertEqual(backup['verify'](root / 'backup', 'k1'), result)
                self.assertFalse(any(' of=' in cmd or 'nv' in cmd or 'reboot' in cmd for cmd in adb.commands))
                (root / 'backup/boot_a.img').write_bytes(b'corrupt')
                with self.assertRaisesRegex(ValueError, 'changed'):
                    backup['verify'](root / 'backup', 'k1')

    def test_model_mismatch_creates_no_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            adb = Adb()
            adb.model = 'VIPME M8'
            target = Path(directory) / 'backup'
            with self.assertRaisesRegex(ValueError, 'model/board'):
                backup['collect'](adb, target, PROFILE)
            self.assertFalse(target.exists())

    def test_disconnect_keeps_incomplete_backup_unusable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(backup['collect'].__globals__, pull_binary=lambda *a, **k: (_ for _ in ()).throw(ConnectionError())), \
                    contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ConnectionError):
                backup['collect'](Adb(), root / 'backup', PROFILE, root / 'locks')
            with self.assertRaisesRegex(ValueError, 'Incomplete'):
                backup['verify'](root / 'backup', 'k1')


if __name__ == '__main__':
    unittest.main()
