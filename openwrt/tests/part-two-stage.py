#!/usr/bin/env python3
"""Host transaction guards plus real sparse-image growth and shell progress.

No ADB, physical disks, formatting of userdata, or reboots.
"""
import importlib.util
import contextlib
import os
from pathlib import Path
import runpy
import shutil
import struct
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

TOP = Path(__file__).resolve().parents[2]
api = runpy.run_path(str(TOP/'openwrt/bundle/part-two-stage.py'))
spec = importlib.util.spec_from_file_location('flash', TOP/'openwrt/bundle/flash.py')
flash = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flash)
fixtures = runpy.run_path(str(TOP/'openwrt/tests/installer-slot-roles.py'))


def boot_fixture():
    return bytearray(fixtures['boot']())


class TwoStage(unittest.TestCase):
    def test_old_missing_request_retry_requires_unchanged_gpt_and_is_not_repeated(self):
        import hashlib, json
        for fault in ('none', 'primary', 'secondary', 'missing', 'identity', 'partial',
                      'recovery', 'control', 'writing', 'new-handoff', 'flash', 'rearming', 'boot-backup'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); files = root/'files'; files.mkdir()
                (files/'boot.img').write_bytes(boot_fixture())
                (files/'openwrt.ext4.gz').write_bytes(b'payload')
                digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
                bc = fixtures['control']('b')
                (root/'boot-control-before.bin').write_bytes(bc)
                (root/'boot-a-before.img').write_bytes(boot_fixture())
                (root/'bootchain.tar').write_bytes(b'chain backup fixture')
                if fault == 'boot-backup': (root/'bootchain.tar').unlink()
                original = {'gpt-primary.bin':b'P'*(34*512), 'gpt-backup.bin':b'B'*(33*512)}
                for name, data in original.items(): (root/name).write_bytes(data)
                if fault == 'missing': (root/'gpt-backup.bin').unlink()
                data = dict(node='mmcblk0p73',cid='a'*32,start=2048,size=16777216)
                state = dict(version=2,phase='awaiting-android',token='c'*32,serial='fixture',model='K1',
                    android_slot='b',linux_slot='a',data=data,target=8388608,need=8388608,grow=1,
                    raw_size=2<<30,boot_sha=digest(files/'boot.img'),image_sha=digest(files/'openwrt.ext4.gz'),
                    apn='',ssid='test',key='12345678')
                if fault == 'writing': state['phase']='writing'
                if fault == 'new-handoff': state['request_handoff']=1
                if fault == 'rearming': state.update(phase='rearming',request_handoff=1)
                rows = {'userdata':dict(data)}
                if fault == 'identity': rows['userdata']['cid']='b'*32
                if fault == 'partial': rows['userdata']['size']-=2048
                path = root/'transaction.json'; api['save'](path,state)
                events=[]; remote=[b'']
                def checked(stage, script, **kwargs):
                    if script.startswith('cat /sys'): return '20000000'
                    if 'count=2048' in script and 'od -An' in script:
                        return (b'boot-recovery'.ljust(2048,b'\0') if fault == 'recovery' else bytes(2048)).hex()
                    if 'of=/data/local/tmp/e5-gpt-recheck' in script:
                        name = 'gpt-primary.bin' if 'count=34;' in script else 'gpt-backup.bin'
                        remote[0] = original[name]
                        if fault == ('primary' if name == 'gpt-primary.bin' else 'secondary'):
                            remote[0] = b'X'+remote[0][1:]
                    return ''
                def adb(*args,**kwargs):
                    if args[0]=='pull': Path(args[2]).write_bytes(remote[0])
                    if args[0]=='reboot': events.append('reboot')
                    return 'fixture'
                def flash(g,p):
                    persisted=json.loads(path.read_text())
                    self.assertEqual(persisted['phase'],'rearming')
                    self.assertEqual(persisted['request_handoff'],1)
                    payload=fixtures['unpack_roles'](p.read_bytes())
                    self.assertTrue(payload['etc/e5/boot-request'].startswith(b'e5.part_prepare='))
                    events.append('boot')
                    if fault=='flash': raise ValueError('injected readback failure')
                g=dict(F=str(files),TMP='/data/local/tmp',current_slots=lambda:('b','a'),
                    adb=adb,sh=lambda _: 'K1',sha256=digest,su_checked=checked,
                    su=lambda _:hashlib.sha256(remote[0]).hexdigest()+'  remote',
                    read_boot_control=lambda: bytes(32) if fault=='control' else bc,
                    install_preflight=lambda:events.append('preflight'),
                    arm_slot_b=lambda:events.append('arm'))
                with patch.dict(api['finish'].__globals__,probe=lambda _:rows,flash_boot=flash,
                                wait_android=lambda *args:events.append('wait')):
                    if fault in ('none','rearming'):
                        api['finish'](g,SimpleNamespace(),path,state)
                        self.assertEqual(events,['preflight','boot','arm','reboot','wait'])
                        self.assertEqual(state['phase'],'awaiting-android')
                        events.clear()
                        with self.assertRaises(ValueError): api['finish'](g,SimpleNamespace(),path,state)
                        self.assertEqual(events,[])
                    else:
                        with self.assertRaises((ValueError,FileNotFoundError)):
                            api['finish'](g,SimpleNamespace(),path,state)
                        self.assertNotIn('arm',events); self.assertNotIn('reboot',events)
                        if fault != 'flash': self.assertNotIn('boot',events)
                for name, contents in original.items():
                    if (root/name).exists(): self.assertEqual((root/name).read_bytes(),contents)

    def test_recovery_request_classification_is_exact_and_fail_closed(self):
        self.assertEqual(api['recovery_message_kind'](bytes(2048)), 'empty')
        loader = b'bootonce-bootloader'.ljust(2048, b'\0')
        self.assertEqual(api['recovery_message_kind'](loader), 'bootloader-only')
        bad = [b'', bytes(2047), bytes(2049), b'boot-recovery'.ljust(2048, b'\0'),
               b'update-radio'.ljust(2048, b'\0'), b'boot-fastboot'.ljust(2048, b'\0'),
               b'\xff' * 2048]
        for offset in (20, 32, 64, 832, 900, 2047):
            data = bytearray(loader); data[offset] = 1; bad.append(bytes(data))
        for message in bad:
            with self.subTest(size=len(message), head=message[:24]), self.assertRaises(ValueError):
                api['recovery_message_kind'](message)

    @unittest.skipUnless(os.name != 'nt', 'Requires a POSIX shell and regular files')
    def test_clear_bootloader_command_preserves_slots_and_vendor_tail(self):
        loader = b'bootonce-bootloader'.ljust(2048, b'\0')
        control = bytes(range(32))
        import hashlib
        for shell in (['sh'], ['busybox', 'ash']):
            for fault in ('none', 'new-recovery', 'changed-slot', 'truncated', 'no-op-write'):
                with self.subTest(shell=shell, fault=fault), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'misc'
                    before = loader + control + b'VENDOR-UNCHANGED' * 400
                    if fault == 'new-recovery':
                        before = b'boot-recovery'.ljust(2048,b'\0') + before[2048:]
                    elif fault == 'changed-slot':
                        before = before[:2048] + b'X' * 32 + before[2080:]
                    elif fault == 'truncated':
                        before = before[:2000]
                    path.write_bytes(before)
                    script = api['clear_bootloader_command_script'](loader, control)
                    script = script.replace('/dev/block/by-name/misc', str(path))
                    if fault == 'no-op-write':
                        script = 'dd() { case "$*" in *of=*) return 0;; *) command dd "$@";; esac; }; ' + script
                    result = subprocess.run([*shell, '-c', script], text=True, capture_output=True)
                    if fault == 'none':
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(path.read_bytes(), bytes(32) + before[32:])
                        self.assertEqual(hashlib.sha256(path.read_bytes()[2048:]).digest(),
                                         hashlib.sha256(before[2048:]).digest())
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(path.read_bytes(), before)

    def test_start_backs_up_and_checkpoints_before_clear_and_never_arms_on_failure(self):
        for command, booted, clear_fails in [('loader', True, False), ('loader', True, True),
                                             ('loader', False, False), ('recovery', True, False)]:
            with self.subTest(command=command, booted=booted, clear_fails=clear_fails), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); files = root/'files'; files.mkdir()
                image = boot_fixture(); image[:8] = b'ANDROID!'
                struct.pack_into('<I', image, 40, 4)
                image[44:1580] = b'e5.part_v=2'.ljust(1536,b'\0')
                for name, data in [('boot.img',image),('openwrt.ext4.gz',b'payload'),
                                   ('ext4-tools.tar',b'tools'),('part-write.sh',b'writer')]:
                    (files/name).write_bytes(data)
                raw = (b'bootonce-bootloader' if command == 'loader' else b'boot-recovery').ljust(2048,b'\0')
                events=[]; saved=[]
                class Gui:
                    def backup_base(self): return str(root)
                    def saved(self,path,state):
                        self_path=Path(path)
                        self_data=__import__('json').loads(self_path.read_text())
                        self_bcb=(self_path.parent/'bcb-before.bin').read_bytes()
                        self_state=state.copy()
                        saved.append((self_data,self_bcb,self_state))
                        events.append('checkpoint')
                def checked(stage, script, **options):
                    if 'count=2048' in script and 'od -An' in script: return raw.hex()
                    if 'bcb_hash()' in script:
                        events.append('clear')
                        self.assertIn('checkpoint',events)
                        self.assertEqual(saved[0][1],raw)
                        self.assertEqual(options['effect'],'two-stage-clear-bootloader-command')
                        if clear_fails: raise ValueError('injected clearance failure')
                    return ''
                def adb(*args,**kwargs):
                    if args[0]=='pull': Path(args[2]).write_bytes(b'chain')
                    events.append(args[0]); return 'fixture-serial'
                g=dict(F=str(files),B=str(root),GUI=Gui(),TMP='/data/local/tmp',
                       current_slots=lambda:('b','a'), read_boot_control=lambda:bytes(range(32)),
                       su_checked=checked, sh=lambda cmd:'1' if booted else '0',
                       adb=adb,su=lambda cmd:'f'*64+'  file',sha256=lambda p:'f'*64,
                       bootchain=lambda mode:events.append('chain'),arm_slot_b=lambda:events.append('arm'))
                ns=api['start'].__globals__
                with patch.dict(ns,probe=lambda _: {'userdata':dict(node='mmcblk0p73',cid='a'*32,start=2048,size=16777216)},
                                backup=lambda *a:events.append('backup'),backup_gpt=lambda *a:events.append('gpt-backup'),
                                flash_boot=lambda *a:events.append('boot-readback'),
                                wait_android=lambda *a:events.append('wait')):
                    if command == 'recovery' or not booted or clear_fails:
                        with self.assertRaises(ValueError):
                            api['start'](g,SimpleNamespace(),'mmcblk0p73',2048,4096,1,2<<30,'','test','12345678')
                        self.assertNotIn('arm',events)
                        self.assertNotIn('reboot',events)
                        if command == 'recovery' or not booted:
                            self.assertFalse(saved)
                    else:
                        api['start'](g,SimpleNamespace(),'mmcblk0p73',2048,4096,1,2<<30,'','test','12345678')
                        self.assertLess(events.index('backup'),events.index('checkpoint'))
                        self.assertLess(events.index('boot-readback'),events.index('clear'))
                        self.assertLess(events.index('clear'),events.index('arm'))
                        self.assertLess(events.index('arm'),events.index('reboot'))

    def prepare_case(self, mounted=False, held=False, wrong_cid=False, embedded=False):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('run', 'etc', 'dev', 'sys/class/block/mmcblk0/device',
                         'sys/class/block/mmcblk0p70/holders', 'proc/self', 'usr/bin'):
                (root/name).mkdir(parents=True, exist_ok=True)
            node=root/'sys/class/block/mmcblk0p70'
            (node/'uevent').write_text('PARTNAME=userdata\n')
            (node/'start').write_text('2048\n'); (node/'size').write_text('16777216\n')
            (node/'dev').write_text('179:70\n')
            if held: (node/'holders/dm-0').touch()
            (root/'sys/class/block/mmcblk0/device/cid').write_text(('b' if wrong_cid else 'a')*32+'\n')
            (root/'proc/self/mountinfo').write_text('1 0 179:70 / /data rw - f2fs data rw\n' if mounted else '')
            (root/'dev/mmcblk0p70').touch(); (root/'dev/mmcblk0').touch()
            before=b'Z'*4096; (root/'dev/misc').write_bytes(before)
            bc=bytes(range(32))
            request=':'.join(['c'*32,'a'*32,'mmcblk0p70','2048','16777216','8388608','8388608',bc.hex()])
            (root/'proc/cmdline').write_text('console=tty0 e5.part_prepare='+request+'\n')
            if embedded:
                (root/'proc/cmdline').write_text('console=tty0 rdinit=/init\n')
                (root/'etc/e5').mkdir()
                (root/'etc/e5/boot-request').write_text('e5.part_prepare='+request+'\n')
            (root/'etc/part-progress.sh').write_text(
                'part_progress_start() { :; }\npart_stop() { :; }\nlog() { :; }\n')
            tool=root/'usr/bin/ucode'
            tool.write_text('#!/bin/sh\necho "$2" >> "'+str(root/'calls')+'"\n'
                            'if [ "$2" = list ]; then echo "71 8390656 16779263 e5root"; fi\n')
            tool.chmod(0o755)
            code=((TOP/'boot/boot-args.sh').read_text()+'\n'+
                  (TOP/'boot/part-prepare.sh').read_text()).replace('[ -b ', '[ -f ')
            for prefix in ('/etc','/sys','/proc','/dev','/run','/usr/bin'):
                code=code.replace(prefix+'/', str(root)+prefix+'/')
            code=code.replace(str(root)+'/dev/zero', '/dev/zero').replace(str(root)+'/dev/null', '/dev/null')
            code+='\nmisc="'+str(root/'dev/misc')+'"\npersist() { :; }\nsleep() { :; }\nsync() { :; }\n'
            code+='reboot() { echo reboot >> "'+str(root/'calls')+'"; }\npart_prepare\n'
            # BusyBox ash is the deployed shell; printf %b supports hex there.
            result=subprocess.run(['busybox','ash','-x'], input=code, text=True, capture_output=True, timeout=8)
            calls=(root/'calls').read_text() if (root/'calls').exists() else ''
            control=(root/'dev/misc').read_bytes()
            if mounted or held or wrong_cid:
                self.assertEqual(calls, '')
                self.assertEqual(control, before)
            else:
                self.assertIn('carve-userdata\nlist\nreboot', calls, result.stderr)
                self.assertEqual(control[:13], b'boot-recovery')
                self.assertIn(b'--wipe_data\n', control[64:832])
                self.assertEqual(control[832:2048], bytes(2048 - 832))
                self.assertEqual(control[2048:2080], bc)
                self.assertEqual(control[2080:], before[2080:])
                with (root/'dev/mmcblk0').open('rb') as stream:
                    stream.seek(8390656*512)
                    self.assertTrue(stream.read(100).startswith(b'E5-PART-PREPARED-V2 '+b'c'*32+b'\n'))

    def test_prepare_recovery_handoff_and_reject_busy_or_wrong_device(self):
        for options in ({}, {'mounted':True}, {'held':True}, {'wrong_cid':True},
                        {'embedded':True}, {'embedded':True,'wrong_cid':True},
                        {'embedded':True,'mounted':True}, {'embedded':True,'held':True}):
            with self.subTest(options=options): self.prepare_case(**options)

    def test_boot_command_preserves_all_payloads_and_footer(self):
        boot = boot_fixture()
        boot[:8] = b'ANDROID!'
        struct.pack_into('<I', boot, 40, 4)
        boot[44:1580] = b'console=tty0 e5.part_v=2'.ljust(1536, b'\0')
        result = api['boot_command'](bytes(boot), 'e5.root=part')
        self.assertEqual(result[4096:8224], boot[4096:8224])
        self.assertEqual(result[-64:-52], boot[-64:-52])
        overlay = fixtures['unpack_roles'](result)
        self.assertEqual(overlay['etc/e5/boot-request'], b'e5.root=part\n')
        self.assertEqual(overlay['etc/boot-args.sh'], (TOP/'boot/boot-args.sh').read_bytes())
        self.assertIn(b'e5.root=part', result[44:1580])
        with self.assertRaises(ValueError):
            api['boot_command'](result, 'e5.part_prepare=other')
        boot[44:1580] = bytes(1536)
        with self.assertRaises(ValueError):
            api['boot_command'](bytes(boot), 'e5.root=part')

    def test_encryption_policy_requires_explicit_unattended_erase(self):
        for kind, source in [('file', '/dev/block/mmcblk0p70'), ('file', '/dev/block/dm-0'), ('block', '/dev/block/dm-1')]:
            def sh(cmd):
                return kind if cmd.endswith('type') else 'encrypted'
            with patch.object(flash, 'sh', sh), patch.object(flash, 'su_checked', return_value=f'{source} /data f2fs rw 0 0'):
                with self.assertRaises(SystemExit):
                    flash.part_userdata_policy(SimpleNamespace(y=True, format_userdata=False))
                self.assertTrue(flash.part_userdata_policy(SimpleNamespace(y=True, format_userdata=True)))
        with patch.object(flash, 'sh', return_value=''), patch.object(flash, 'su_checked', return_value='/dev/block/mmcblk0p70 /data f2fs rw 0 0'):
            self.assertFalse(flash.part_userdata_policy(SimpleNamespace(y=True, format_userdata=False)))

    def test_resume_rejects_other_device_and_wrong_extents(self):
        data = dict(node='mmcblk0p70', cid='a'*32, start=2048, size=8388608)
        state = dict(data=data, target=4194304, need=4194304)
        good = dict(userdata=dict(data, size=4194304),
                    e5root=dict(node='mmcblk0p71', cid='a'*32, start=4196352, size=4194304))
        self.assertEqual(api['verify_geometry'](state, good), '/dev/block/mmcblk0p71')
        for field, value in [('cid', 'b'*32), ('start', 4196353), ('size', 4194303), ('node', 'mmcblk1p71')]:
            wrong = dict(good, e5root=dict(good['e5root'], **{field: value}))
            with self.assertRaises(ValueError):
                api['verify_geometry'](state, wrong)

    def test_reinstall_rewrites_e5root_and_keeps_the_settings(self):
        # --reinstall-part: no stage one, no marker, no wipe. The transaction it
        # builds describes the device as it is (userdata untouched, e5root's
        # full extent) and carries the settings the installed system already has.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = root/'files'; files.mkdir()
            boot = boot_fixture()
            boot[:8] = b'ANDROID!'
            struct.pack_into('<I', boot, 40, 4)
            boot[44:1580] = b'console=tty0 e5.part_v=2'.ljust(1536, b'\0')
            (files/'boot.img').write_bytes(bytes(boot))
            (files/'openwrt.ext4.gz').write_bytes(b'\0' * 64)
            (files/'ext4-tools.tar').write_bytes(b'\0')
            (files/'part-write.sh').write_bytes(b'\0')
            seen = {}
            # runpy.run_path hands back a copy of the globals, so the module's
            # own namespace is the one to patch.
            ns = api['reinstall'].__globals__
            original_finish, original_probe = ns['finish'], ns['probe']
            ns['finish'] = lambda g, a, path, state: seen.update(path=path, state=state)
            ns['probe'] = lambda g: {
                'userdata': dict(node='mmcblk0p70', cid='a'*32, start=2048, size=8388608),
                'e5root': dict(node='mmcblk0p71', cid='a'*32, start=8390656, size=20971520)}
            try:
                api['reinstall'](dict(
                    current_slots=lambda: ('a','b'),
                    F=str(files), B=str(root/'package'),
                    find_adb=lambda: None, android_checks=lambda: None,
                    adb=lambda *a, **k: 'SERIAL123', sh=lambda *a, **k: 'T551M',
                    sha256=lambda p: 'f' * 64, gz_usize=lambda p: 2147483648,
                    default_hotspot_name=lambda: 'E5-OpenWrt',
                    confirm=lambda *a, **k: True,
                    su=lambda *a, **k: "E5_APN='internet'\nE5_WIFI_SSID='E5-OpenWrt'\nE5_WIFI_KEY='abcdefgh12'\n"),
                    SimpleNamespace(apn='', ssid='', wifi_key='', y=True))
            finally:
                ns['finish'], ns['probe'] = original_finish, original_probe
            state = seen['state']
            self.assertEqual(state['phase'], 'writing')        # skips the marker, still writes
            self.assertEqual((state['target'], state['need']), (8388608, 20971520))
            self.assertEqual((state['apn'], state['ssid'], state['key']), ('internet', 'E5-OpenWrt', 'abcdefgh12'))
            self.assertEqual(state['grow'], 1)
            api['validate_state'](state)
            self.assertEqual(api['verify_geometry'](state, dict(
                userdata=dict(state['data'], size=state['target']),
                e5root=dict(node='mmcblk0p71', cid='a'*32, start=8390656, size=state['need']))),
                '/dev/block/mmcblk0p71')
            self.assertTrue(seen['path'].is_file())            # the transaction is recorded
            self.assertIn('reinstalls', str(seen['path']))

    def test_installed_settings_are_best_effort(self):
        # The M8's e5root could not be mounted (damaged journal), and su_checked's
        # `set -e` wrapper turned that into a hard failure of the whole rewrite.
        # Reading the old settings must never be fatal: they fall back to the
        # command line and the defaults.
        self.assertEqual(api['installed_settings'](dict(su=lambda *a, **k: '__E5_NO_OLD_SYSTEM__')), {})
        text = "E5_APN='internet'\nE5_WIFI_SSID='X'\nE5_WIFI_KEY='abcdefgh12'\n"
        self.assertEqual(api['installed_settings'](dict(su=lambda *a, **k: text)),
                         {'apn': 'internet', 'ssid': 'X', 'key': 'abcdefgh12'})

    def test_resume_retains_phase_when_root_verification_or_boot_write_fails(self):
        for phase, fault in [('writing','root'), ('installed','boot'), ('installed','chain')]:
            with self.subTest(phase=phase,fault=fault), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); files=root/'files'; files.mkdir()
                boot=boot_fixture(); boot[:8]=b'ANDROID!'
                struct.pack_into('<I', boot, 40, 4)
                boot[44:1580]=b'e5.part_v=2'.ljust(1536,b'\0')
                (files/'boot.img').write_bytes(boot)
                state=dict(version=2,phase=phase,token='c'*32,serial='SERIAL',model='T551M',
                           data=dict(node='mmcblk0p70',cid='a'*32,start=2048,size=8388608),
                           target=4194304,need=4194304,grow=1,raw_size=2147483648,
                           boot_sha='f'*64,image_sha='f'*64,apn='',ssid='test',key='12345678')
                path=root/'transaction.json'; api['save'](path,state)
                rows=dict(userdata=dict(state['data'],size=state['target']),
                          e5root=dict(node='mmcblk0p71',cid='a'*32,start=4196352,size=4194304))
                ns=api['finish'].__globals__; original=ns['probe']; ns['probe']=lambda _:rows
                stages=[]
                def checked(stage,*args,**kwargs):
                    stages.append(stage)
                    raise SystemExit('injected root-readback or boot-write failure')
                chain=[]
                def sync_chain(mode):
                    chain.append(mode)
                    if fault=='chain':raise SystemExit('injected boot-chain failure')
                g=dict(current_slots=lambda:('a','b'),bind_boot_image=lambda data:data,F=str(files),TMP='/data/local/tmp',adb=lambda *a,**k:'SERIAL',
                       android_shell=lambda script,args: 'sh '+script+' '+args,
                       sh=lambda *a,**k:'T551M',sha256=lambda *a:'f'*64,
                       busy=lambda *a:contextlib.nullcontext(),su_checked=checked,
                       install_preflight=lambda:None,bootchain=sync_chain,
                       push=lambda *a:None,pull_device_files=lambda *a:None,
                       arm_slot_b=lambda: self.fail('must not arm a failed installation'))
                try:
                    with self.assertRaises(SystemExit):
                        api['finish'](g,SimpleNamespace(),path,state)
                finally:
                    ns['probe']=original
                self.assertEqual(__import__('json').loads(path.read_text())['phase'],phase)
                self.assertEqual(stages, ['安装独立分区 / install independent root'] if phase=='writing'
                                 else [] if fault=='chain' else ['写入并校验 B 槽 / writing slot B'])
                self.assertEqual(chain, [] if phase=='writing' else ['sync'])

    def test_progress_heartbeat_with_fractional_uptime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'uptime').write_text('123.45 9.00\n')
            code = (TOP/'boot/part-progress.sh').read_text().replace('/run/', str(root)+'/')
            code = code.replace('/proc/uptime', str(root/'uptime')).replace('/dev/kmsg', str(root/'kmsg'))
            code = code.replace('/dev/tty0', str(root/'no-tty'))
            code += '\npart_progress_start\nsleep 3\npart_stop\n'
            p = subprocess.run(['sh'], input=code, text=True, capture_output=True, timeout=8)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertNotIn('arithmetic', p.stderr)
            self.assertIn('elapsed 0s', (root/'kmsg').read_text())
            self.assertFalse((root/'part-install.running').exists())

    def test_real_ext4_two_to_ten_gib_and_noop_rejected(self):
        if not shutil.which('mkfs.ext4') or not shutil.which('resize2fs'):
            self.skipTest('requires Linux ext4 tools')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); image=root/'root.ext4'
            with image.open('wb') as stream:
                stream.truncate(2 << 30)
            subprocess.run(['mkfs.ext4', '-q', '-F', '-b', '4096', str(image)], check=True)
            code=(TOP/'boot/part-progress.sh').read_text().replace('/run/', str(root)+'/')
            code=code.replace('/usr/sbin/resize2fs', shutil.which('resize2fs'))
            command=f'\npart_grow_ext4 "{image}" 20971520\n'
            result=subprocess.run(['sh'], input=code+command, text=True, capture_output=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stderr+(root/'part-growfs.log').read_text())
            with image.open('rb') as stream:
                stream.seek(1028)
                self.assertEqual(struct.unpack('<I', stream.read(4))[0]*4096, 10 << 30)
            # A tool that claims success without resizing must not pass validation.
            fake=root/'no-op'; fake.write_text('#!/bin/sh\nexit 0\n'); fake.chmod(0o755)
            result=subprocess.run(['sh'], input=code.replace(shutil.which('resize2fs'),str(fake))+
                                  f'\npart_grow_ext4 "{image}" 25165824\n', text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
