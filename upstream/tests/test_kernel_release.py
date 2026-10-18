#!/usr/bin/env python3
"""Exercise the pinned kernel's real setlocalversion without building firmware.

Run on Linux with KERNEL_TREE pointing at the mainline kernel checkout.
All Git changes and config files are confined to a temporary fixture.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[2]
BUILDERS = (
    'upstream/build.sh', 'upstream/build-native.sh',
    'devices/e5/build-kernel.sh', 'devices/t551m/build-kernel.sh',
    'devices/m8/build-probe.sh', 'devices/t512n/build-kernel.sh',
)


class KernelRelease(unittest.TestCase):
    def test_shared_config(self):
        config = (TOP / 'upstream/e5-mainline.config').read_text().splitlines()
        self.assertIn('CONFIG_LOCALVERSION_AUTO=n', config)
        self.assertNotIn('CONFIG_LOCALVERSION_AUTO=y', config)

    def test_builder_environment(self):
        # Execute each actual preamble with a conflicting inherited suffix.
        for name in BUILDERS:
            with self.subTest(builder=name):
                source = (TOP / name).read_text()
                prefix = source[:source.index('export LOCALVERSION=')]
                subprocess.run(
                    ['bash', '-ec', prefix + 'export LOCALVERSION=\n'
                     '[ "${LOCALVERSION+set}" = set ] && [ -z "$LOCALVERSION" ]'],
                    env=dict(os.environ, LOCALVERSION='-inherited-dirty'), check=True)

    def test_real_setlocalversion(self):
        kernel = os.environ.get('KERNEL_TREE')
        if not kernel:
            self.skipTest('Set KERNEL_TREE to exercise the real kernel script')
        script = Path(kernel) / 'scripts/setlocalversion'
        self.assertTrue(script.is_file(), str(script))
        with tempfile.TemporaryDirectory(prefix='e5-kernel-release-') as tmp:
            root = Path(tmp)
            tree, obj = root / 'source', root / 'objects'
            tree.mkdir()
            (obj / 'include/config').mkdir(parents=True)
            shutil.copyfile(script, tree / 'setlocalversion')
            tracked = tree / 'driver.c'
            tracked.write_text('original\n')

            def git(*args):
                subprocess.run(['git', '-C', str(tree), *args], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

            git('init', '-q')
            git('add', '.')
            git('-c', 'user.name=Release test', '-c', 'user.email=test@example.invalid',
                'commit', '-qm', 'kernel fixture')
            env = dict(os.environ, KERNELVERSION='6.18.54')

            def release(auto, explicit=True):
                (obj / 'include/config/auto.conf').write_text(
                    'CONFIG_LOCALVERSION=' + suffix + '\n' +
                    ('CONFIG_LOCALVERSION_AUTO=y\n' if auto else ''))
                current_env = dict(env)
                current_env.pop('LOCALVERSION', None)
                if explicit:
                    current_env['LOCALVERSION'] = ''
                return subprocess.check_output(
                    ['sh', str(tree / 'setlocalversion'), str(tree)],
                    cwd=obj, env=current_env, text=True).strip()

            for patched in (False, True):
                if patched:
                    tracked.write_text('local driver fix\n')
                for suffix in ('-e5', '-e5-mobile4', '-t551m-otg10',
                               '-m8-probe12', '-t512n-bringup1'):
                    with self.subTest(patched=patched, suffix=suffix):
                        self.assertEqual(release(False), '6.18.54' + suffix)
                # Establish that this fixture really triggers the old SCM path.
                self.assertEqual(release(False, explicit=False), '6.18.54' + suffix + '+')
                if patched:
                    self.assertTrue(release(True).endswith('-dirty'))


if __name__ == '__main__':
    unittest.main()
