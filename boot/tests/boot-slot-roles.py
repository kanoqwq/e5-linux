#!/usr/bin/env python3
import os
from pathlib import Path, PurePosixPath
import runpy
import stat
import struct
import subprocess
import tempfile
import unittest
import zlib

TOP=Path(__file__).resolve().parents[2]
control=runpy.run_path(str(TOP/'boot/build-boot-image.py'))['bootloader_control']
archive=runpy.run_path(str(TOP/'boot/build-boot-image.py'))['initramfs_archive']


class Roles(unittest.TestCase):
    @unittest.skipIf(os.name=='nt','Requires Linux shell and coreutils')
    def test_init_role_uses_trial_suffix_without_guessing(self):
        text=(TOP/'boot/init').read_text()
        function=text[text.index('load_linux_slot() {'):text.index('\nload_linux_slot ||')]
        for declared,suffix,expected in [('', '_a','a'), ('','_b','b'),
                                       ('a','_a','a'), ('b','_b','b'),
                                       ('a','_b',''), ('b','_a',''), ('','??','')]:
            with self.subTest(declared=declared,suffix=suffix),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);(root/'e5').mkdir()
                if declared:(root/'e5/linux-slot').write_text(declared+'\n')
                (root/'misc-bc-slot-b-trial.bin').write_bytes(suffix.encode()+b'\0'*30)
                script=function.replace('/etc/',str(root)+'/')
                result=subprocess.run(['sh','-c',script+'\nload_linux_slot; r=$?; printf "%s:%s" "$r" "$linux_slot"'],capture_output=True,text=True)
                self.assertEqual(result.stdout,('0:' if expected else '1:')+expected)

    def test_archive_emits_parent_before_slot_role_file(self):
        raw=archive({'etc/e5/linux-slot': (b'a\n', stat.S_IFREG | 0o644)}, {'dev'})
        offset=0;seen={};payloads={}
        while offset<len(raw):
            self.assertEqual(raw[offset:offset+6],b'070701')
            fields=[int(raw[offset+6+i*8:offset+14+i*8],16) for i in range(13)]
            size,namesize=fields[6],fields[11]
            name=raw[offset+110:offset+110+namesize-1].decode()
            start=(offset+110+namesize+3)&~3
            if name=='TRAILER!!!':break
            for parent in PurePosixPath(name).parents:
                if str(parent)!='.':
                    self.assertIn(str(parent),seen)
                    self.assertTrue(stat.S_ISDIR(seen[str(parent)]))
            seen[name]=fields[1];payloads[name]=raw[start:start+size]
            offset=(start+size+3)&~3
        self.assertEqual(payloads['etc/e5/linux-slot'],b'a\n')

    def make(self,slot):
        raw=bytearray(4096);bc=bytearray(32)
        bc[:4]=('_'+slot).encode()+b'\0\0';bc[4:8]=b'BCAB';bc[8:10]=b'\x01\x02'
        bc[12]=0x9f if slot=='a' else 0x9e
        bc[14]=0x9f if slot=='b' else 0
        struct.pack_into('<I',bc,28,zlib.crc32(bc[:28]))
        raw[2048:2080]=bc
        return bytes(raw)

    def test_physical_roles_and_crc_for_both_android_slots(self):
        for slot in ('a','b'):
            fallback,linux=control(self.make(slot),slot)
            self.assertEqual(control(self.make(slot)),(fallback,linux))
            index=12 if slot=='a' else 14;opposite=14 if slot=='a' else 12
            self.assertEqual(fallback[:2],('_'+slot).encode())
            self.assertEqual(linux[:2],b'_b' if slot=='a' else b'_a')
            self.assertEqual(linux[opposite],0x2f)
            self.assertEqual(linux[index]&15,14)
            self.assertTrue(fallback[index]&0x80)
            self.assertEqual(fallback[opposite],0)
            for block in (fallback,linux):
                self.assertEqual(struct.unpack_from('<I',block,28)[0],zlib.crc32(block[:28]))

    @unittest.skipIf(os.name=='nt','Requires Linux shell and coreutils')
    def test_system_switch_and_rearm_follow_roles_on_regular_files(self):
        for slot in ('a','b'):
            with self.subTest(slot=slot),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);fallback,linux=control(self.make(slot),slot)
                (root/'android.bin').write_bytes(fallback);(root/'linux.bin').write_bytes(linux)
                misc=root/'misc';misc.write_bytes(b'\0'*2048+fallback)
                conf=root/'default';script=root/'next'
                text=(TOP/'rootfs/overlay/opt/e5/e5-next-boot').read_text()
                text=text.replace('CONF=/etc/e5linux/default-boot','CONF='+str(conf))
                text=text.replace('BC_A=/run/e5linux/misc-bc-slot-a.bin','BC_A='+str(root/'android.bin'))
                text=text.replace('BC_B=/run/e5linux/misc-bc-slot-b-trial.bin','BC_B='+str(root/'linux.bin'))
                (root/'run').mkdir()
                text=text.replace('/run/',str(root/'run')+'/')
                line=next(line for line in text.splitlines() if line.startswith('MISC='))
                text=text.replace(line,'MISC='+str(misc));script.write_text(text)
                def run(arg):return subprocess.check_output(['bash',str(script),arg],text=True).strip()
                self.assertEqual(run('next'),'android')
                # Unknown or conflicting roles must fail before touching misc.
                original=misc.read_bytes()
                (root/'linux.bin').write_bytes(b'?'*32)
                result=subprocess.run(['bash',str(script),'linux'],capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(misc.read_bytes(),original)
                (root/'linux.bin').write_bytes(linux)
                (root/'android.bin').write_bytes(linux)
                result=subprocess.run(['bash',str(script),'android'],capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(misc.read_bytes(),original)
                (root/'android.bin').write_bytes(fallback)
                run('linux');self.assertEqual(misc.read_bytes()[2048:2080],linux)
                self.assertEqual(run('next'),'linux')
                run('--rearm');self.assertEqual(run('next'),'linux')
                run('android');run('--rearm')
                self.assertEqual(misc.read_bytes()[2048:2080],fallback)
                self.assertEqual(run('next'),'android')


if __name__=='__main__':unittest.main()
