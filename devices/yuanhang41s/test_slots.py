#!/usr/bin/env python3
"""Execute staged slot-selection shell fragments in isolated temporary paths."""
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[2]


class Slots(unittest.TestCase):
    def test_staged_aliases_and_loader_inputs(self):
        stage = runpy.run_path(str(TOP / 'devices/e5/stage.py'))['stage_nv']
        for slot in ('a', 'b', 'current'):
            for original in ('a', 'b', None, 'android-only', 'sprd-only', 'late'):
                with self.subTest(slot=slot, original=original), tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    (root / 'etc/init.d').mkdir(parents=True)
                    (root / 'opt/e5').mkdir(parents=True)
                    (root / 'etc/init.d/e5-vendor').write_bytes(
                        (TOP / 'openwrt/overlay/etc/init.d/e5-vendor').read_bytes())
                    (root / 'opt/e5/vendor-start.sh').write_bytes(
                        (TOP / 'rootfs/overlay/opt/e5/vendor-start.sh').read_bytes())
                    stage(root)
                    text = (root / 'opt/e5/vendor-start.sh').read_text()
                    start = text.index('case "$modem_slot" in')
                    end = text.index('/opt/e5/node-perms.sh', start)
                    alias = text[start:end]
                    start = text.index('src=/proc/cmdline')
                    end = text.index('chmod 0666', start)
                    cmdline = text[start:end]
                    start = text.index('if [ "$modem_slot" != "current" ] && [ -r /proc/bootconfig ]; then')
                    end = text.index('\nfi', start) + 3
                    bootconfig = text[start:end]
                    for name in ('nr_modem', 'nr_phy', 'nr_fixnv1', 'nr_deltanv'):
                        for suffix in ('a', 'b'):
                            (root / (name + '_' + suffix)).write_text(suffix)
                    source = {
                        None: 'quiet', 'android-only': 'androidboot.slot_suffix=_b',
                        'sprd-only': 'sprdboot.slot_suffix=_a',
                        'late': 'quiet ' + 'x' * 1500 + ' androidboot.slot_suffix=_a',
                    }.get(original, 'androidboot.slot_suffix=_' + str(original) + ' sprdboot.slot_suffix=_' + str(original))
                    (root / 'cmdline').write_text(source)
                    (root / 'bootconfig').write_text('androidboot.slot_suffix = "_' + (original if original in ('a', 'b') else 'b') + '"\n')
                    code = '\n'.join((alias, cmdline, bootconfig))
                    for before, after in (
                        ('/dev/block/by-name', temp),
                        ('/proc/device-tree/chosen/bootargs', temp + '/absent'),
                        ('/proc/cmdline', temp + '/cmdline'),
                        ('/proc/bootconfig', temp + '/bootconfig'),
                        ('/run/e5-cmdline.android', temp + '/result-cmdline'),
                        ('/run/e5-cmdline.without-slot', temp + '/without-slot'),
                        ('/run/e5-bootconfig.android', temp + '/result-bootconfig'),
                    ):
                        code = code.replace(before, after)
                    # Never perform bind mounts on the host during this test.
                    code = 'mountpoint(){ return 0; }; mount(){ return 91; };\n' + code
                    subprocess.run(['sh', '-eu', '-c', code], check=True,
                                   env={**os.environ, 'modem_slot': slot, 'A': temp},
                                   stdout=subprocess.DEVNULL)
                    actual = (root / 'result-cmdline').read_text()
                    if slot == 'current':
                        self.assertEqual(actual, source)
                        self.assertFalse((root / 'result-bootconfig').exists())
                    else:
                        self.assertIn('androidboot.slot_suffix=_' + slot, actual)
                        self.assertIn('sprdboot.slot_suffix=_' + slot, actual)
                        self.assertIn('sprdboot.slot_suffix=_' + slot, actual[:128])
                        self.assertEqual(actual.count('androidboot.slot_suffix='), 1)
                        self.assertEqual(actual.count('sprdboot.slot_suffix='), 1)
                        self.assertEqual((root / 'result-bootconfig').read_text(),
                                         'androidboot.slot_suffix = "_' + slot + '"\n')
                    for name in ('nr_modem', 'nr_phy'):
                        self.assertEqual((root / (name + '_a')).read_text(),
                                         'a' if slot == 'current' else slot)
                        self.assertEqual((root / (name + '_b')).read_text(),
                                         'b' if slot == 'current' else slot)
                    for name in ('nr_fixnv1', 'nr_deltanv'):
                        for suffix in ('a', 'b'):
                            self.assertFalse((root / (name + '_' + suffix)).is_symlink())
                            self.assertEqual((root / (name + '_' + suffix)).read_text(), suffix)


if __name__ == '__main__':
    unittest.main()
