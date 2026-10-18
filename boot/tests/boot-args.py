"""Actual installer argument parser, including forced command lines; no devices."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[2]


class BootArguments(unittest.TestCase):
    def read_arg(self, proc, dt, key):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'cmdline').write_text(proc)
            (root/'bootargs').write_bytes(dt)
            code = (TOP/'boot/boot-args.sh').read_text().replace(
                '/proc/device-tree/chosen/bootargs', str(root/'bootargs')).replace(
                '/proc/cmdline', str(root/'cmdline'))
            shell = ['busybox', 'ash'] if shutil.which('busybox') else ['sh']
            return subprocess.run([*shell, '-c', code+'\nboot_arg "$1"', 'fixture', key],
                                  capture_output=True, text=True)

    def test_fixed_kernel_line_uses_chosen_request(self):
        r = self.read_arg('console=tty0 rdinit=/init', b'console=ttyS1 e5.part_prepare=token:cid:plan\0', 'e5.part_prepare')
        self.assertEqual((r.returncode, r.stdout.strip()), (0, 'token:cid:plan'))

    def test_explicit_kernel_value_wins(self):
        r = self.read_arg('e5.root=part', b'e5.root=other\0', 'e5.root')
        self.assertEqual((r.returncode, r.stdout.strip()), (0, 'part'))

    def test_duplicates_do_not_fall_back_or_select_a_plan(self):
        for proc, dt in [('e5.part_prepare=a e5.part_prepare=b', b'e5.part_prepare=c\0'),
                         ('console=tty0', b'e5.part_prepare=a e5.part_prepare=b\0')]:
            r = self.read_arg(proc, dt, 'e5.part_prepare')
            self.assertEqual((r.returncode, r.stdout), (2, ''))

    def test_absent_similar_or_unrelated_options_do_not_match(self):
        for key in ('e5.part_prepare', 'init'):
            r = self.read_arg('init=/init', b'prepare=bad xe5.part_prepare=bad\0', key)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(r.stdout, '')

    def test_t512n_lk_twenty_byte_offset_preserves_full_install_keys(self):
        prefix = 'e5.lkpad=0000000000 '
        self.assertEqual(len(prefix), 20)
        for option in ('e5.part_prepare=token:cid:plan', 'e5.root=part'):
            forwarded = (prefix+'e5.part_v=2 '+option)[20:].encode()+b'\0'
            key, value = option.split('=', 1)
            r = self.read_arg('console=tty0', forwarded, key)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, value))


if __name__ == '__main__':
    unittest.main()
