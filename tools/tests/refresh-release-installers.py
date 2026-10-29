#!/usr/bin/env python3
"""Installer refresh must preserve image bytes and checksum coverage."""
import hashlib
import io
import json
from pathlib import Path
import runpy
import tempfile
import tarfile
import unittest
import zipfile

TOP=Path(__file__).resolve().parents[2]
helper=runpy.run_path(str(TOP/'tools/refresh-release-installers.py'))
runner=runpy.run_path(str(TOP/'tools/build-release.py'))


def tar_bytes(files):
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w') as tar:
        for name,raw in files.items():
            member=tarfile.TarInfo(name);member.size=len(raw);tar.addfile(member,io.BytesIO(raw))
    return output.getvalue()


class Refresh(unittest.TestCase):
    def test_stream_refresh_preserves_firmware_and_updates_only_host_files(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'old.zip';target=root/'new.zip';tools=root/'tools.tar'
            baseline=tar_bytes({'lib/base.so':b'same ABI'});tools.write_bytes(tar_bytes({'lib/base.so':b'same ABI','usr/bin/flock':b'new lock tool'}))
            files={'files/boot.img':b'unchanged boot','files/openwrt.ext4.gz':b'unchanged image',
                   'files/part-write.sh':b'old writer','files/ext4-tools.tar':baseline,
                   'START-HERE.md':b'guide','OpenWrt-Diff-Studio.exe':b'GUI'}
            files['files/SHA256SUMS']=''.join(hashlib.sha256(v).hexdigest()+'  '+n+'\n' for n,v in files.items()).encode()
            with zipfile.ZipFile(source,'w',zipfile.ZIP_DEFLATED) as z:
                for name,raw in files.items():z.writestr(name,raw)
            meta=helper['refresh'](source,target,tools,{'source_commit':'a'*40,'source_fingerprint':'b'*64})
            with zipfile.ZipFile(target) as z:
                self.assertIsNone(z.testzip());self.assertEqual(len(z.namelist()),len(set(z.namelist())))
                for name in ('files/boot.img','files/openwrt.ext4.gz','OpenWrt-Diff-Studio.exe'):
                    self.assertEqual(z.read(name),files[name])
                self.assertEqual(z.read('files/ext4-tools.tar'),baseline)
                with tarfile.open(fileobj=io.BytesIO(z.read('files/install-lock-tools.tar'))) as tar:
                    self.assertEqual(tar.extractfile('usr/bin/flock').read(),b'new lock tool')
                    self.assertNotIn('lib/base.so',tar.getnames())
                self.assertIn(b'flock',z.read('files/part-write.sh'))
                self.assertEqual(z.read('files/device-flash-boot.sh'),
                    (TOP/'openwrt/device-flash-boot.sh').read_bytes().replace(b'\r\n',b'\n'))
                self.assertEqual(z.read('files/storage.sh'),
                    (TOP/'boot/storage.sh').read_bytes().replace(b'\r\n',b'\n'))
                self.assertEqual(json.loads(z.read('files/INSTALLER-REVISION.json')),meta)
                sums={n:h for line in z.read('files/SHA256SUMS').decode().splitlines() for h,n in [line.split(None,1)]}
                self.assertEqual(set(sums),set(z.namelist())-{'files/SHA256SUMS'})
                for name,sha in sums.items():self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(),sha)
            with self.assertRaisesRegex(ValueError,'already exists'):
                helper['refresh'](source,target,tools,{'source_commit':'a'*40})


if __name__=='__main__':unittest.main()
