"""Execute the real Linux updater against temporary boot/misc files, both roles."""
import hashlib
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest

TOP=Path(__file__).resolve().parents[2]
helper=runpy.run_path(str(TOP/'openwrt/tests/installer-slot-roles.py'))


class Flash(unittest.TestCase):
    def run_case(self,android,bad=None):
        linux='b' if android=='a' else 'a'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);run=root/'run';run.mkdir();by=root/'by';by.mkdir()
            roles=helper['api']['role_files'](helper['control'](android),android)
            for name,data in roles.items():
                if name.startswith('etc/e5linux/'):(run/Path(name).name).write_bytes(data)
            declared=root/'linux-slot';declared.write_text(linux+'\n')
            if bad=='conflict':declared.write_text(android+'\n')
            if bad=='short':(run/'misc-bc-slot-b-trial.bin').write_bytes(('_'+linux).encode())
            (by/'boot_a').write_bytes(b'A'*(2<<20));(by/'boot_b').write_bytes(b'B'*(2<<20))
            misc=by/'misc';misc.write_bytes(b'M'*4096)
            original={p.name:p.read_bytes() for p in by.iterdir()}
            head=root/'head.img';head.write_bytes(b'N'*(1<<20))
            code=(TOP/'openwrt/device-flash-boot.sh').read_text()
            start=code.index('part() {');end=code.index('\nBC_B=',start)
            code=code[:start]+'part() { echo "'+str(by)+'/$1"; }\n'+code[end:]
            code=code.replace('/run/e5linux',str(run)).replace('/etc/e5/linux-slot',str(declared))
            code=code.replace('/tmp/e5-boot-head.img',str(root/'temporary.img')).replace('[ -b ','[ -f ')
            # A real block device does not truncate on open; mirror that for
            # this regular-file fixture while keeping the actual dd/readback.
            code=code.replace('conv=fsync','conv=notrunc,fsync')
            script=root/'flash.sh';script.write_text(code)
            result=subprocess.run(['sh',str(script),str(head),hashlib.sha256(head.read_bytes()).hexdigest(),'1'],capture_output=True,text=True,timeout=20)
            if bad:
                self.assertNotEqual(result.returncode,0)
                self.assertEqual({p.name:p.read_bytes() for p in by.iterdir()},original)
            else:
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertEqual((by/('boot_'+android)).read_bytes(),original['boot_'+android])
                self.assertEqual((by/('boot_'+linux)).read_bytes(),head.read_bytes()+original['boot_'+linux][1<<20:])
                self.assertEqual(misc.read_bytes(),original['misc'][:2048]+roles['etc/e5linux/misc-bc-slot-b-trial.bin']+original['misc'][2080:])

    def test_writes_only_the_identified_linux_slot(self):
        for slot in ('a','b'):
            with self.subTest(android=slot):self.run_case(slot)

    def test_incomplete_and_conflicting_roles_stop_before_writes(self):
        for slot in ('a','b'):
            for bad in ('short','conflict'):
                with self.subTest(android=slot,bad=bad):self.run_case(slot,bad)


if __name__=='__main__':unittest.main()
