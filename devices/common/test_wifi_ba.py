"""Patch real prepared driver files, then execute their C with fault injection."""
import os
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
api=runpy.run_path(str(HERE/'wifi-ba.py'))
BASE=api['BASE']
FILES=('common/hif.h','common/delay_work.h','common/delay_work.c','common/cfg80211.c',
       'sc2355/tx.c','sc2355/sdio.c','sc2355/reorder.c')


class BA(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        source=Path(os.environ.get('E5_TEST_KERNEL','/home/kano/openwrt-build/sources/e5-kernel'))
        if not (source/BASE/'sc2355/tx.c').is_file():self.skipTest('Set E5_TEST_KERNEL to the real pinned driver source')
        for name in FILES:
            target=self.root/BASE/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source/BASE/name,target)
        marker=source/BASE/'sc2355/ba-recovery.h'
        if marker.is_file():shutil.copyfile(marker,self.root/BASE/'sc2355/ba-recovery.h')

    def test_real_sources_reapply_without_drift(self):
        api['apply'](self.root)
        before={p.relative_to(self.root):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        times={p:p.stat().st_mtime_ns for p in self.root.rglob('*') if p.is_file()}
        api['apply'](self.root)
        self.assertEqual(before,{p.relative_to(self.root):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertEqual(times,{p:p.stat().st_mtime_ns for p in self.root.rglob('*') if p.is_file()})
        for name,count in [('common/delay_work.c',2),('common/cfg80211.c',1)]:
            source=(self.root/BASE/name).read_text()
            self.assertEqual(source.count('sprd_work->release(sprd_work);'),count)
        self.assertIn('s64 time[8]',(self.root/BASE/'common/hif.h').read_text())

    def test_unknown_anchor_leaves_all_inputs_unchanged(self):
        path=self.root/BASE/'common/delay_work.h';path.write_text('unknown work ABI')
        before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with self.assertRaises(ValueError):api['apply'](self.root)
        self.assertEqual(before,{p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_unknown_vendor_ba_body_is_not_overwritten(self):
        path=self.root/BASE/'sc2355/tx.c'
        path.write_text(path.read_text().replace('void sc2355_tx_prepare_addba(',
            '/* Different upstream implementation. */\nvoid sc2355_tx_prepare_addba(',1)
            .replace('hif->tx_num[lut_index] > 9','hif->tx_num[lut_index] > 12',1))
        # A source already carrying this port may have its own marker; the
        # standalone unreviewed-vendor case intentionally has none.
        (self.root/BASE/'sc2355/ba-recovery.h').unlink(missing_ok=True)
        before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(ValueError,'Unreviewed'):api['apply'](self.root)
        self.assertEqual(before,{p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_actual_c_faults_rearm_and_concurrency(self):
        api['apply'](self.root)
        tx=(self.root/BASE/'sc2355/tx.c').read_text()
        sdio=(self.root/BASE/'sc2355/sdio.c').read_text()
        functions=[]
        for name in ('sc2355_tx_prepare_addba','sc2355_tx_addba','sc2355_ba_command',
                     'sc2355_tx_ba_mgmt','sc2355_tx_send_addba'):
            a,b=api['function'](tx,name);functions.append(tx[a:b])
        a,b=api['function'](sdio,'sc2355_event_sta_lut');functions.append(sdio[a:b])
        implementation=(HERE/'wifi-ba.h').read_text().replace('#include "tx.h"','').replace('#include "common/delay_work.h"','')
        code=(HERE/'test_wifi_ba.c').read_text().replace('/* BA_IMPLEMENTATION */',implementation+'\n'+'\n'.join(functions))
        path=self.root/'test.c';path.write_text(code)
        binary=self.root/'test'
        subprocess.run(['cc','-std=gnu11','-O1','-g','-Wall','-Werror','-Wno-unused-function',
                        '-fsanitize=address,undefined','-pthread',str(path),'-o',str(binary)],check=True)
        subprocess.run([str(binary)],check=True,timeout=30)

    def test_all_registered_profiles_use_the_same_ba_contract(self):
        workspace=Path('/home/kano/openwrt-build')
        config=json.loads((HERE.parents[1]/'tools/build-layout.json').read_text())
        lower=workspace/'sources/e5-kernel'
        fragment=(HERE/'wifi-ba-tx.c').read_text()
        for model in config['profiles']:
            with self.subTest(model=model):
                native=config.get('native_kernel_builds',{}).get(model)
                source=Path(native['source'].format(workspace=workspace)) if native else workspace/'kernel-views'/model/'upper'
                target=self.root/'models'/model
                for name in FILES:
                    original=source/BASE/name
                    if not original.is_file():original=lower/BASE/name
                    path=target/BASE/name;path.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(original,path)
                marker=source/BASE/'sc2355/ba-recovery.h'
                if marker.is_file():shutil.copyfile(marker,target/BASE/'sc2355/ba-recovery.h')
                api['apply'](target)
                for name,file in [('sc2355_tx_addba','tx.c'),('sc2355_tx_send_addba','tx.c'),
                                  ('sc2355_event_sta_lut','sdio.c')]:
                    text=(target/BASE/'sc2355'/file).read_text()
                    a,b=api['function'](text,name);x,y=api['function'](fragment,name)
                    self.assertEqual(text[a:b],fragment[x:y])
        self.assertIn("common') / 'wifi-ba.py'",(HERE.parent/'wifi-common.py').read_text())


if __name__=='__main__':unittest.main()
