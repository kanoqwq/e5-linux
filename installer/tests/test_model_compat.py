"""Model exceptions never replace device, package or plan validation."""
from copy import deepcopy
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_method_routing as routing
from backend.adapter import Adapter
from backend.model_compat import android_warning, runtime_warning, decision, require_approval
from backend.service import Service
from backend.state import Context, Store


class ModelCompatibility(unittest.TestCase):
    def setUp(self):
        self.package = {'adapter':'e5','profile':'m8','layout':'e5root-ext4',
                        'kernel':'v2','payloads':{'boot':'a'*64},'path':'.'}
        self.profile = {'profile':'m8','android_models':['VIPME M8']}
        self.device = {'serial':'fixture','cid':'b'*32,'model':'M8 variant',
                       'board':'variant-board','system':'Android'}
        self.device['model_mismatch'] = android_warning(self.profile,'M8 variant','variant-board')

    def approve(self, method='reinstall', device=None):
        device = device or self.device
        prompt, _ = decision(self.package,device,method,{})
        pending, approval = decision(self.package,device,method,
            {'model_token':prompt['model_token'],'model_confirmation':'yes'})
        self.assertIsNone(pending)
        return dict(package=deepcopy(self.package),device=deepcopy(device),
                    choices={'method':method},model_override=approval)

    def test_matching_models_and_other_repository_do_not_prompt(self):
        self.assertIsNone(android_warning(self.profile,'VIPME M8','anything'))
        self.assertIsNone(android_warning({'profile':'k1','android_models':['angelcare K1']},
                                          'angelcare K1','ums9157_2h10'))
        self.assertIsNotNone(android_warning({'profile':'k1','android_models':['angelcare K1']},
                                             'angelcare K1','wrong'))
        self.assertIsNotNone(decision({'adapter':'mu300'},self.device,'part',{})[0])
        self.assertEqual(decision({'adapter':'mu300'},{'model_mismatch':None},'part',{}),(None,None))

    def test_only_exact_yes_with_current_token_approves(self):
        prompt, _ = decision(self.package,self.device,'part',{})
        for value in ('',False,True,'YES',' yes','yes '):
            result, approval = decision(self.package,self.device,'part',
                {'model_confirmation':value,'model_token':prompt['model_token']})
            self.assertIsNotNone(result)
            self.assertIsNone(approval)
        result, approval = decision(self.package,self.device,'part',
            {'model_confirmation':'yes','model_token':'wrong-token'})
        self.assertIsNotNone(result)
        self.assertIsNone(approval)
        for token in (None,{},'确认'):
            prompt, approval = decision(self.package,self.device,'part',
                {'model_confirmation':'yes','model_token':token})
            self.assertIsNotNone(prompt)
            self.assertIsNone(approval)

    def test_package_device_and_method_changes_invalidate_confirmation(self):
        record = self.approve()
        require_approval(record,record['device'])
        for key in ('serial','cid','model','board'):
            actual=deepcopy(record['device']);actual[key]='changed'
            with self.subTest(key=key),self.assertRaises(ValueError):
                require_approval(record,actual)
        for key in ('profile','kernel','layout','payloads'):
            changed=deepcopy(record);changed['package'][key]='different'
            with self.subTest(package=key),self.assertRaises(ValueError):
                require_approval(changed,changed['device'])
        changed=deepcopy(record);changed['choices']['method']='part'
        with self.assertRaises(ValueError):require_approval(changed,changed['device'])
        # The same hardware may reboot during the existing two-stage workflow.
        actual=deepcopy(record['device']);actual['boot_id']='another boot'
        actual['slot']='_b'
        require_approval(record,actual)

    def test_missing_approval_stops_before_legacy_execution(self):
        record=self.approve();record.pop('model_override')
        with self.assertRaises(ValueError):require_approval(record,record['device'])

    def test_gui_plan_prompts_without_creating_transaction_then_persists_yes(self):
        adapter=routing.MethodRouting().adapter()
        properties=adapter.adb.properties();properties['ro.product.model']='M8-like hardware'
        adapter.adb.properties=Mock(return_value=properties)
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory);service=Service('.',store,io.StringIO())
            service.package=adapter.package;service.adapters=lambda *_args:adapter
            params={'serial':routing.SERIAL,'choices':{'method':'reinstall','size_gib':4,'grow':True}}
            prompt=service.dispatch({'protocol':1,'command':'plan','params':params})
            self.assertEqual(prompt['confirmation_required'],'model_mismatch')
            self.assertEqual(store.list(),[])
            params.update(model_confirmation='yes',model_token=prompt['model_token'])
            result=service.dispatch({'protocol':1,'command':'plan','params':params})
            saved=store.load(result['id'])
            self.assertEqual(saved['status'],'ready')
            self.assertEqual(saved['model_override']['confirmation'],'yes')
            self.assertEqual(saved['effects'],{})
            require_approval(saved,saved['device'])
            self.assertFalse(any(' of=' in c for c in adapter.adb.commands))
            # A newly-created backend context can validate the saved exception.
            bound=Adapter.__new__(Adapter);bound.package=saved['package']
            bound.ctx=Context(store,saved,lambda *_:None)
            bound.confirm_device_model(adapter.legacy.package_profile(),
                                       saved['device']['model'],saved['device']['board'])
            with self.assertRaises(ValueError):
                bound.confirm_device_model(adapter.legacy.package_profile(),
                                           saved['device']['model'],'different-board')

    def test_update_profile_uses_the_same_explicit_decision(self):
        device={'serial':'ssh:test-key','cid':'b'*32,'profile':'e5','model':'E5',
                'system':'OpenWrt','model_mismatch':runtime_warning('m8','e5')}
        record=self.approve('update',device)
        bound=Adapter.__new__(Adapter);bound.package=self.package
        bound.ctx=type('Context',(),{'record':record})()
        bound.confirm_update_profile('e5')
        with self.assertRaises(ValueError):bound.confirm_update_profile('t551m')


if __name__ == '__main__':
    unittest.main()
