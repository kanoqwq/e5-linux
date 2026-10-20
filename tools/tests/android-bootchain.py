#!/usr/bin/env python3
"""Run the real transaction against regular files, never connected devices.

Only device paths and block/identity probes are substituted in a private copy.
Test both POSIX sh and Android/Magisk's BusyBox ash when installed.
"""
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib

TOP = Path(__file__).resolve().parents[2]
PARTS = ('uboot', 'sml', 'teecfg', 'trustos', 'vbmeta')
SHELLS = [['sh']]
if shutil.which('busybox'):
    SHELLS.append(['busybox', 'sh'])


class Transaction(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.by = self.root / 'by-name'
        self.by.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.backups = self.root / 'backups'
        self.lock = self.root / 'lock'
        self.props = self.root / 'props'
        self.props.write_text('ro.boot.slot_suffix=_a\nro.boot.verifiedbootstate=orange\n')
        self.stub('getprop', f'sed -n "s/^$1=//p" "{self.props}"')
        self.stub('id', 'echo 0')
        self.stub('blockdev', 'case $1 in --getsize64) wc -c < "$2" ;; --flushbufs) : ;; *) exit 2 ;; esac')
        self.stub('sync', ':')
        self.stub('dd', f'exec {shutil.which("dd")} "$@"')
        for name in ('boot_a', 'boot_b', 'l_agdsp_a', 'l_agdsp_b', 'nr_fixnv1', 'nr_runtimenv1', 'userdata'):
            (self.by / name).write_bytes(os.urandom(128))
        for part in PARTS:
            for slot in 'ab':
                (self.by / f'{part}_{slot}').write_bytes((part + slot).encode().ljust(8192, b'!'))
        self.misc()
        code = (TOP / 'tools/android-bootchain.sh').read_text()
        code = code.replace('BY=/dev/block/by-name', f'BY={self.by}')
        code = code.replace('BACKUPS=/data/adb/e5-bootchain', f'BACKUPS={self.backups}')
        code = code.replace('LOCK=/data/local/tmp/e5-bootchain.lock', f'LOCK={self.lock}')
        code = code.replace('[ -b ', '[ -f ')
        # BusyBox may prefer built-in applets even with our PATH prepended.
        code = code.replace('$(id -u)', '0')
        for command in ('blockdev', 'dd'):
            import re
            code = re.sub(r'\b' + command + r'(?=[ ;])', str(self.bin / command), code)
        self.script = self.root / 'script'
        self.script.write_text(code)
        self.original = self.snapshot()

    def stub(self, name, code):
        path = self.bin / name
        path.write_text('#!/bin/sh\n' + code + '\n')
        path.chmod(0o755)

    def misc(self, a=0x9f, b=0x1e, crc=True):
        block = bytearray(32)
        block[:10] = b'_a\0\0BCAB\x01\x02'
        block[12], block[14] = a, b
        block[16:28] = os.urandom(12)
        block[28:] = struct.pack('<I', zlib.crc32(block[:28]) if crc else 0)
        (self.by / 'misc').write_bytes(b'X' * 2048 + block + b'Y' * 2016)

    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.by.iterdir()}

    def run_script(self, mode, shell=None):
        return subprocess.run([*(shell or ['sh']), str(self.script), mode], text=True,
                              capture_output=True, timeout=30,
                              env={**os.environ, 'PATH': str(self.bin) + ':' + os.environ['PATH']})

    def test_check_is_read_only_on_both_shells(self):
        for shell in SHELLS:
            r = self.run_script('check', shell)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('BOOTCHAIN-PLAN a b uboot sml teecfg trustos vbmeta', r.stdout)
            self.assertEqual(self.snapshot(), self.original)
            self.assertFalse(self.backups.exists())
            self.assertFalse(self.lock.exists())

    def test_sync_backup_integrity_and_repeat_noop(self):
        for shell in SHELLS:
            for name, data in self.original.items():
                (self.by / name).write_bytes(data)
            r = self.run_script('sync', shell)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            for name, data in self.snapshot().items():
                want = self.original[name[:-1] + 'a'] if name in {p + '_b' for p in PARTS} else self.original[name]
                self.assertEqual(data, want, name)
            for backup in self.backups.iterdir():
                for part in PARTS:
                    for slot in 'ab':
                        path = backup / f'{part}_{slot}.img'
                        self.assertEqual(path.read_bytes(), self.original[f'{part}_{slot}'])
                        self.assertEqual(path.with_suffix('.sha256').read_text().strip(), hashlib.sha256(path.read_bytes()).hexdigest())
            directories = set(self.backups.iterdir())
            self.assertEqual(self.run_script('sync', shell).returncode, 0)
            self.assertEqual(set(self.backups.iterdir()), directories)

    def refused(self):
        before = self.snapshot()
        for mode in ('check', 'sync'):
            result = self.run_script(mode)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.backups.exists())

    def test_bad_crc(self):
        self.misc(crc=False)
        self.refused()

    def test_pending_ota(self):
        self.misc(a=0x9e, b=0x1f)
        self.refused()

    def test_unsuccessful_android(self):
        self.misc(a=0x1f)
        self.refused()

    def test_android_b_check_uses_opposite_chain_without_writes(self):
        self.props.write_text('ro.boot.slot_suffix=_b\nro.boot.verifiedbootstate=orange\n')
        block=bytearray((self.by/'misc').read_bytes())
        block[2048:2050]=b'_b';block[2060],block[2062]=0x1e,0x9f
        struct.pack_into('<I',block,2076,zlib.crc32(block[2048:2076]))
        (self.by/'misc').write_bytes(block);before=self.snapshot()
        for shell in SHELLS:
            result=self.run_script('check',shell)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('BOOTCHAIN-PLAN b a',result.stdout)
            self.assertEqual(self.snapshot(),before)

    def test_41s_tested_green_firmware_requires_exact_board(self):
        self.props.write_text('ro.product.model=ZTE 7546N\nro.product.device=P720S11\nro.boot.slot_suffix=_a\nro.boot.verifiedbootstate=green\n')
        self.assertEqual(self.run_script('check').returncode,0)
        self.assertEqual(self.snapshot(),self.original)
        self.props.write_text(self.props.read_text().replace('P720S11','other'))
        self.refused()

    def test_locked_bootloader(self):
        self.props.write_text('ro.boot.slot_suffix=_a\nro.boot.verifiedbootstate=green\n')
        self.refused()

    def test_t512n_engineering_green_check_is_read_only(self):
        self.props.write_text('ro.product.model=T512N\nro.boot.slot_suffix=_a\nro.boot.verifiedbootstate=green\n')
        for shell in SHELLS:
            result = self.run_script('check', shell)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.snapshot(), self.original)
            self.assertFalse(self.backups.exists())

    def test_e5_green_still_refused(self):
        self.props.write_text('ro.product.model=E5\nro.boot.slot_suffix=_a\nro.boot.verifiedbootstate=green\n')
        self.refused()

    def test_partition_size_mismatch(self):
        (self.by / 'sml_b').write_bytes(b'short')
        self.refused()

    def test_alias_into_nv_refused(self):
        (self.by / 'sml_b').unlink()
        (self.by / 'sml_b').symlink_to(self.by / 'nr_fixnv1')
        self.refused()

    def test_missing_partition(self):
        (self.by / 'trustos_b').unlink()
        self.refused()

    def test_lock_not_stolen(self):
        self.lock.mkdir()
        self.refused()
        self.assertTrue(self.lock.is_dir())

    def test_backup_failure_never_writes_partitions(self):
        real_dd = shutil.which('dd')
        self.stub('dd', f'case "$*" in *"of={self.backups}"*) exit 1 ;; esac\nexec {real_dd} "$@"')
        self.assertNotEqual(self.run_script('sync').returncode, 0)
        self.assertEqual(self.snapshot(), self.original)

    def inject_failure(self, permanent=False, signal=False):
        real_dd = shutil.which('dd')
        marker = self.root / 'failed'
        # Fail the second partition after the first has been written; damage
        # that partition before reporting error (not just a no-op failure).
        guard = ':' if permanent else f'[ ! -e {marker} ]'
        finish = 'kill -TERM "$PPID"; exit 1' if signal else 'exit 1'
        self.stub('dd', f'''case "$*" in
 *"of={self.by}/sml_b"*)
   if {guard}; then touch {marker}; printf BROKEN > {self.by}/sml_b; {finish}; fi ;;
esac
exec {real_dd} "$@"''')

    def test_partial_write_restores_all_changed_partitions(self):
        self.inject_failure()
        r = self.run_script('sync')
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('restored and verified', r.stdout)
        self.assertEqual(self.snapshot(), self.original)

    def test_signal_rolls_back(self):
        self.inject_failure(signal=True)
        r = self.run_script('sync')
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.snapshot(), self.original)

    def test_failed_rollback_keeps_backup_and_reports(self):
        self.inject_failure(permanent=True)
        r = self.run_script('sync')
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn('DO NOT boot Linux', r.stderr)
        self.assertTrue(list(self.backups.glob('*/sml_b.img')))
        self.assertEqual((self.by / 'uboot_b').read_bytes(), self.original['uboot_b'])


if __name__ == '__main__':
    unittest.main()
