"""Exercise RAM trial guards and real cohort resolution without hardware."""
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[2]
helpers = runpy.run_path(str(TOP / 'devices/k1/prepare-ram-root.py'))
stage = helpers['stage_ram_modules']


class RamProbe(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'RAM staging requires Linux')
    def test_offline_root_with_saved_nv_is_rejected_before_firmware_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / 'etc/e5/device-profile'
            marker.parent.mkdir(parents=True)
            marker.write_text('k1\n')
            store = root / 'opt/e5/nv'
            store.mkdir(parents=True)
            (store / 'foreign-working-copy').write_text('never import')
            output = root / 'payload.tar.gz'
            with self.assertRaisesRegex(ValueError, 'stored NV'):
                helpers['prepare'](root, root / 'absent-kernel', root / 'absent-firmware', output, root / 'absent-backup')
            self.assertFalse(output.exists())

    def test_lab_dhcp_selects_this_probes_host_mac_without_a_gateway(self):
        import hashlib
        cid = 'b' * 32
        tag = hashlib.sha256(cid.encode()).hexdigest()
        mac = '02:51:' + ':'.join(tag[i:i + 2] for i in range(0, 8, 2))
        config = helpers['lab_dhcp'](cid)
        self.assertIn("option mac '" + mac + "'", config)
        self.assertIn("option broadcast '1'", config)
        self.assertIn("list dhcp_option '3'\n", config)
        self.assertNotIn(cid, config)
        with self.assertRaises(ValueError):
            helpers['lab_dhcp']('../bad')

    def test_full_cohort_required_and_dependency_order_retained(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'root'
            kernel = Path(temporary) / 'kernel'
            for name in ('etc/e5', 'etc/init.d', 'etc/rc.d', 'usr/libexec'):
                (root / name).mkdir(parents=True)
            (kernel / 'modules').mkdir(parents=True)
            (kernel / 'kernel.release').write_text('6.18-k1-test\n')
            names = ['panfrost.ko', 'sprd_modem_loader.ko', 'spipe.ko',
                     'wcn_bsp.ko', 'sprd_wlan_combo.ko']
            for name in names + ['dependency.ko']:
                depends = b'dependency' if name == 'panfrost.ko' else b''
                (kernel / 'modules' / name).write_bytes(b'\0vermagic=6.18-k1-test SMP\0depends=' + depends + b'\0')
            (kernel / 'module-order.txt').write_text('panfrost.ko\n')
            with self.assertRaisesRegex(ValueError, 'Incomplete'):
                stage(root, kernel)
            self.assertFalse((root / 'etc/rc.d/S07e5-ram-modules').exists())
            (kernel / 'module-order.txt').write_text('\n'.join(names))
            (kernel / 'modules/dependency.ko').write_bytes(b'\0vermagic=6.18-m8-test SMP\0depends=\0')
            with self.assertRaisesRegex(ValueError, 'cohort'):
                stage(root, kernel)
            (kernel / 'modules/dependency.ko').write_bytes(b'\0vermagic=6.18-k1-test SMP\0depends=\0')
            if os.name == 'nt':
                return  # Symlink/service execution is tested on Linux below.
            stage(root, kernel)
            stage(root, kernel)
            ordered = (root / 'etc/e5/ram-module-order').read_text().split()
            self.assertEqual(ordered[:2], ['dependency.ko', 'panfrost.ko'])
            self.assertTrue((root / 'etc/rc.d/S07e5-ram-modules').is_symlink())

    @unittest.skipIf(os.name == 'nt', 'Requires POSIX shell')
    def test_runtime_gates_protect_nv_and_fail_before_module_load(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = {'etc/e5/ram-probe': 'k1', 'etc/e5/device-profile': 'k1',
                     'etc/e5/ram-kernel-release': '6.18-k1-test',
                     'etc/e5/ram-module-order': 'driver.ko\n',
                     'lib/firmware/wcnmodem.bin': 'own', 'lib/firmware/gnssmodem.bin': 'own',
                     'lib/modules/6.18-k1-test/driver.ko': 'module'}
            for name, data in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(data)
            script = (TOP / 'devices/common/probe-ram-modules.sh').read_text()
            for prefix in ('/etc/e5', '/lib/firmware', '/lib/modules', '/sys/module'):
                script = script.replace(prefix, str(root) + prefix)
            script = script.replace('/opt/e5/t551m-nv-prepare', 'fake_nv')
            script = script.replace('/usr/libexec/e5-ram-probe --once', 'fake_persist')
            stubs = 'uname() { echo 6.18-k1-test; }; logger() { :; }; sync() { :; }; reboot() { echo REBOOT; }; insmod() { echo LOAD; }; fake_persist() { echo PERSIST; }; '

            def run(nv_ok=True):
                return subprocess.run(['sh', '-c', stubs +
                    'fake_nv() { echo NV_PROTECTED; return ' + ('0' if nv_ok else '1') + '; };\n' + script],
                    capture_output=True, text=True)

            result = run()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertLess(result.stdout.index('NV_PROTECTED'), result.stdout.index('LOAD'))
            result = run(False)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('LOAD', result.stdout)
            (root / 'etc/e5/device-profile').write_text('m8')
            result = run()
            self.assertNotIn('NV_PROTECTED', result.stdout)
            self.assertNotIn('LOAD', result.stdout)
            (root / 'etc/e5/device-profile').write_text('k1')
            (root / 'lib/firmware/gnssmodem.bin').unlink()
            result = run()
            self.assertNotIn('NV_PROTECTED', result.stdout)
            self.assertNotIn('LOAD', result.stdout)


if __name__ == '__main__':
    unittest.main()
