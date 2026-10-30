"""Cross-family regressions: actual bundled assets and simulated Android ADB only."""
import importlib.util
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
sys.path.insert(0,str(HERE/'mobile/python'))
from backend import storage, host
from backend.adapter import Adapter
from backend.state import RecoveryRequired
from android_host.runtime import Provider


class FamilyIntegration(unittest.TestCase):
    def test_storage_identities_and_labels_remain_family_specific(self):
        e5 = storage.inventory_script(HERE,'e5')
        mu = storage.inventory_script(HERE,'mu300')
        self.assertIn('E5_UFS_ID_HELPER',e5)
        self.assertNotIn('MU300_STORAGE_SYS',e5)
        self.assertIn('ufs-descriptor-v1',e5)
        self.assertIn('for label in e5root;',e5)
        self.assertIn('string_descriptors/serial_number',mu)
        self.assertNotIn('E5_UFS_ID_HELPER',mu)
        self.assertIn('for label in mu300root e5root;',mu)
        self.assertIn('mu300_storage_identity "$@"',mu)
        self.assertIn('e5_storage_identity "$@"',e5)

    def test_mu300_ufs_keeps_existing_identity_without_claiming_e5_descriptor_format(self):
        data = 'FAMILY=mu300\nNODE=sda76\nCID='+'a'*32+'\nSTORAGE_KIND=ufs\nSECTOR_SIZE=4096\nSTART=4096\nSIZE=8388608\nDISK_SIZE=16777216\nFREE=4096\n'
        self.assertEqual(storage.parse_inventory(data)['CID'],'a'*32)
        with self.assertRaisesRegex(ValueError,'descriptor'):
            storage.parse_inventory(data.replace('FAMILY=mu300','FAMILY=e5'))
        with self.assertRaises(ValueError):storage.library(family='guess')

    def test_embedded_and_desktop_use_the_same_selected_family_assets(self):
        with patch.object(host,'_provider',SimpleNamespace(resources=HERE/'runtime')):
            for family in ('e5','mu300'):
                self.assertIn(family+'_storage_identity()',storage.library(family=family))
                other='mu300' if family=='e5' else 'e5'
                self.assertNotIn(other+'_storage_identity()',storage.library(family=family))

    def test_exported_mu300_cli_uses_its_own_library(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'desktop').mkdir()
            (root/'desktop/manifest.json').write_text('{}')
            shutil.copy2(HERE/'backend/storage.py',root/'desktop/storage.py')
            shutil.copy2(HERE/'runtime/storage-mu300.sh',root/'desktop/storage-lib.sh')
            module=runpy.run_path(str(root/'desktop/storage.py'))
            self.assertIn('mu300_storage_identity()',module['inventory_script'](root))
            shutil.copy2(HERE/'runtime/storage-e5.sh',root/'desktop/storage-lib.sh')
            with self.assertRaises(ValueError):module['inventory_script'](root)

    def test_mu300_android_transport_uses_legacy_mu300_call_without_desktop_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);provider=Provider(root,root,Mock(),lambda *_:None)
            legacy=SimpleNamespace(Android=type('Android',(),{}))
            adapter=SimpleNamespace(kind='mu300',root=root,legacy=legacy,adb=SimpleNamespace(serial='usb:selected'),ctx=None)
            provider.bind_legacy(adapter)
            driver=Mock();driver.run.return_value=subprocess.CompletedProcess([],0,b'raw\0mu300',b'')
            provider.adb=Mock(return_value=driver)
            android=legacy.Android();android.serial='usb:selected'
            self.assertEqual(android.call('exec-out','cat /fixture'),b'raw\0mu300')
            provider.adb.assert_called_once_with('usb:selected')
            driver.run.assert_called_once_with(['exec-out','cat /fixture'],timeout=1800,context=None)
            with self.assertRaises(ValueError):legacy.subprocess.Popen(['adb.exe'])

    def test_ambiguous_package_cannot_select_a_different_nv_policy(self):
        from backend.package import inspect_package
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'desktop').mkdir();(root/'files').mkdir()
            (root/'desktop/manifest.json').write_text('{}')
            (root/'files/device-profile.json').write_text('{}')
            (root/'OpenWrt-Installer.py').write_text('# fixture')
            with self.assertRaisesRegex(ValueError,'Ambiguous'):inspect_package(root)

    def test_mu300_cannot_enter_e5_nv_recovery(self):
        adapter=Adapter.__new__(Adapter)
        record={'package':{'adapter':'mu300'}}
        adapter.ctx=SimpleNamespace(record=record,stage=Mock())
        with self.assertRaises(RecoveryRequired):adapter.confirm_nv_recovery('fixture')
        self.assertNotIn('nv_recovery',record)
        adapter.ctx.stage.assert_not_called()

    def test_unknown_mu300_model_requires_yes_then_legacy_entry_can_continue(self):
        from backend.model_compat import mu300_android, decision
        profile,warning=mu300_android('Compatible Box','custom-board','ums9620')
        package={'adapter':'mu300','profile':'mu300-family','kernel':'7.2','layout':'mu300root-container','payloads':{}}
        device={'serial':'fixture','cid':'a'*32,'model':'Compatible Box','board':'custom-board','platform':'ums9620','profile':profile,'model_mismatch':warning}
        prompt,approval=decision(package,device,'part',{})
        self.assertIsNone(approval)
        for answer in ('YES',' yes','',False):
            self.assertIsNotNone(decision(package,device,'part',{'model_confirmation':answer,'model_token':prompt['model_token']})[0])
        prompt,approval=decision(package,device,'part',{'model_confirmation':'yes','model_token':prompt['model_token']})
        self.assertIsNone(prompt)
        record={'package':package,'device':device,'choices':{'method':'part'},'model_override':approval}
        adapter=Adapter.__new__(Adapter);adapter.package=package;adapter.ctx=SimpleNamespace(record=record)
        self.assertEqual(adapter.confirm_mu300_model('Compatible Box\ncustom-board','ums9620'),'mu300-family')
        with self.assertRaises(ValueError):adapter.confirm_mu300_model('Compatible Box\nother-board','ums9620')
        with self.assertRaises(ValueError):adapter.confirm_mu300_model('Compatible Box\ncustom-board','other-platform')

    def test_unknown_mu300_runtime_profile_uses_the_same_yes_decision(self):
        from backend.model_compat import mu300_runtime, decision
        package={'adapter':'mu300','profile':'mu300-family','kernel':'7.2','layout':'mu300root-container','payloads':{}}
        device={'serial':'ssh:fixture','cid':'a'*32,'model':'CUSTOM','profile':'custom','system':'OpenWrt','model_mismatch':mu300_runtime('custom')}
        prompt,_=decision(package,device,'update',{})
        _,approval=decision(package,device,'update',{'model_confirmation':'yes','model_token':prompt['model_token']})
        adapter=Adapter.__new__(Adapter);adapter.package=package
        adapter.ctx=SimpleNamespace(record={'package':package,'device':device,'choices':{'method':'update'},'model_override':approval})
        adapter.confirm_update_profile('custom')
        with self.assertRaises(ValueError):adapter.confirm_update_profile('changed')

    def test_new_e5_package_profile_is_not_rejected_by_a_compiled_model_list(self):
        from backend.package import e5_profile
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'files').mkdir()
            metadata={'profile':'new-compatible-model','android_models':['Unlisted device']}
            (root/'files/device-profile.json').write_text(json.dumps(metadata))
            self.assertEqual(e5_profile(root),metadata)
            (root/'files/device-profile.json').write_text('{"profile":"bad/profile"}')
            with self.assertRaises(ValueError):e5_profile(root)

    def test_source_fingerprints_are_inside_the_independent_repository(self):
        manifest=runpy.run_path(str(HERE/'package_gui.py'))['sources']()
        self.assertIn('runtime/storage-e5.sh',manifest)
        self.assertIn('runtime/storage-mu300.sh',manifest)
        self.assertFalse(any(name.startswith('../') for name in manifest))

    def test_core_imports_work_without_any_firmware_parent_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for directory in ('backend','runtime'):
                shutil.copytree(HERE/directory,root/directory,ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copy2(HERE/'resources.py',root/'resources.py')
            code="import sys;sys.path.insert(0,sys.argv[1]);from backend.service import Service;from backend import storage;from backend.ota_engine import PROTECTED;assert 'etc/e5-nv' in PROTECTED;assert 'mu300_storage_identity()' in storage.library(family='mu300');assert 'e5_storage_identity()' in storage.library(family='e5');print('standalone-ok')"
            result=subprocess.run([sys.executable,'-I','-c',code,str(root)],cwd=root,capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),'standalone-ok')


if __name__=='__main__':unittest.main()
