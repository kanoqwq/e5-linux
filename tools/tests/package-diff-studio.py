#!/usr/bin/env python3
"""Native entry reuse and OTA export scopes, without devices or compilation."""
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
import zipfile

TOP=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(TOP/'installer'))
from package_gui import sources
from backend.ota_bundle import export_bundle
tool=runpy.run_path(str(TOP/'tools/package-diff-studio.py'))


class DiffPackaging(unittest.TestCase):
    def test_real_cli_attaches_the_tool_instead_of_exiting_without_work(self):
        result=subprocess.run([sys.executable,str(TOP/'tools/package-diff-studio.py'),
            '--package',str(self.root)],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((self.root/'diff-tool.json').is_file())
        self.assertTrue((self.root/'OpenWrt-Diff-Studio.exe').is_file())

    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)/'package'
        (self.root/'docs').mkdir(parents=True);(self.root/'START-HERE.md').write_text('Installer guide\n')
        self.native={}
        for platform,label,files in [
            ('windows-x64','GUI-BUILD.json',{'OpenWrt-Installer.exe':b'MZ GUI','tools/backend/openwrt-backend.exe':b'MZ runtime','tools/adb.exe':b'adb'}),
            ('macos-arm64','GUI-BUILD-macos.json',{'OpenWrt-Installer.app/Contents/MacOS/openwrt-installer':b'\xcf\xfa\xed\xfe gui','OpenWrt-Installer.app/Contents/Info.plist':b'unchanged signed metadata','tools/macos/backend/openwrt-backend':b'\xcf\xfa\xed\xfe runtime','tools/macos/adb':b'adb'})]:
            for name,raw in files.items():
                path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            build={'protocol':1,'platform':platform,'source':sources(),'files':{n:hashlib.sha256(raw).hexdigest() for n,raw in files.items()}}
            (self.root/'docs'/label).write_text(json.dumps(build));self.native.update(files)
        (self.root/'files').mkdir();(self.root/'files/boot.img').write_bytes(b'one boot')
        (self.root/'files/openwrt.ext4.gz').write_bytes(b'one rootfs')

    def test_shared_runtime_and_signed_mac_contents_unchanged(self):
        manifest=tool['attach'](self.root)
        for name,raw in self.native.items():self.assertEqual((self.root/name).read_bytes(),raw)
        self.assertEqual((self.root/'OpenWrt-Diff-Studio.exe').read_bytes(),self.native['OpenWrt-Installer.exe'])
        self.assertEqual((self.root/'OpenWrt-Diff-Studio.app/Contents/Info.plist').read_bytes(),b'unchanged signed metadata')
        self.assertTrue((self.root/'docs/DIFF-STUDIO.md').exists())
        self.assertIn('OpenWrt-Diff-Studio.exe',(self.root/'START-HERE.md').read_text())
        self.assertNotIn('tools/adb.exe',manifest['files']);self.assertNotIn('tools/macos/adb',manifest['files'])
        self.assertFalse(any(n.startswith('files/') for n in manifest['files']))
        self.assertEqual(list(self.root.rglob('openwrt.ext4.gz')),[self.root/'files/openwrt.ext4.gz'])

    def test_export_contains_both_updaters_and_no_full_installer_payload(self):
        tool['attach'](self.root);patch=self.root.parent/'test.ota';patch.write_bytes(b'opaque patch fixture')
        target=self.root.parent/'patch.zip';export_bundle(patch,target,self.root)
        with zipfile.ZipFile(target) as z:
            names=z.namelist();self.assertEqual(len(names),len(set(names)))
            for name in ('OpenWrt-Updater.exe','OpenWrt-Updater.py','OpenWrt-Updater.app/Contents/MacOS/openwrt-installer','tools/backend/openwrt-backend.exe','tools/macos/backend/openwrt-backend'):
                self.assertIn(name,names)
            self.assertFalse(any('Installer' in n or n.startswith('files/') or 'adb' in n for n in names))
            self.assertEqual(names.count('update.ota'),1)

    def test_export_restores_mac_execute_bits_from_windows_style_modes(self):
        manifest=tool['attach'](self.root)
        executable='OpenWrt-Diff-Studio.app/Contents/MacOS/openwrt-installer'
        backend='tools/macos/backend/openwrt-backend'
        library='tools/macos/backend/_internal/libpython-test.dylib'
        path=self.root/library;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b'\xcf\xfa\xed\xfe library fixture')
        manifest['files'][library]=hashlib.sha256(path.read_bytes()).hexdigest()
        (self.root/'diff-tool.json').write_text(json.dumps(manifest))
        for name in (executable,backend,library):(self.root/name).chmod(0o644)
        patch=self.root.parent/'test.ota';patch.write_bytes(b'opaque fixture')
        target=self.root.parent/'patch.zip';export_bundle(patch,target,self.root)
        with zipfile.ZipFile(target) as z:
            for name in (executable.replace('Diff-Studio','Updater'),backend,library):
                self.assertEqual((z.getinfo(name).external_attr>>16)&0o777,0o755)
            self.assertEqual(z.read(executable.replace('Diff-Studio','Updater')),(self.root/executable).read_bytes())
            self.assertFalse((z.getinfo('OpenWrt-Updater.app/Contents/Info.plist').external_attr>>16)&0o111)

    def test_stale_native_and_duplicate_attachment_refused(self):
        (self.root/'tools/backend/openwrt-backend.exe').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'native input mismatch'):tool['attach'](self.root)
        self.assertFalse((self.root/'OpenWrt-Diff-Studio.exe').exists())
        (self.root/'tools/backend/openwrt-backend.exe').write_bytes(self.native['tools/backend/openwrt-backend.exe'])
        tool['attach'](self.root)
        with self.assertRaisesRegex(ValueError,'already exists'):tool['attach'](self.root)


if __name__=='__main__':unittest.main()
