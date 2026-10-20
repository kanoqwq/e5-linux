"""Device-free A/B binding, actual LZ4/newc decoding and installer uploads."""
import hashlib
import importlib.util
import json
from pathlib import Path
import runpy
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

TOP = Path(__file__).resolve().parents[2]
api = runpy.run_path(str(TOP/'openwrt/bundle/boot-slots.py'))
spec = importlib.util.spec_from_file_location('flash_slots', TOP/'openwrt/bundle/flash.py')
flash = importlib.util.module_from_spec(spec); spec.loader.exec_module(flash)
update = runpy.run_path(str(TOP/'tools/e5-update.py'))


def control(slot):
    data = bytearray(32); data[:10] = ('_'+slot).encode()+b'\0\0BCAB\x01\x02'
    data[12], data[14] = (0x9f, 0x1e) if slot == 'a' else (0x1e, 0x9f)
    struct.pack_into('<I', data, 28, zlib.crc32(data[:28]))
    return bytes(data)


def boot():
    data = bytearray(65536); data[:8] = b'ANDROID!'
    struct.pack_into('<II', data, 8, 4096, 32)
    struct.pack_into('<I', data, 20, 1584); struct.pack_into('<I', data, 40, 4)
    data[44:1580] = b'e5.part_v=2'.ljust(1536, b'\0')
    data[4096:8192] = b'K'*4096
    data[8192:8224] = b'R'*32
    data[12288:12416] = b'V'*128
    data[-64:-60] = b'AVBf'
    struct.pack_into('>QQQ', data, len(data)-52, 12288, 12288, 128)
    return bytes(data)


def unpack_roles(result):
    import lz4.block
    ramdisk_size = struct.unpack_from('<I', result, 12)[0]
    frame = result[8224:8192+ramdisk_size]
    assert frame[:4] == bytes.fromhex('02214c18')
    size = struct.unpack_from('<I', frame, 4)[0]
    raw = lz4.block.decompress(frame[8:8+size], uncompressed_size=8 << 20)
    at = 0; files = {}; seen = set()
    while at < len(raw):
        assert raw[at:at+6] == b'070701'
        fields = [int(raw[at+6+i*8:at+14+i*8], 16) for i in range(13)]
        size, namesize = fields[6], fields[11]
        name = raw[at+110:at+110+namesize-1].decode()
        start = (at+110+namesize+3)&~3
        if name == 'TRAILER!!!': break
        if '/' in name: assert name.rsplit('/', 1)[0] in seen
        seen.add(name)
        if stat.S_ISREG(fields[1]): files[name] = raw[start:start+size]
        at = (start+size+3)&~3
    return files


class Slots(unittest.TestCase):
    def test_update_binds_the_installed_roles_and_rejects_conflicting_metadata(self):
        for android, linux in [('a','b'), ('b','a')]:
            fallback, trial = api['role_files'](control(android),android), None
            trial = fallback['etc/e5linux/misc-bc-slot-b-trial.bin']
            manifest = {'sha256':hashlib.sha256(boot()).hexdigest(),'slot_roles_version':1}
            result = update['bind_update_boot'](boot(),manifest,control(android),trial,api)
            self.assertEqual(unpack_roles(result)['etc/e5/linux-slot'],(linux+'\n').encode())
            for bad in (control(android), trial[:-1], trial[:28]+b'xxxx'):
                with self.assertRaises(ValueError):
                    update['bind_update_boot'](boot(),manifest,control(android),bad,api)
            with self.assertRaises(ValueError):
                update['bind_update_boot'](boot(),dict(manifest,sha256='0'*64),control(android),trial,api)
            if android == 'b':
                with self.assertRaises(ValueError):
                    update['bind_update_boot'](boot(),dict(manifest,slot_roles_version=0),control(android),trial,api)

    def test_android_detection_accepts_both_slots_and_requires_matching_firmware(self):
        for android, linux in [('a','b'), ('b','a')]:
            def shell(command):
                return {'id -u':'0', 'getprop ro.product.model':'T551M',
                        'getprop ro.boot.slot_suffix':'_'+android,
                        'getprop ro.boot.verifiedbootstate':'orange'}[command]
            with self.subTest(android=android), patch.multiple(flash, SERIAL='unit', GUI=None,
                     ANDROID_SLOT=None, LINUX_SLOT=None), \
                 patch.object(flash,'adb',return_value='List of devices attached\nunit\tdevice\n'), \
                 patch.object(flash,'sh',side_effect=shell), patch.object(flash,'su',return_value='ok') as su, \
                 patch.object(flash,'android_tools'), patch.object(flash,'package_profile',return_value={'profile':'t551m','android_models':['T551M']}):
                flash.android_checks()
                self.assertEqual(flash.current_slots(),(android,linux))
                self.assertIn('boot_'+linux,su.call_args.args[0])
                self.assertIn('l_agdsp_'+android,su.call_args.args[0])

    def test_both_layouts_preserve_kernel_original_ramdisk_and_avb(self):
        for android, linux in [('a','b'), ('b','a')]:
            with self.subTest(android=android):
                original = boot()
                result = api['bind_boot'](original, control(android), android)
                self.assertEqual(len(result), len(original))
                self.assertEqual(result[4096:8224], original[4096:8224])
                avb, size = struct.unpack_from('>QQ', result, len(result)-44)
                self.assertEqual(result[avb:avb+size], b'V'*128)
                roles = unpack_roles(result)
                self.assertEqual(roles['etc/e5/linux-slot'], (linux+'\n').encode())
                for name, slot in [('etc/misc-bc-slot-a.bin', android), ('etc/misc-bc-slot-b-trial.bin', linux)]:
                    block = roles[name]
                    self.assertEqual(block[:2], ('_'+slot).encode())
                    self.assertEqual(struct.unpack_from('<I', block, 28)[0], zlib.crc32(block[:28]))

    def test_unknown_unhealthy_or_invalid_images_are_rejected(self):
        for image, bc, slot in [(b'bad',control('a'),'a'),(boot(),control('a')[:-1],'a'),
                                (boot(),control('a'),'x'), (boot(),control('a'),'b')]:
            with self.subTest(slot=slot), self.assertRaises(ValueError):
                api['bind_boot'](image, bc, slot)

    def test_firmware_and_root_roles_follow_android_not_linux(self):
        for android, linux in [('a','b'), ('b','a')]:
            files = api['role_files'](control(android), android)
            self.assertEqual(files['lib/firmware/android-firmware-slot'], (android+'\n').encode())
            self.assertEqual(files['etc/e5/modem-slot'], (android+'\n').encode())
            self.assertEqual(files['etc/e5/linux-slot'], (linux+'\n').encode())

    def test_cli_upload_uses_bound_image_without_rewriting_package(self):
        for android, linux in [('a','b'), ('b','a')]:
            with self.subTest(android=android), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); original = boot()
                (root/'boot.img').write_bytes(original)
                (root/'boot.json').write_text(json.dumps({'sha256': hashlib.sha256(original).hexdigest(), 'slot_roles_version':1}))
                uploads = []
                def push(local, remote, label): uploads.append((Path(local).read_bytes(), remote))
                with patch.multiple(flash, F=str(root), ANDROID_SLOT=android, LINUX_SLOT=linux), \
                     patch.object(flash, 'read_boot_control', return_value=control(android)), patch.object(flash, 'push', push):
                    result = flash.push_boot(str(root), '/tmp/boot.img')
                self.assertEqual((root/'boot.img').read_bytes(), original)
                self.assertEqual(unpack_roles(uploads[0][0])['etc/e5/linux-slot'], (linux+'\n').encode())
                self.assertEqual(result['sha256'], hashlib.sha256(uploads[0][0]).hexdigest())


if __name__ == '__main__': unittest.main()
