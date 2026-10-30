"""Exercise the formal P720S20 path without connecting to hardware."""
from pathlib import Path
from contextlib import redirect_stdout
import hashlib
import io
import json
import runpy
import shlex
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.adapter import Adapter
from backend import gpt, storage
from backend.service import plan_install
from backend.state import Context, Store, Pending
from test_storage_ufs import snapshot

from firmware_sources import firmware_root
TOP = firmware_root('e5')


class Transport:
    exe = 'fixture-adb'
    su = 'su'

    def __init__(self, slot):
        self.slot = slot
        self.commands = []
        self.first, self.last, self.total = snapshot(4096)
        self.layout = gpt.gpt_space(self.first, self.last, self.total)

    def devices(self):
        return [{'serial': 'fixture-cx60', 'state': 'device'}]

    def properties(self):
        return {'ro.product.model': 'ZTE 7552N', 'ro.product.device': 'P720S20',
                'ro.boot.slot_suffix': self.slot, 'ro.board.platform': 'ums9620',
                'ro.crypto.state': 'encrypted', 'ro.crypto.type': 'file',
                'sys.boot_completed': '1'}

    def root_status(self):
        return 'ready'

    def shell(self, script, root=False):
        self.commands.append(script)
        if script == 'cat /proc/sys/kernel/random/boot_id':
            return 'fixture-boot'
        if script == 'settings get secure user_setup_complete':
            return '1'
        if 'echo NODE=$n' in script:
            return ('NODE=sdb76\nDISK=sdb\nSTORAGE_KIND=ufs\n'
                    'IDENTITY_KIND=ufs-descriptor-v1\nCID='+'a'*32+
                    '\nSECTOR_SIZE=4096\nSTART=4096\nSIZE=33550336\n'
                    f'DISK_SIZE={self.total}\nFREE=2097152\nSOURCE=/dev/block/dm-7\n')
        raise AssertionError(script)

    def read(self, script):
        self.commands.append(script)
        if 'bs=4096 skip=1 count=1' in script:
            return self.first[4096:8192]
        if f'bs=4096 skip={self.total//8-1} count=1' in script:
            return self.last[-4096:]
        if f'bs=512 count={self.layout["front"]} ' in script:
            return self.first
        if f'bs=512 skip={self.layout["tail"]} ' in script:
            return self.last
        if 'count=4 ' in script:
            return bytes(2048)
        raise AssertionError(script)


class Changxing60(unittest.TestCase):
    def test_reinstall_can_identify_original_preparation_without_discarding_unknown_nv(self):
        with tempfile.TemporaryDirectory() as temp:
            api=runpy.run_path(str(TOP/'openwrt/bundle/part-two-stage.py'))
            store=Store(Path(temp));data=dict(node='sda76',cid='a'*32,start=2048,size=8000000)
            root=dict(node='sda77',cid='a'*32,start=8002048,size=4194304)
            dev=dict(serial='fixture',cid='a'*32,slot='_b')
            original=store.create({'adapter':'e5','profile':'changxing60'},dev,{'method':'part'},dict(gap_start=root['start'],openwrt_bytes=root['size']*512))
            original['effects']={'gpt-free-space':{'state':'done'}}
            path=store.directory(original['id'])/'legacy/transaction.json';path.parent.mkdir()
            state=dict(token='c'*32,data=data,root_start=root['start'],need=root['size'],phase='awaiting-android')
            path.write_text(json.dumps(state));original['legacy']=str(path);store.save(original)
            raw=(b'E5-PART-PREPARED-V2 '+b'c'*32+b'\n').ljust(8192,b'\0')
            read=Mock(return_value=raw)
            gui=SimpleNamespace(ctx=SimpleNamespace(store=store),read_legacy=read)
            g=dict(GUI=gui,SERIAL='fixture',package_profile=lambda:{'profile':'changxing60'},current_slots=lambda:('b','a'))
            self.assertEqual(api['prepared_root_token'](g,root,data),'c'*32)
            for fault in ('marker','ext4','f2fs','geometry','effect','slot'):
                with self.subTest(fault=fault):
                    changed=bytearray(raw);target=dict(root)
                    if fault=='marker':changed[20]=ord('d')
                    if fault=='ext4':changed[1080:1082]=b'\x53\xef'
                    if fault=='f2fs':changed[1024:1028]=bytes.fromhex('1020f5f2')
                    if fault=='geometry':target['size']+=8
                    original['effects']['gpt-free-space']['state']='started' if fault=='effect' else 'done'
                    original['device']['slot']='_a' if fault=='slot' else '_b'
                    store.save(original);read.return_value=bytes(changed)
                    self.assertIsNone(api['prepared_root_token'](g,target,data))

    def test_new_free_space_records_keep_dynamic_roles_and_real_guard(self):
        import gzip
        for slot in ('_a','_b'):
            with self.subTest(slot=slot),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);(root/'files').mkdir()
                (root/'files/boot.img').write_bytes(b'boot fixture')
                with gzip.open(root/'files/openwrt.ext4.gz','wb') as out:out.write(bytes(4096))
                first,last,total=snapshot(4096);space=gpt.gpt_space(first,last,total)
                data=next(p for p in space['parts'] if p['name']=='userdata')
                storage=dict(NODE='sdb2',CID='a'*32,START=data['start'],SIZE=data['end']-data['start']+1,DISK_SIZE=total,SECTOR_SIZE=4096)
                device=dict(serial='fixture',model='fixture',slot=slot,cid='a'*32,storage=storage)
                plan=dict(gap_start=16*2097152,openwrt_bytes=4<<30)
                store=Store(root/'state');record=store.create({'adapter':'e5'},device,{'method':'part'},plan)
                adapter=Adapter.__new__(Adapter);adapter.root=root;adapter.ctx=Context(store,record,lambda *_:None)
                adapter.legacy=SimpleNamespace(android_tools=Mock(),push=Mock(),default_hotspot_name=lambda:'fixture')
                adapter.read_gpt=Mock(return_value=dict(space,first=first,last=last))
                adapter.action=Mock();adapter.reboot=Mock();adapter.adb=Mock()
                with self.assertRaises(Pending):adapter.e5_free(SimpleNamespace(grow_fs=True))
                path=Path(record['legacy']);state=json.loads(path.read_text())
                self.assertEqual(state['android_slot'],slot[1:]);self.assertNotEqual(state['android_slot'],state['linux_slot'])
                api=runpy.run_path(str(TOP/'openwrt/bundle/part-two-stage.py'))
                wrong='b' if slot=='_a' else 'a'
                with self.assertRaisesRegex(ValueError,'slots changed'):
                    api['finish']({'F':str(root/'files'),'current_slots':lambda:(wrong,slot[1:])},None,path,state)

    def test_free_space_resume_recovers_roles_only_from_bound_host_record(self):
        api=runpy.run_path(str(TOP/'openwrt/bundle/part-two-stage.py'))
        for slot in ('_a','_b'):
            with self.subTest(slot=slot),tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/'transaction.json'
                data=dict(node='sda76',cid='a'*32,start=4096,size=8000000)
                state=dict(root_start=8004096,serial='fixture',data=data)
                record=dict(package={'adapter':'e5'},choices={'method':'part'},
                    plan={'gap_start':state['root_start']},effects={'gpt-free-space':{'state':'done'}},
                    legacy=str(path),device=dict(slot=slot,serial='fixture',cid='a'*32,
                        storage=dict(NODE=data['node'],CID=data['cid'],START=data['start'],SIZE=data['size'])))
                gui=SimpleNamespace(ctx=SimpleNamespace(record=record),saved=Mock())
                api['recover_free_slots']({'GUI':gui},path,state)
                self.assertEqual(state['android_slot'],slot[1:])
                self.assertEqual(state['linux_slot'],'b' if slot=='_a' else 'a')
                self.assertEqual(json.loads(path.read_text())['android_slot'],slot[1:])
                gui.saved.assert_called_once()
                for fault in ('slot','serial','cid'):
                    invalid=dict(root_start=8004096,serial='fixture',data=data)
                    old=record['device'][fault];record['device'][fault]='wrong'
                    with self.assertRaisesRegex(ValueError,'evidence'):
                        api['recover_free_slots']({'GUI':gui},path,invalid)
                    record['device'][fault]=old
                    self.assertNotIn('android_slot',invalid)

    def test_real_identity_helper_upload_during_probe_without_transaction(self):
        # Do not mock storage_script()/push(): their real call chain used to
        # dereference a missing transaction context before inventory was read.
        for slot in ('_a','_b'):
            with self.subTest(slot=slot),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);(root/'files').mkdir()
                scripts=root/'scripts/installer';scripts.mkdir(parents=True)
                (scripts/'storage-api.py').write_bytes((TOP/'installer/backend/storage.py').read_bytes())
                (scripts/'storage-lib.sh').write_bytes((TOP/'boot/storage.sh').read_bytes())
                (root/'OpenWrt-Installer.py').write_bytes((TOP/'openwrt/bundle/flash.py').read_bytes())
                (root/'files/device-profile.json').write_text(json.dumps({'profile':'changxing60','android_models':['ZTE 7552N']}))
                helper=b'fixture read-only UFS descriptor executable'
                (root/'files/e5-ufs-id').write_bytes(helper)
                transport=Transport(slot);remote_files={};commands=[]
                def adb_call(*args,**kwargs):
                    commands.append(args)
                    if args[0]=='push':
                        remote_files[args[2]]=Path(args[1]).read_bytes();return ''
                    self.assertEqual(args[0],'shell')
                    words=shlex.split(args[1]);command=words[2] if words[:2]==['su','-c'] else args[1]
                    self.assertTrue(command.startswith('(set -e; '),command)
                    body=command[len('(set -e; '):].split('); e5_stage_rc=',1)[0]
                    words=shlex.split(body);result=''
                    if words[:2]==['rm','-f']:remote_files.pop(words[2],None)
                    elif words[0]=='sha256sum':result=hashlib.sha256(remote_files[words[1]]).hexdigest()+'  file'
                    elif words[0]=='cat':remote_files[words[3]]=remote_files.get(words[3],b'')+remote_files.pop(words[1])
                    elif words[:2]==['chmod','755']:self.assertEqual(remote_files[words[2]],helper)
                    else:self.fail('Unexpected identity staging: '+body)
                    return result+'\n__E5_STAGE_STATUS__=0\n'
                package={'path':str(root),'adapter':'e5'}
                adapter=Adapter(package)
                with patch('backend.adapter.Adb',return_value=transport),patch.object(adapter.legacy,'adb',side_effect=adb_call),redirect_stdout(io.StringIO()) as output:
                    device=adapter.probe('fixture-cx60','part')
                    with self.assertRaisesRegex(ValueError,'Writes are unavailable'):
                        adapter.action('misc-trial',lambda:self.fail('probe wrote misc'))
                self.assertIsNone(adapter.ctx)
                self.assertEqual(device['storage']['DISK'],'sdb')
                self.assertEqual(list(remote_files.values()),[helper])
                self.assertTrue(any(c[0]=='push' for c in commands))
                self.assertFalse(any('/dev/block/' in ' '.join(c) for c in commands))
                self.assertEqual(output.getvalue(),'')

    def test_both_android_slots_and_encrypted_4kn_non_sda_inventory(self):
        for slot in ('_a', '_b'):
            with self.subTest(slot=slot):
                adapter = Adapter.__new__(Adapter)
                adapter.ctx = None
                adapter.root = TOP
                adapter.kind = 'e5'
                adapter.adb = Transport(slot)
                adapter.bind = Mock()
                adapter.legacy = SimpleNamespace(
                    package_profile=lambda: {'profile':'changxing60', 'android_models':['ZTE 7552N']},
                    storage_script=Mock(return_value='export E5_UFS_ID_HELPER=/fixture/helper\n'))
                device = adapter.probe('fixture-cx60', 'part')
                self.assertTrue(device['encrypted'])
                self.assertEqual(device['storage']['DISK'], 'sdb')
                self.assertEqual(device['storage']['SECTOR_SIZE'], 4096)
                self.assertEqual(adapter.legacy.LINUX_SLOT, 'b' if slot == '_a' else 'a')
                package = {'adapter':'e5', 'profile':'changxing60', 'raw_bytes':2 << 30}
                device['gpt']['safe_gaps'] = []
                plan = plan_install(package, device, {'method':'part', 'size_gib':4,
                    'format_userdata':True, 'grow':True})
                self.assertEqual(plan['openwrt_bytes'], 4 << 30)
                self.assertTrue(plan['wipe'])
                self.assertTrue(plan['two_stage'])
                self.assertFalse(any(' of=' in script for script in adapter.adb.commands))

    def test_ufs_request_handoff_and_wrong_parent_rejection(self):
        api = runpy.run_path(str(TOP/'openwrt/bundle/part-two-stage.py'))
        boot = runpy.run_path(str(TOP/'openwrt/tests/installer-slot-roles.py'))['boot']()
        control = '00'*32
        request = 'e5.part_prepare='+':'.join(('c'*32, 'a'*32, 'sdb76',
            '4096', '33550336', '16773120', '16777216', control))
        self.assertTrue(api['boot_command'](boot, request).startswith(b'ANDROID!'))
        state = {'data':{'node':'sdb76','cid':'a'*32,'start':4096},
                 'target':16773120,'need':16777216}
        rows = {'userdata':{**state['data'],'size':state['target']},
                'e5root':{'node':'sdb77','cid':'a'*32,'start':16777216,'size':16777216}}
        self.assertEqual(api['verify_geometry'](state, rows), '/dev/block/sdb77')
        rows['e5root']['node'] = 'sdc77'
        with self.assertRaises(ValueError):
            api['verify_geometry'](state, rows)

    def test_two_stage_gpt_backup_is_decoded_from_real_4kn_headers(self):
        api = runpy.run_path(str(TOP/'openwrt/bundle/part-two-stage.py'))
        first, last, total = snapshot(4096)
        def read(_label, command):
            if command.startswith('cat /sys/'):
                return f'{total}\n4096'
            return (first[4096:8192] if 'skip=1 ' in command else last[-4096:]).hex()
        disk, sectors, ranges = api['gpt_ranges']({'su_checked':read}, {'node':'sdb76'})
        info = gpt.gpt_space(first, last, total)
        self.assertEqual((disk, sectors), ('sdb', total))
        self.assertEqual(ranges, [('gpt-primary.bin',0,info['front']),
                                 ('gpt-backup.bin',info['tail'],total-info['tail'])])


if __name__ == '__main__':
    unittest.main()
