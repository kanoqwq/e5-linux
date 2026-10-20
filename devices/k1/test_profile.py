"""Offline K1 production-adapter checks; never connects to hardware."""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
TOP = HERE.parents[1]


def load(relative):
    return runpy.run_path(str(TOP / relative))


class Profile(unittest.TestCase):
    def test_config_preserves_platform_and_replaces_trial_settings(self):
        merge = load('devices/k1/build-kernel.py')['config']
        base = ('CONFIG_SPRD_UMS9620_CLK=y\nCONFIG_PINCTRL_SPRD_QOGIRN6PRO=y\n'
                'CONFIG_LOCALVERSION="-m8-old"\nCONFIG_CMDLINE="e5.openwrt=donor"\n'
                'CONFIG_SPRD_WLAN=m\n# CONFIG_UNUSED_TEST is not set\n')
        result = merge(base)
        self.assertIn('CONFIG_LOCALVERSION="-k1-bringup1"\n', result)
        self.assertIn('CONFIG_SPRD_WLAN=m\n', result)
        self.assertIn('CONFIG_UNUSED_TEST=n\n', result)
        self.assertNotIn('e5.openwrt=', result)
        self.assertEqual(result.count('CONFIG_CMDLINE='), 1)
        self.assertNotIn('lcd_id=', result)
        with self.assertRaises(ValueError):
            merge('CONFIG_SPRD_UMS9621_CLK=y\n')

    def test_no_fixed_slot_or_encryption_policy_in_profile(self):
        profile = json.loads((HERE / 'profile.json').read_text(encoding='utf-8'))
        self.assertEqual(profile['android_models'], ['angelcare K1'])
        self.assertEqual(profile['battery_max_voltage_uv'], 4380000)
        self.assertFalse({'android_slot', 'linux_slot', 'encrypted', 'encryption'} & profile.keys())

    def test_kernel_package_rejects_other_profile_before_copying(self):
        package = load('devices/m8/package-kernel.py')['package']
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory) / 'build'
            release = build / 'include/config/kernel.release'
            release.parent.mkdir(parents=True)
            release.write_text('6.18.54-m8-bringup1\n')
            out = Path(directory) / 'out'
            with self.assertRaisesRegex(ValueError, 'Wrong kernel profile'):
                package(build, out, profile='k1')
            self.assertFalse(out.exists())

    def test_case_distinct_modules_are_never_silently_overwritten(self):
        check = load('devices/m8/package-kernel.py')['check_output_names']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            if os.name == 'nt':
                with self.assertRaisesRegex(ValueError, 'case-sensitive'):
                    check(root, ['xt_RATEEST.ko', 'xt_rateest.ko'])
            else:
                check(root, ['xt_RATEEST.ko', 'xt_rateest.ko'])
            self.assertEqual(list(root.iterdir()), [])


@unittest.skipIf(os.name == 'nt', 'Linux staging and native C test; run under WSL')
class Native(unittest.TestCase):
    def test_actual_voltage_functions_and_m8_limit(self):
        # Compile the functions emitted by the real port, with only I2C mocked.
        # Expected REG0E values come from the stock modules' disassembly.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for profile, requested, expected in (('m8', 4250, 4248), ('k1', 4380, 4376)):
                kernel = root / profile
                supply = kernel / 'drivers/power/supply'
                supply.mkdir(parents=True)
                source = supply / 'bq2560x-charger.c'
                shutil.copyfile(TOP / 'devices/m8/port/drivers/power/supply/bq2560x-charger.c', source)
                for name in ('Kconfig', 'Makefile'):
                    (supply / name).write_text('')
                load('devices/' + profile + '/charger.py')['apply'](kernel)
                text = source.read_text()
                functions = []
                for name in ('set', 'get'):
                    pattern = (r'static int\s+bq2560x_charger_' + name +
                               r'_termina_vol\([^)]*\)\s*\{.*?^\}')
                    functions.append(re.search(pattern, text, re.M | re.S).group())
                harness = r'''
#include <assert.h>
#include <stdint.h>
typedef uint8_t u8;
typedef uint32_t u32;
struct bq2560x_charger_info { u8 value; int fail; };
#define clamp_val(v,l,h) ((v)<(l)?(l):((v)>(h)?(h):(v)))
static int bq2560x_read(struct bq2560x_charger_info *i, u8 r, u8 *v) {
    assert(r == 0x0e); if (i->fail) return i->fail; *v=i->value; return 0;
}
static int bq2560x_update_bits(struct bq2560x_charger_info *i, u8 r, u8 m, u8 v) {
    assert(r == 0x0e && m == 0xfe); if (i->fail) return i->fail;
    i->value=(i->value & ~m) | (v & m); return 0;
}
''' + '\n'.join(functions) + r'''
int main(void) {
    struct bq2560x_charger_info i = {1, 0}; u32 voltage=0;
    assert(bq2560x_charger_set_termina_vol(&i, REQUESTED) == 0);
    assert((i.value & 1) == 1);
    assert(bq2560x_charger_get_termina_vol(&i, &voltage) == 0 && voltage == EXPECTED);
    assert(bq2560x_charger_set_termina_vol(&i, 5000) == 0);
    assert(bq2560x_charger_get_termina_vol(&i, &voltage) == 0 && voltage == EXPECTED);
    assert(bq2560x_charger_set_termina_vol(&i, 1) == 0);
    assert(bq2560x_charger_get_termina_vol(&i, &voltage) == 0 && voltage == 3856);
    i.fail=-5; voltage=999;
    assert(bq2560x_charger_get_termina_vol(&i, &voltage) == -5 && voltage == 999);
    assert(bq2560x_charger_set_termina_vol(&i, REQUESTED) == -5);
    return 0;
}
'''
                cfile = kernel / 'test.c'
                cfile.write_text(harness)
                binary = kernel / 'test'
                subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                                '-DREQUESTED=' + str(requested), '-DEXPECTED=' + str(expected),
                                str(cfile), '-o', str(binary)], check=True)
                subprocess.run([str(binary)], check=True)

    def test_real_k1_userspace_stage_and_preserved_user_settings(self):
        ui = load('devices/t551m/ui.py')['stage']
        real_run = runpy.run_path
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'root'
            for name in ('etc/init.d/e5-vendor', 'etc/init.d/e5-sipc-wwan',
                         'usr/libexec/e5-infoscreen/session', 'usr/share/rpcd/ucode/e5-infoscreen.uc'):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('offline base fixture\n')
            for name in ('e5-bt', 'e5-sipc-wwan'):
                (root / 'etc/init.d' / name).write_bytes(
                    (TOP / 'openwrt/overlay/etc/init.d' / name).read_bytes())
            client = Path(directory) / 'at-client'
            client.write_bytes(b'offline compiled-client fixture')

            def staged(path, *args, **kwargs):
                path = Path(path)
                if path.name == 'ui.py' and path.parent.name == 't551m':
                    return {'stage': ui}
                if path.name == 'build-at-client.py':
                    return {'build': lambda: client}
                return real_run(str(path), *args, **kwargs)

            # Package fetching and AT compilation are separate verified inputs;
            # all profile, NV-isolation, dashboard and screen staging runs here.
            with patch.dict(ui.__globals__, PACKAGES={}), \
                 patch.object(runpy, 'run_path', side_effect=staged), \
                 patch.dict(os.environ, E5_UI_CACHE=str(Path(directory) / 'cache')):
                stage = real_run(str(HERE / 'stage.py'))['stage']
                stage(root)
                config = root / 'etc/config/unisoc_modem'
                self.assertNotIn('lte_supported_bands', config.read_text())
                config.write_text(config.read_text() + "\nconfig private 'user'\n option note 'Keep me'\n")
                stage(root)
                self.assertIn('Keep me', config.read_text())
                self.assertEqual((root / 'etc/e5/device-profile').read_text().strip(), 'k1')
                self.assertEqual((root / 'etc/e5/device-hostname').read_text().strip(), 'Angelcare-K1')
                self.assertFalse((root / 'etc/e5/call-low-volume-attenuation-db').exists())
                self.assertEqual((root / 'etc/e5/receiver-wiring').read_text(), 'aol\n')
                self.assertFalse((root / 'etc/e5/receiver-output').exists())
                self.assertTrue((root / 'etc/rc.d/S13e5-cpu').is_symlink())
                self.assertTrue((root / 'etc/rc.d/S22e5-modemd').is_symlink())
                self.assertTrue((root / 'usr/libexec/m8-bluetooth-address').is_file())
                with self.assertRaisesRegex(ValueError, 'convert'):
                    real_run(str(TOP / 'devices/m8/stage.py'))['stage'](root)

    def test_k1_bluetooth_uses_only_own_android_adapter(self):
        prepare = load('devices/m8/bluetooth-address.py')['prepare']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = {'etc/e5/device-profile': b'k1\n',
                    'lib/firmware/sprd/marlin3lite_pskey.bin': bytes(176),
                    'mnt/e5-data/misc/bluedroid/bt_config.conf':
                        b'[Adapter]\nAddress=40:45:da:12:34:56\n[Paired]\nLinkKey=never-copy\n'}
            for name, value in data.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(value)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(prepare(root))
                self.assertFalse(prepare(root))
            result = (root / 'lib/firmware/sprd/marlin3lite_pskey.bin').read_bytes()
            self.assertEqual(result[20:26], bytes.fromhex('563412da4540'))
            self.assertEqual(result[:20] + result[26:], bytes(170))


if __name__ == '__main__':
    unittest.main()
