"""Bounded probe identity gates and actual module dependency resolution."""
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[2]
probe = runpy.run_path(str(TOP / 'devices/common/bringup-probe.py'))


class Probe(unittest.TestCase):
    def test_both_slots_use_only_the_linux_partition_for_persistence(self):
        for android, linux in (('a', 'b'), ('b', 'a')):
            with self.subTest(android=android):
                text = probe['probe_init']('b' * 32, 'mmcblk0', android, linux, 90, 'K1', '192.168.29.1')
                self.assertIn('PARTNAME=boot_' + linux, text)
                self.assertNotIn('PARTNAME=boot_' + android, text)
                self.assertIn('probe_verified:-0', text)
                self.assertLess(text.index('identity mismatch'), text.index('dd if=/etc/misc-bc'))
                self.assertLess(text.index('cmp /tmp/probe-bc.bin'), text.index('probe_verified=1'))
                self.assertIn('sleep 180;', text)
                self.assertIn('ifconfig usb0 192.168.29.1', text)
                self.assertNotIn('192.168.9.1', text)
                if os.name != 'nt':
                    result = subprocess.run(['sh', '-n'], input=text, text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_identity_and_slot_roles_are_rejected(self):
        for cid, disk, android, linux in (('bad', 'mmcblk0', 'b', 'a'),
                                         ('b' * 32, '../misc', 'b', 'a'),
                                         ('b' * 32, 'mmcblk0', 'b', 'b')):
            with self.assertRaises(ValueError):
                probe['probe_init'](cid, disk, android, linux, 90, 'K1', '192.168.29.1')

    def test_dependencies_precede_consumers_and_mixed_release_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def module(name, depends, release='6.18-k1-test'):
                (root / (name + '.ko')).write_bytes(
                    b'\0vermagic=' + release.encode() + b' SMP\0depends=' + depends.encode() + b'\0')
            module('consumer', 'supplier')
            module('supplier', '')
            self.assertEqual(probe['module_order'](root, ['consumer.ko', 'supplier.ko'], '6.18-k1-test'),
                             ['supplier.ko', 'consumer.ko'])
            module('supplier', '', '6.18-m8-test')
            with self.assertRaisesRegex(ValueError, 'cohort'):
                probe['module_order'](root, ['consumer.ko'], '6.18-k1-test')
            module('supplier', 'consumer')
            with self.assertRaisesRegex(ValueError, 'cyclic'):
                probe['module_order'](root, ['consumer.ko'], '6.18-k1-test')

    @unittest.skipIf(os.name == 'nt', 'Requires a POSIX shell')
    def test_identity_failure_cannot_write_boot_control_or_persistent_logs(self):
        text = probe['probe_init']('b' * 32, 'mmcblk0', 'b', 'a', 90, 'K1', '192.168.29.1')
        prep = text.split('probe_verified=0\n', 1)[1].split('log "uptime ', 1)[0]
        stub = '''
sleep() { :; }
cat() { echo wrong-device; }
log() { echo "$*"; }
reboot() { echo REBOOT; }
dd() { echo FORBIDDEN_WRITE; }
'''
        result = subprocess.run(['sh', '-c', stub + prep], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('identity mismatch', result.stdout)
        self.assertNotIn('FORBIDDEN_WRITE', result.stdout)
        persist = text.split('persist() {', 1)[1].split('\n}', 1)[0]
        result = subprocess.run(['sh', '-c', stub + 'persist() {' + persist + '\n}\nprobe_verified=0\npersist'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('FORBIDDEN_WRITE', result.stdout)


if __name__ == '__main__':
    unittest.main()
