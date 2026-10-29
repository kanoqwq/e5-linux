#!/usr/bin/env python3
"""NV maintenance against disposable synthetic files; never physical devices."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

TOP = Path(__file__).resolve().parents[2]
SOURCE = TOP/'openwrt/luci-app-t551m/root/usr/lib/e5/nv_persistence.py'
spec = importlib.util.spec_from_file_location('nv_persistence', SOURCE)
sys.path.insert(0, str(SOURCE.parent))
nv = importlib.util.module_from_spec(spec); sys.modules['nv_persistence'] = nv; spec.loader.exec_module(nv)


def image(value=1, tail=b'untouched padding'):
    payload = struct.pack('<4H', value, 2, 3, 4)
    checksum = 0xffff - sum(struct.unpack('<4H', payload))
    return struct.pack('<4I', 0x4e56, len(payload), checksum, 1).ljust(512, b'\0') + payload + tail


class NVTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.store = self.root/'opt/e5/nv'
        self.store.mkdir(parents=True)
        (self.root/'etc/e5').mkdir(parents=True)
        (self.root/'etc/e5/device-profile').write_text('e5\n')
        (self.root/'etc/e5/modem-slot').write_text('b\n')
        (self.root/'proc/sys/kernel/random').mkdir(parents=True)
        (self.root/'proc/sys/kernel/random/boot_id').write_text('first-boot')
        self.devices = {}
        for name in nv.NAMES:
            data = image() if name.startswith(('nr_fixnv', 'nr_runtimenv')) else b'opaque stock calibration'
            source = self.root/('raw-'+name); source.write_bytes(data)
            self.devices[name] = source; (self.store/name).write_bytes(data)
        nv.atomic_json(self.store/'factory-sha256.json', {n: nv.digest(p) for n,p in self.devices.items()})
        self.manager = nv.Manager(self.root, self.devices)

    def request(self):
        plan = self.manager.preview('from-original')
        return dict(direction='from-original', confirmation='yes', token=plan['token'], conflict_ack=plan['conflict'])

    def import_nv(self):
        with patch.object(nv.subprocess, 'run') as run:
            result = self.manager.sync(self.request())
            run.assert_called_once_with(['/etc/init.d/e5-vendor','stop'],check=True,timeout=60)
        return result

    def test_all_shared_models_both_firmware_slots(self):
        for profile in nv.PROFILES:
            for slot in ('a','b'):
                (self.root/'etc/e5/device-profile').write_text(profile)
                (self.root/'etc/e5/modem-slot').write_text(slot)
                status = self.manager.status()
                self.assertTrue(status['can_import']); self.assertTrue(status['same'])
                self.assertFalse(status['can_export']); self.assertEqual(status['firmware_slot'],slot)

    def test_unknown_model_slot_and_ambiguous_current_are_disabled(self):
        for profile,slot in [('unknown','a'),('e5',''),('e5','current')]:
            (self.root/'etc/e5/device-profile').write_text(profile)
            (self.root/'etc/e5/modem-slot').write_text(slot)
            (self.root/'proc/cmdline').write_text('androidboot.slot_suffix=_a sprdboot.slot_suffix=_b')
            self.assertFalse(self.manager.status()['can_import'])

    def test_dynamic_b_slot_is_not_hardcoded_a(self):
        (self.root/'etc/e5/modem-slot').write_text('current')
        (self.root/'proc/cmdline').write_text('androidboot.slot_suffix=_b console=x')
        self.assertEqual(self.manager.inspect()['firmware_slot'],'b')

    def test_corrupt_header_checksum_missing_origin_and_erased_runtime(self):
        target = self.store/'nr_fixnv1_a'; valid = target.read_bytes()
        for broken in (b'x', b'\0'*600, image()[:512]+b'broken payload'):
            target.write_bytes(broken); self.assertFalse(self.manager.status()['can_import'])
        target.write_bytes(valid)
        (self.store/'nr_runtimenv1').write_bytes(b'\0'*len(valid))
        self.assertTrue(self.manager.status()['validated'])
        (self.store/'nr_runtimenv1').write_bytes(b'\0'*20+b'x')
        self.assertFalse(self.manager.status()['validated'])
        (self.store/'factory-sha256.json').unlink()
        self.assertFalse(self.manager.status()['can_import'])

    def test_large_untrusted_payload_length_rejected_before_allocation(self):
        (self.store/'nr_fixnv1_a').write_bytes(struct.pack('<4I',0x4e56,0xfffffffe,1,1))
        self.assertFalse(self.manager.status()['can_import'])

    def test_opaque_source_change_disables_import(self):
        self.devices['calinv'].write_bytes(b'x'*self.devices['calinv'].stat().st_size)
        self.assertEqual(self.manager.status()['import_reason'],'nv_opaque_source_changed')
        with self.assertRaisesRegex(nv.NVError,'nv_opaque_source_changed'): self.request()

    def test_import_replaces_changed_work_even_if_original_hash_unchanged(self):
        (self.store/'nr_fixnv1_a').write_bytes(image(42))
        before = {n:p.read_bytes() for n,p in self.devices.items()}
        result = self.import_nv()
        self.assertEqual((self.store/'nr_fixnv1_a').read_bytes(),image())
        self.assertEqual(before,{n:p.read_bytes() for n,p in self.devices.items()})
        self.assertTrue(result['reboot_required'])
        self.assertTrue(self.manager.status()['same'])

    def test_confirmed_conflict_primary_reconciles_only_candidate(self):
        (self.store/'nr_fixnv1_a').write_bytes(image(2))
        self.devices['nr_fixnv1_a'].write_bytes(image(3))
        request = self.request(); self.assertTrue(request['conflict_ack'])
        with patch.object(nv.subprocess,'run') as run:
            with self.assertRaisesRegex(nv.NVError,'nv_conflict'):
                self.manager.sync(dict(request, conflict_ack=False))
            run.assert_not_called()
            self.manager.sync(request)
        self.assertEqual((self.store/'nr_fixnv2_a').read_bytes(),image(3))
        self.assertEqual(self.devices['nr_fixnv2_a'].read_bytes(),image())

    def test_yes_token_and_source_staleness_never_stop_modem(self):
        request = self.request()
        with patch.object(nv.subprocess,'run') as run:
            for data in (dict(request,confirmation='YES'),dict(request,token='')):
                with self.assertRaises(nv.NVError): self.manager.sync(data)
            self.devices['nr_fixnv1_b'].write_bytes(image(31))
            with self.assertRaisesRegex(nv.NVError,'nv_preview_expired'): self.manager.sync(request)
            run.assert_not_called()

    def test_expired_preview_rejected(self):
        request=self.request(); path=self.manager.ram/'preview.json'
        data=nv.read_json(path); data['time']-=301; nv.atomic_json(path,data)
        with self.assertRaisesRegex(nv.NVError,'nv_preview_expired'): self.manager.sync(request)

    def test_missing_device_identity_cannot_write_original_even_with_yes(self):
        with patch.object(nv.subprocess,'run') as run:
            with self.assertRaisesRegex(nv.NVError,'nv_device_unknown'):
                self.manager.preview('to-original')
            for operation in (self.manager.start,self.manager.sync):
                with self.assertRaisesRegex(nv.NVError,'nv_preview_expired'):
                    operation(dict(direction='to-original',confirmation='yes'))
            run.assert_not_called()

    def test_complete_private_backups_and_reboot_status(self):
        result=self.import_nv(); backup=self.root/result['backup'].lstrip('/')
        self.assertTrue(nv.read_json(backup/'record.json')['complete'])
        record=nv.read_json(backup/'record.json')
        for name in nv.NAMES:
            for group in ('work','source'):
                    p=backup/group/(name+'.gz'); self.assertEqual(p.stat().st_mode & 0o777,0o600)
                    expected=record['work'][name] if group=='work' else record['source'][name]['sha256']
                    nv.verify_snapshot(backup/group/name,expected,(self.store/name).stat().st_size)
        self.assertFalse((backup/'previous-working').exists())
        self.assertEqual(backup.stat().st_mode & 0o777,0o700)
        self.assertTrue(self.manager.status()['last_sync']['reboot_required'])
        (self.root/'proc/sys/kernel/random/boot_id').write_text('second-boot')
        self.assertFalse(self.manager.status()['last_sync']['reboot_required'])

    def test_import_gzip_preserves_full_tails_without_third_working_copy(self):
        for name,path in self.devices.items():
            data=path.read_bytes().ljust(1<<20,b'\0');path.write_bytes(data);(self.store/name).write_bytes(data)
        nv.atomic_json(self.store/'factory-sha256.json',{n:nv.digest(p) for n,p in self.devices.items()})
        before={n:nv.digest(p) for n,p in self.devices.items()}
        result=self.import_nv();backup=self.root/result['backup'].lstrip('/')
        size=sum(p.stat().st_size for p in backup.rglob('*') if p.is_file())
        self.assertLess(size,(2*len(nv.NAMES)*(1<<20))//50)
        self.assertFalse((backup/'previous-working').exists())
        for name in nv.NAMES:
            for group in ('source','work'):nv.verify_snapshot(backup/group/name,before[name],1<<20)
        self.assertEqual(before,{n:nv.digest(p) for n,p in self.devices.items()})

    def legacy_backup(self):
        path=self.manager.state/'backups/update-20261025-123456-012345abcdef'
        path.mkdir(parents=True)
        for name in nv.NAMES:shutil.copyfile(self.store/name,path/name)
        nv.atomic_json(path/'record.json',{'kind':'update-preservation'})
        return path

    def test_compress_legacy_update_backup_without_touching_active_nv(self):
        path=self.legacy_backup();before={n:nv.digest(self.store/n) for n in nv.NAMES}
        with self.assertRaises(nv.NVError):self.manager.compress_backup({'id':path.name,'confirmation':'YES'})
        with patch.object(nv.subprocess,'run') as run:
            self.assertTrue(self.manager.compress_backup({'id':path.name,'confirmation':'yes'})['backup_compressed'])
        run.assert_not_called()
        for name in nv.NAMES:
            nv.verify_snapshot(path/name,before[name],(self.store/name).stat().st_size)
            self.assertFalse((path/name).exists())
        self.assertEqual(before,{n:nv.digest(self.store/n) for n in nv.NAMES})

    def test_compress_legacy_import_removes_only_verified_duplicate_working(self):
        result=self.import_nv();backup=self.root/result['backup'].lstrip('/')
        shutil.copytree(self.store,backup/'previous-working')
        for group in ('source','work'):
            for name in nv.NAMES:
                with nv.snapshot_reader(backup/group/name) as src:(backup/group/name).write_bytes(src.read())
                (backup/group/(name+'.gz')).unlink()
        record=nv.read_json(backup/'record.json');record.pop('storage');record.pop('raw_bytes');nv.atomic_json(backup/'record.json',record)
        before={n:nv.digest(self.store/n) for n in nv.NAMES}
        self.manager.compress_backup({'id':backup.name,'confirmation':'yes'})
        self.assertFalse((backup/'previous-working').exists())
        self.assertFalse(self.manager.backups()['entries'][0]['can_compress'])
        self.assertEqual(before,{n:nv.digest(self.store/n) for n in nv.NAMES})

    def test_compression_worker_is_async_and_does_not_create_nv_sync_history(self):
        backup=self.legacy_backup();nv.safe_directory(self.manager.ram)
        with patch.object(nv.subprocess,'Popen') as launch:
            queued=self.manager.start({'id':backup.name,'confirmation':'yes'},'compress_backup')
        launch.assert_called_once();self.assertEqual(queued['job']['operation'],'compress_backup')
        request=next(self.manager.ram.glob('request-*.json'))
        nv.run_worker(request,self.manager)
        result=nv.read_json(self.manager.ram/'job.json')
        self.assertEqual(result['state'],'complete');self.assertTrue(result['result']['backup_compressed'])
        self.assertFalse(request.exists());self.assertFalse((self.manager.state/'last-sync.json').exists())

    def test_failed_gzip_never_publishes_partial_or_loses_original(self):
        path=self.legacy_backup();source=path/nv.NAMES[0];before=source.read_bytes()
        with patch.object(nv.shutil,'copyfileobj',side_effect=OSError('injected compression failure')):
            with self.assertRaises(OSError):nv.save_snapshot(source,source)
        self.assertEqual(source.read_bytes(),before);self.assertFalse(list(path.glob('*.gz')))
        self.assertFalse(list(path.glob('.snapshot-*')))

    def test_interrupted_old_backup_cleanup_can_resume_without_losing_bytes(self):
        backup=self.legacy_backup();target=backup/nv.NAMES[1];real_unlink=Path.unlink
        before={n:(backup/n).read_bytes() for n in nv.NAMES}
        def unlink(path,*a,**k):
            if path==target:raise OSError('injected cleanup interruption')
            return real_unlink(path,*a,**k)
        with patch.object(Path,'unlink',unlink),self.assertRaises(OSError):
            self.manager.compress_backup({'id':backup.name,'confirmation':'yes'})
        self.manager.compress_backup({'id':backup.name,'confirmation':'yes'})
        for name in nv.NAMES:
            with nv.snapshot_reader(backup/name) as stream:self.assertEqual(stream.read(),before[name])
        self.assertFalse(self.manager.backups()['entries'][0]['can_compress'])

    def test_stop_failure_preserves_work_and_error_record(self):
        before={n:(self.store/n).read_bytes() for n in nv.NAMES}
        with patch.object(nv.subprocess,'run',side_effect=RuntimeError('stop failed')):
            with self.assertRaisesRegex(nv.NVError,'nv_import_failed'): self.manager.sync(self.request())
        self.assertEqual(before,{n:(self.store/n).read_bytes() for n in nv.NAMES})
        state=nv.read_json(self.manager.state/'last-sync.json')
        self.assertFalse(state['ok']); self.assertEqual(state['cause'],'stop failed')

    def test_second_rename_failure_restores_previous_work(self):
        request=self.request(); before={n:(self.store/n).read_bytes() for n in nv.NAMES}
        rename=os.rename
        def fail(src,dst):
            if Path(src).name=='import': raise OSError('injected commit failure')
            return rename(src,dst)
        with patch.object(nv.subprocess,'run'),patch.object(nv.os,'rename',side_effect=fail):
            with self.assertRaises(nv.NVError): self.manager.sync(request)
        self.assertEqual(before,{n:(self.store/n).read_bytes() for n in nv.NAMES})
        nv.recover_import(self.root)
        self.assertFalse((self.root/'opt/e5/nv-import.json').exists())

    def test_power_loss_in_directory_gap_restores_validated_previous(self):
        backup=self.root/'etc/e5-nv/backups/20261024-123456-123456abcdef'; backup.mkdir(parents=True)
        nv.atomic_json(backup/'record.json',{'work':{n:nv.digest(self.store/n) for n in nv.NAMES}})
        nv.atomic_json(self.root/'opt/e5/nv-import.json',{'backup':backup.relative_to(self.root).as_posix()})
        os.rename(self.store,backup/'previous-working')
        nv.recover_import(self.root)
        self.assertTrue(self.manager.status()['validated'])
        self.assertFalse((self.root/'opt/e5/nv-import.json').exists())
        self.assertTrue(nv.read_json(self.manager.state/'last-sync.json')['recovered'])

    def test_import_power_loss_after_commit_records_success_and_no_reboot_prompt(self):
        self.devices['nr_fixnv1_a'].write_bytes(image(7))
        result=self.import_nv();backup=self.root/result['backup'].lstrip('/')
        nv.atomic_json(self.root/'opt/e5/nv-import.json',{'backup':backup.relative_to(self.root).as_posix()})
        nv.recover_import(self.root)
        record=nv.read_json(self.manager.state/'last-sync.json')
        self.assertTrue(record['ok']);self.assertTrue(record['recovered']);self.assertFalse(record['reboot_required'])

    def test_recovery_never_uses_arbitrary_path(self):
        nv.atomic_json(self.root/'opt/e5/nv-import.json',{'backup':'../../somewhere'})
        with self.assertRaises(nv.NVError):nv.recover_import(self.root)

    def test_import_and_update_share_mutex_and_pending_update(self):
        request=self.request()
        with nv.maintenance_lock(self.root):
            with self.assertRaises(BlockingIOError):self.manager.sync(request)
        (self.root/'.e5-update').mkdir();(self.root/'.e5-update/request.ready').touch()
        with self.assertRaisesRegex(nv.NVError,'nv_busy'):self.manager.sync(request)

    def test_prepared_retry_snapshots_do_not_bypass_armed_or_live_updates(self):
        pending=self.root/'.e5-update';pending.mkdir()
        (pending/'request.ready').write_text('previous preparation stopped before boot write\n')
        before={n:p.read_bytes() for n,p in self.devices.items()}
        working={p.name:p.read_bytes() for p in self.store.iterdir() if p.is_file()}
        command=[sys.executable,str(SOURCE),'archive','--lock','--gzip','--root',str(self.root)]
        for _ in range(2):
            result=subprocess.run(command,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(nv.verify_preserved(self.restore(result.stdout)))
        with nv.maintenance_lock(self.root,snapshot=True):
            result=subprocess.run(command,capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn(b'BlockingIOError',result.stderr)
        (pending/'request').write_text('armed\n')
        result=subprocess.run(command,capture_output=True)
        self.assertNotEqual(result.returncode,0);self.assertIn(b'nv_busy',result.stderr)
        self.assertEqual(before,{n:p.read_bytes() for n,p in self.devices.items()})
        self.assertEqual(working,{p.name:p.read_bytes() for p in self.store.iterdir() if p.is_file()})
        self.assertTrue((pending/'request.ready').exists());self.assertTrue((pending/'request').exists())

    def test_real_pc_backup_retries_prepared_request_without_original_fallback(self):
        spec=importlib.util.spec_from_file_location('nv_retry_update',TOP/'tools/e5-update.py')
        updater=importlib.util.module_from_spec(spec);spec.loader.exec_module(updater)
        updater.GUI=SimpleNamespace(backup_base=lambda:str(self.root/'pc'),
            confirm_nv_recovery=unittest.mock.Mock(side_effect=AssertionError('must keep working NV')))
        pending=self.root/'.e5-update';pending.mkdir();(pending/'request.ready').touch()
        stage=self.root/'stage';stage.mkdir();shutil.copyfile(SOURCE,stage/'nv-persistence.py')
        calls=[]
        def execute(client,command):
            calls.append(command)
            command=command.replace('/opt/e5/nv',str(self.store)).replace(' --lock',' --lock --root '+str(self.root))
            result=subprocess.run(['sh','-ec',command],capture_output=True,text=True)
            if result.returncode:raise RuntimeError(result.stderr)
            return result.stdout
        def read(command,**options):
            self.assertEqual(command,'cat '+str(stage/'nv.tar'))
            out=io.BytesIO((stage/'nv.tar').read_bytes());out.channel=SimpleNamespace(recv_exit_status=lambda:0)
            return None,out,io.BytesIO()
        with patch.object(updater,'execute',side_effect=execute),contextlib.redirect_stdout(io.StringIO()):
            for _ in range(2):
                result=updater.save_nv_backup(SimpleNamespace(exec_command=read),str(stage),'e5',compress=True)
                self.assertTrue(nv.verify_preserved(self.restore(result.read_bytes())))
        self.assertEqual(len(calls),2);self.assertFalse(any('factory-archive' in s for s in calls))

    def test_busy_or_transport_errors_are_not_nv_corruption(self):
        spec=importlib.util.spec_from_file_location('nv_busy_update',TOP/'tools/e5-update.py')
        updater=importlib.util.module_from_spec(spec);spec.loader.exec_module(updater)
        updater.GUI=SimpleNamespace(confirm_nv_recovery=unittest.mock.Mock())
        for error in (RuntimeError('NVError: nv_busy'),RuntimeError('BlockingIOError: unavailable'),
                      TimeoutError('SSH timed out'),RuntimeError('device command failed: executable missing')):
            with self.subTest(error=str(error)),patch.object(updater,'execute',side_effect=error) as execute:
                with self.assertRaises((ValueError,RuntimeError,TimeoutError)):
                    updater.save_nv_backup(None,'/fixture','e5')
                self.assertEqual(execute.call_count,1)
        updater.GUI.confirm_nv_recovery.assert_not_called()

    def snapshot(self):
        output=io.BytesIO();self.assertTrue(nv.archive(self.root,output));return output.getvalue()

    def restore(self,data):
        dest=self.root/'new';(dest/'etc/e5').mkdir(parents=True,exist_ok=True)
        (dest/'etc/e5/device-profile').write_text('e5')
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:tar.extractall(dest)
        return dest

    def test_snapshot_preserves_entire_files_tail_origin_and_history(self):
        (self.store/'nr_fixnv1_a').write_bytes(image(5,b'\xff'*65536))
        history=self.root/'etc/e5-nv/backups/old/record.json';history.parent.mkdir(parents=True);history.write_text('{}')
        dest=self.restore(self.snapshot());self.assertTrue(nv.verify_preserved(dest))
        self.assertEqual((dest/'opt/e5/nv/nr_fixnv1_a').read_bytes(),image(5,b'\xff'*65536))
        self.assertEqual((dest/'opt/e5/nv/factory-sha256.json').read_bytes(),(self.store/'factory-sha256.json').read_bytes())
        self.assertTrue((dest/'etc/e5-nv/backups/old/record.json').exists())
        backup=nv.backup_work(dest);self.assertTrue(backup.is_dir())
        self.assertTrue(nv.verify_preserved(dest))

    def test_snapshot_checksum_profile_and_symlink_fail_closed(self):
        dest=self.restore(self.snapshot());(dest/'opt/e5/nv/calinv').write_bytes(b'corrupt')
        with self.assertRaisesRegex(nv.NVError,'nv_preservation_mismatch'):nv.verify_preserved(dest)
        (self.store/'calinv').unlink();(self.store/'calinv').symlink_to(self.devices['calinv'])
        with self.assertRaises(nv.NVError):self.snapshot()

    def test_status_does_not_expose_payload_or_full_source_hashes(self):
        value=json.dumps(self.manager.status());self.assertNotIn('opaque stock',value)
        self.assertNotIn(nv.digest(self.devices['calinv']),value)

    def test_menu_acl_and_rpc_signature_match(self):
        app=TOP/'openwrt/luci-app-t551m/root'
        menu=json.loads((app/'usr/share/luci/menu.d/e5-nv.json').read_text())
        acl=json.loads((app/'usr/share/rpcd/acl.d/e5-nv.json').read_text())['e5-nv']
        self.assertEqual(menu['admin/modem/nv']['depends']['acl'],['e5-nv'])
        self.assertEqual(set(acl['read']['ubus']['e5-nv']+acl['write']['ubus']['e5-nv']),set(nv.METHODS))
        self.assertEqual(set(nv.METHODS['sync']),{'direction','token','confirmation','conflict_ack'})

    def test_insufficient_backup_space_does_not_stop_vendor(self):
        request=self.request()
        with patch.object(nv.shutil,'disk_usage',return_value=SimpleNamespace(free=0)),patch.object(nv.subprocess,'run') as run:
            with self.assertRaisesRegex(nv.NVError,'nv_backup_space'):self.manager.sync(request)
            run.assert_not_called()

    def test_no_physical_file_is_ever_opened_for_writing(self):
        request=self.request(); original=Path.open
        def guarded(path,mode='r',*args,**kwargs):
            if path in self.devices.values() and any(x in mode for x in 'wax+'):
                raise AssertionError('physical NV write attempted')
            return original(path,mode,*args,**kwargs)
        with patch.object(Path,'open',guarded),patch.object(nv.subprocess,'run'):
            self.manager.sync(request)

    def test_async_worker_completes_and_cleans_request_and_rejects_duplicate(self):
        request=self.request()
        with patch.object(nv.subprocess,'Popen') as launch:
            response=self.manager.start(request)
            launch.assert_called_once()
            self.assertEqual(response['job']['state'],'queued')
            self.assertTrue(self.manager.status()['busy'])
            with self.assertRaisesRegex(nv.NVError,'nv_busy'):self.manager.start(request)
        staged=next(self.manager.ram.glob('request-*.json'))
        with patch.object(nv.subprocess,'run'):nv.run_worker(staged,self.manager)
        self.assertFalse(staged.exists())
        self.assertEqual(self.manager.status()['job']['state'],'complete')

    def test_async_worker_failure_and_launch_failure_are_visible(self):
        request=self.request()
        with patch.object(nv.subprocess,'Popen',side_effect=OSError('launch failure')):
            with self.assertRaises(OSError):self.manager.start(request)
        self.assertEqual(self.manager.status()['job']['state'],'failed')
        self.assertFalse(list(self.manager.ram.glob('request-*.json')))
        with patch.object(nv.subprocess,'Popen') as launch:
            with self.assertRaisesRegex(nv.NVError,'nv_preview_expired'):
                self.manager.start(dict(request,token='invalid'))
            launch.assert_not_called()

    def test_pc_update_keeps_verified_backup_and_retains_failed_download(self):
        spec=importlib.util.spec_from_file_location('nv_update',TOP/'tools/e5-update.py')
        updater=importlib.util.module_from_spec(spec);spec.loader.exec_module(updater)
        updater.GUI=SimpleNamespace(backup_base=lambda:str(self.root/'pc-backups'))
        raw=self.snapshot(); sha=__import__('hashlib').sha256(raw).hexdigest()
        for corrupt in (False,True):
            out=io.BytesIO(raw+(b'corrupt' if corrupt else b''));out.channel=SimpleNamespace(recv_exit_status=lambda:0)
            client=SimpleNamespace(exec_command=lambda *a,**k:(None,out,io.BytesIO()))
            with patch.object(updater,'execute',return_value='NV-BACKUP '+sha+'\n'),contextlib.redirect_stdout(io.StringIO()):
                if corrupt:
                    with self.assertRaisesRegex(ValueError,'NV backup readback failed'):updater.save_nv_backup(client,'/tmp/e5-update-test','e5')
                else:
                    target=updater.save_nv_backup(client,'/tmp/e5-update-test','e5')
                    self.assertEqual(target.read_bytes(),raw)
        records=[json.loads(p.read_text()) for p in (self.root/'pc-backups').rglob('backup.json')]
        self.assertEqual(sorted(record['ok'] for record in records),[False,True])

    def test_valid_original_can_replace_invalid_work_without_changing_raw_nv(self):
        (self.store/'nr_fixnv1_a').write_bytes(b'broken')
        before={n:p.read_bytes() for n,p in self.devices.items()}
        output=io.BytesIO();nv.factory_archive(self.root,output,self.devices)
        target=self.restore(output.getvalue())
        self.assertTrue(nv.verify_preserved(target))
        self.assertEqual((target/'opt/e5/nv/nr_fixnv1_a').read_bytes(),image())
        self.assertEqual(before,{n:p.read_bytes() for n,p in self.devices.items()})
        self.assertEqual(nv.read_json(target/'etc/e5-nv/last-sync.json')['reason'],'confirmed-install-recovery')

    def test_android_original_recovery_uses_binary_reads_and_bound_model_slot(self):
        spec=importlib.util.spec_from_file_location('nv_android_flash',TOP/'openwrt/bundle/flash.py')
        flash=importlib.util.module_from_spec(spec);spec.loader.exec_module(flash)
        mapping={name:f'/dev/block/mmcblk0p{i+1}' for i,name in enumerate(nv.NAMES)}
        by_path={mapping[n]:self.devices[n].read_bytes().ljust(1024,b'\0') for n in nv.NAMES}
        reads=[];uploads=[]
        def pull(call,shell,script,local,expected_size):
            reads.append(script);data=by_path[script.split()[-1]]
            self.assertEqual(expected_size,len(data));local.write_bytes(data)
        def checked(stage,command):
            if 'for u in ' in command:
                return '\n'.join(name+' '+Path(path).name for name,path in mapping.items())
            if command.startswith('cat /sys/class/block/'):
                return '2'
            return __import__('hashlib').sha256(by_path[command.split()[-1]]).hexdigest()
        sys.path.insert(0,str(TOP/'installer'))
        gui=SimpleNamespace(backup_base=lambda:str(self.root/'android-pc'),
            adb=SimpleNamespace(serial='bound-device',call=unittest.mock.Mock(),shell=unittest.mock.Mock()),
            confirm_nv_recovery=unittest.mock.Mock(return_value=True))
        with patch.object(flash,'GUI',gui),patch.object(flash,'SERIAL','bound-device'),patch.object(flash,'ANDROID_SLOT','b'), \
             patch.object(flash,'package_profile',return_value={'profile':'e5'}),patch.object(flash,'su_checked',side_effect=checked), \
             patch.object(flash,'storage_script',return_value=''), \
             patch.object(flash,'F',str(SOURCE.parent)),patch.object(flash.runpy,'run_path',return_value=vars(nv)), \
             patch('backend.binary_io.pull_binary',side_effect=pull), \
             patch.object(flash,'push',side_effect=lambda source,*args:uploads.append(Path(source).read_bytes())):
            flash.original_nv_recovery('working NV failed')
        self.assertEqual(len(reads),9);gui.confirm_nv_recovery.assert_called_once()
        self.assertEqual(len(uploads),1);self.assertTrue(nv.verify_preserved(self.restore(uploads[0])))

    def test_invalid_original_is_never_a_recovery_source(self):
        self.devices['nr_fixnv1_a'].write_bytes(b'bad original')
        with self.assertRaises(nv.NVError):nv.factory_archive(self.root,io.BytesIO(),self.devices)

    def test_pc_update_requires_explicit_consent_after_validating_original(self):
        spec=importlib.util.spec_from_file_location('nv_recovery_update',TOP/'tools/e5-update.py')
        updater=importlib.util.module_from_spec(spec);spec.loader.exec_module(updater)
        raw=self.snapshot();sha=__import__('hashlib').sha256(raw).hexdigest()
        gui=SimpleNamespace(backup_base=lambda:str(self.root/'pc'),confirm_nv_recovery=unittest.mock.Mock(return_value=True))
        updater.GUI=gui
        out=io.BytesIO(raw);out.channel=SimpleNamespace(recv_exit_status=lambda:0)
        client=SimpleNamespace(exec_command=lambda *a,**k:(None,out,io.BytesIO()))
        calls=[]
        def execute(client,command):
            calls.append(command)
            if ' archive --lock' in command:raise RuntimeError('NVError: nv_invalid_checksum')
            if ' factory-archive ' in command:return 'NV-BACKUP '+sha+'\n'
            return ''
        with patch.object(updater,'execute',side_effect=execute),contextlib.redirect_stdout(io.StringIO()):
            result=updater.save_nv_backup(client,'/tmp/e5-update-test','e5')
        self.assertEqual(json.loads((result.parent/'backup.json').read_text())['policy'],'original')
        gui.confirm_nv_recovery.assert_called_once()
        self.assertIn('factory-archive',calls[1])
        gui.confirm_nv_recovery.return_value=False
        with patch.object(updater,'execute',side_effect=execute):
            with self.assertRaisesRegex(ValueError,'declined'):updater.save_nv_backup(client,'/tmp/e5-update-test','e5')

    def test_legacy_confirmation_never_assumes_yes_or_emits_partial_archive(self):
        original_open=open
        for answer,accepted in [('yes\n',True),('YES\n',False),('\n',False)]:
            def opened(path,mode='r',*args,**kwargs):
                if path=='/dev/tty':return io.StringIO(answer if mode=='r' else '')
                return original_open(path,mode,*args,**kwargs)
            output=io.BytesIO()
            with patch('builtins.open',side_effect=opened),patch.object(nv,'factory_archive',side_effect=lambda root,out:out.write(b'verified')):
                if accepted:nv.confirmed_factory_archive(self.root,output)
                else:
                    with self.assertRaisesRegex(nv.NVError,'nv_confirmation_required'):nv.confirmed_factory_archive(self.root,output)
            self.assertEqual(output.getvalue(),b'verified' if accepted else b'')

    def test_userdata_original_preparation_is_immutable_and_ready_checks_hash(self):
        base=self.root/'android-image';base.mkdir();token='b'*32
        original=base/'e5-nv-original.tar'
        with original.open('wb') as stream:nv.factory_archive(self.root,stream,self.devices)
        script=(TOP/'openwrt/bundle/userdata-image.sh').read_text()
        block=script[script.index('nv_phase=1'):script.index('gzip -dc /data/local/tmp/openwrt.ext4.gz >')]
        block=block.replace('/data/local/tmp/',str(base)+'/')
        stage=base/'stage';stage.mkdir()
        prefix=f'''set -eu
stage="{stage}"; nv_token={token}; nvdir="{base}/e5-nv-images/{token}"; D=unused
E5_NV_POLICY=original; nv_mode=--nv-check
sync() {{ :; }}
'''
        first=subprocess.run(['busybox','ash'],input=prefix+block,text=True,capture_output=True)
        self.assertEqual(first.returncode,0,first.stderr)
        ready=base/'e5-nv-images'/token/'ready';self.assertTrue(ready.exists())
        original.unlink();(stage/'view').rmdir()
        second=subprocess.run(['busybox','ash'],input=prefix.replace('nv_mode=--nv-check','nv_mode=--nv-ready')+block,text=True,capture_output=True)
        self.assertEqual(second.returncode,0,second.stderr)
        (base/'e5-nv-images'/token/'nv.tar').write_bytes(b'corrupt')
        (stage/'view').rmdir()
        bad=subprocess.run(['busybox','ash'],input=prefix.replace('nv_mode=--nv-check','nv_mode=--nv-ready')+block,text=True,capture_output=True)
        self.assertNotEqual(bad.returncode,0)

    def test_image_boot_recovery_requires_per_image_cid_and_valid_original(self):
        raw=io.BytesIO();nv.factory_archive(self.root,raw,self.devices)
        new=self.restore(raw.getvalue());old=self.root/'old-image';old.mkdir()
        source=(TOP/'boot/nv-preserve.sh').read_text()
        code=source[source.index('nv_migrate_images()'):]
        code=code.replace('/nv-old',str(old)).replace('/nv-new',str(new))
        code=code.replace('/etc/nv-persistence.py',str(SOURCE))
        code=code.replace('/run/nv-update.tar',str(self.root/'scratch-nv.tar'))
        prefix='''set -eu
losetup() { [ "$1" != -f ] || echo /dev/fake; }
mount() { return 0; }
umount() { :; }
sync() { :; }
nv_archive_offline() { return 1; }
nv_restore_offline() { echo INCORRECT_RESTORE; return 1; }
chroot() { nvroot=$1; shift 2; command python3 "$@" --root "$nvroot"; }
nv_python_offline() { chroot "$1" /usr/bin/python3 - "$2" < "'''+str(SOURCE)+'''"; }
e5_storage_disk() { echo "$TEST_DISK"; }
e5_storage_identity() { [ "$1" = "$TEST_DISK" ] || return 1; printf '%032d\n' 0 | tr 0 a; }
'''
        marker=new/'etc/e5/nv-update-original'
        for value,success in [('',False),('NV-ORIGINAL-RECOVERY-V1 e5 '+'b'*32,False),
                              ('NV-ORIGINAL-RECOVERY-V1 e5 '+'a'*32,True)]:
            if value:marker.write_text(value)
            else:marker.unlink(missing_ok=True)
            for disk in ('mmcblk0','sda'):
                result=subprocess.run(['busybox','ash'],input='TEST_DISK='+disk+'\n'+prefix+code+'\nnv_migrate_images old new || exit 17\n',text=True,capture_output=True)
                self.assertEqual(result.returncode==0,success,(disk,result.stderr))
                self.assertNotIn('INCORRECT_RESTORE',result.stdout)
        # The token is kept until real NV startup: power loss before renaming
        # the image cannot erase the user's already persisted decision.
        self.assertTrue(marker.exists())

    def test_android_reinstall_preserves_nv_before_write_and_resumes_from_receipt(self):
        base=self.root/'android';stage=base/'stage';(stage/'view/dev').mkdir(parents=True)
        shutil.copytree(self.root/'etc',stage/'view/etc')
        shutil.copytree(self.root/'opt',stage/'view/opt')
        shutil.copyfile(SOURCE,base/'e5-nv-persistence.py')
        source=(TOP/'openwrt/bundle/part-write.sh').read_text()
        code=source[source.index('nvbase='):source.index("echo '1/4 writing root image'")]
        code=code.replace('/data/local/tmp/',str(base)+'/')
        token='a'*32
        harness='''set -eu
mount() { [ "$MOUNT_OK" = yes ]; }
umount() { :; }
sync() { :; }
chroot() { nvroot=$1; shift 2; command python3 "$@" --root "$nvroot"; }
'''+f'stage="{stage}"; token={token}; dev="{base}/unreadable"; mounted=0; MOUNT_OK=yes; nv_mode=--nv-check\n'
        first=subprocess.run(['busybox','ash'],input=harness+code,text=True,capture_output=True)
        self.assertEqual(first.returncode,0,first.stderr)
        receipt=base/'e5-nv-backups'/f'{token}.receipt'
        fields=receipt.read_text().split();self.assertEqual(fields[0],'NV-PRESERVATION-V1')
        backup=receipt.parent/fields[2]
        self.assertTrue(backup.is_file());self.assertEqual(nv.digest(backup),fields[3])
        second=subprocess.run(['busybox','ash'],input=harness.replace('MOUNT_OK=yes','MOUNT_OK=no')+code,text=True,capture_output=True)
        self.assertEqual(second.returncode,0,second.stderr)
        self.assertEqual(receipt.read_text().split(),fields)
        receipt.unlink()
        (base/'unreadable').write_bytes(b'unknown damaged root')
        rejected=subprocess.run(['busybox','ash'],input=harness.replace('MOUNT_OK=yes','MOUNT_OK=no')+code,text=True,capture_output=True)
        self.assertNotEqual(rejected.returncode,0)
        self.assertFalse(receipt.exists())

    def test_android_nv_python_binds_entropy_device_and_unmounts_on_failure(self):
        source=(TOP/'openwrt/bundle/part-write.sh').read_text()
        helper=source[source.index('nv_python() {'):source.index('\n[ ! -L "$nvbase" ]')]
        stage=self.root/'python-root';(stage/'view/dev').mkdir(parents=True)
        script=self.root/'empty.py';script.write_text('')
        harness=f'''set -eu
stage="{stage}"; nvhelper="{script}"; nvdevbound=0; active=0
mount() {{ [ "$1" = --bind ] && [ "$2" = /dev ]; active=1; }}
umount() {{ [ "$active" = 1 ]; active=0; }}
chroot() {{ [ "$active" = 1 ] || exit 98; return "$PYTHON_RC"; }}
'''
        for rc in (0,17):
            result=subprocess.run(['busybox','ash'],input=harness+helper+f'''
PYTHON_RC={rc}
rc=0
nv_python verify || rc=$?
[ "$rc" = {rc} ] && [ "$active" = 0 ] && [ "$nvdevbound" = 0 ]
''',text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
        cleanup=source[source.index('cleanup() {'):source.index('trap cleanup EXIT')]
        self.assertLess(cleanup.index('umount "$stage/view/dev"'),cleanup.index('umount "$stage/view"'))

    def test_reinstall_bound_old_preparation_has_no_nv_but_unreadable_filesystem_is_protected(self):
        base=self.root/'prepared';stage=base/'stage';(stage/'view').mkdir(parents=True)
        shutil.copyfile(SOURCE,base/'e5-nv-persistence.py')
        source=(TOP/'openwrt/bundle/part-write.sh').read_text()
        code=source[source.index('nvbase='):source.index("echo '1/4 writing root image'")].replace('/data/local/tmp/',str(base)+'/')
        token='a'*32;prior='c'*32;dev=base/'root'
        harness=f'''set -eu
mount() {{ return 1; }}
sync() {{ :; }}
stage="{stage}"; token={token}; dev="{dev}"; mounted=0; nv_mode=--nv-check
'''
        receipt=base/'e5-nv-backups'/f'{token}.receipt'
        blank=(b'E5-PART-PREPARED-V2 '+prior.encode()+b'\n').ljust(8192,b'\0')
        for fault,accepted in (('unbound',False),('bound',True),('wrong',False),('ext4',False),('f2fs',False)):
            receipt.unlink(missing_ok=True);raw=bytearray(blank)
            if fault=='ext4':raw[1080:1082]=b'\x53\xef'
            if fault=='f2fs':raw[1024:1028]=bytes.fromhex('1020f5f2')
            dev.write_bytes(raw)
            env='' if fault=='unbound' else 'E5_PREPARED_TOKEN='+('d'*32 if fault=='wrong' else prior)+'\n'
            result=subprocess.run(['busybox','ash'],input=harness+env+code,text=True,capture_output=True)
            self.assertEqual(result.returncode==0,accepted,(fault,result.stderr))
            self.assertEqual(receipt.exists(),accepted)
            if accepted:self.assertEqual(receipt.read_text().split()[2],'no')
            self.assertEqual(dev.read_bytes(),bytes(raw))

    def test_legacy_keep_config_cannot_ignore_nv_restore_failure_in_conditional(self):
        source=(TOP/'openwrt/device-install-image.sh').read_text()
        start=source.index('    if [ -e "$from/opt/e5/nv"')
        block=source[start:source.index('\n    fi\n}',start)+len('\n    fi')]
        target=self.root/'new-image';target.mkdir()
        harness=f'''set -eu
from="{self.root}"; N="{target}"
mktemp() {{ command mktemp "{self.root}/nv-test.XXXXXX"; }}
python3() {{
    script=$1; shift
    case "$1" in
        archive) command python3 "{SOURCE}" "$@" ;;
        verify) return 1 ;;
        *) echo INVALID_CONTINUE; return 0 ;;
    esac
}}
keep() {{
{block}
}}
keep || exit 17
echo INVALID_COMMIT
'''
        result=subprocess.run(['busybox','ash'],input=harness,text=True,capture_output=True)
        self.assertEqual(result.returncode,17,result.stderr)
        self.assertNotIn('INVALID_',result.stdout)
        self.assertIn('backup retained',result.stderr)


if __name__=='__main__':unittest.main()
