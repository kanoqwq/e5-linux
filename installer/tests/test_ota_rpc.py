"""rpcd HTTP session metadata and post-commit plugin registration regressions."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


@unittest.skipUnless(os.name == 'posix', 'rpcd uses Linux locks')
class RPCTests(unittest.TestCase):
    def setUp(self):
        directory = Path(__file__).resolve().parents[1]/'runtime/luci-app-unisoc-ota/root/usr/lib/unisoc-ota'
        sys.path.insert(0, str(directory)); self.addCleanup(lambda: sys.path.remove(str(directory)))
        spec = importlib.util.spec_from_file_location('ota_rpc_regression', directory/'rpc.py')
        self.rpc = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.rpc)

    def call(self, method, arguments):
        with patch.object(sys, 'argv', ['rpc.py','call',method]), patch.object(sys, 'stdin', io.StringIO(json.dumps(arguments))), redirect_stdout(io.StringIO()):
            self.rpc.main()

    def test_authenticated_metadata_is_accepted_for_every_method(self):
        for method in ('status','inspect','start','rollback_check','rollback','discard_recovery','reboot'):
            args = {'ubus_rpc_session': 'a'*32}
            if method == 'start': args['id'] = 'b'*64
            with patch.object(self.rpc, 'dispatch', return_value={}) as dispatch:
                self.call(method, args)
                dispatch.assert_called_once_with(method, None, 'b'*64 if method == 'start' else None)

    def test_other_arguments_still_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unexpected RPC'):
            self.call('status', {'ubus_rpc_session':'a'*32,'command':'not-allowed'})
        with self.assertRaisesRegex(ValueError, 'must be an object'):
            self.call('status', ['not-an-object'])

    def test_bootstrap_passes_review_id_for_generic_web_upload(self):
        with patch.object(sys,'argv',['rpc.py','start','/target/etc/unisoc-ota/upload.ota','b'*64]), \
             patch.object(self.rpc,'dispatch',return_value={}) as dispatch, redirect_stdout(io.StringIO()):
            self.rpc.main()
        dispatch.assert_called_once_with('start','/target/etc/unisoc-ota/upload.ota','b'*64)

    def test_start_acknowledges_before_any_bulk_preparation_and_holds_lock(self):
        import fcntl
        with tempfile.TemporaryDirectory() as d:
            self.rpc.STATE=Path(d);self.rpc.JOB=Path(d)/'job.json'
            def launch(argv,**kwargs):
                state=json.loads(self.rpc.JOB.read_text())
                self.assertEqual((state['status'],state['phase'],state['patch_id']),('running','queued','b'*64))
                self.assertEqual(argv[2],'_prepare_apply')
                self.assertTrue(kwargs['pass_fds'])
                with (Path(d)/'job.lock').open('a') as other:
                    with self.assertRaises(BlockingIOError):fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
                return Mock()
            with patch.object(self.rpc.subprocess,'Popen',side_effect=launch), \
                 patch.object(self.rpc,'Patch',side_effect=AssertionError('HTTP must not inspect/copy the archive')):
                result=self.rpc.worker('apply',str(Path(d)/'upload.ota'),'b'*64)
            self.assertEqual(result,{'status':'running','patch_id':'b'*64,'phase':'queued'})

    def test_spawn_failure_cannot_leave_a_phantom_running_job(self):
        with tempfile.TemporaryDirectory() as d:
            self.rpc.STATE=Path(d);self.rpc.JOB=Path(d)/'job.json'
            with patch.object(self.rpc.subprocess,'Popen',side_effect=OSError('fixture spawn failure')):
                with self.assertRaises(OSError):self.rpc.worker('apply',str(Path(d)/'upload.ota'),'b'*64)
            state=json.loads(self.rpc.JOB.read_text())
            self.assertEqual(state['status'],'failed')
            self.assertIn('spawn failure',state['result']['error'])

    def test_first_web_start_prepares_then_enters_worker_without_recursive_start(self):
        import fcntl
        for ram in ('','/'):
            with self.subTest(ram=ram), tempfile.TemporaryDirectory() as d:
                root=Path(d);self.rpc.ROOT=d;self.rpc.STATE=root/'etc/unisoc-ota';self.rpc.STATE.mkdir(parents=True)
                self.rpc.JOB=self.rpc.STATE/'job.json';upload=self.rpc.STATE/'upload.ota';upload.write_bytes(b'fixture reviewed payload')
                home=Path('/proc/1/root' if ram else d)/'tmp'/('unisoc-ota-'+'b'*64)
                with (self.rpc.STATE/'job.lock').open('a') as held, \
                     patch.dict(os.environ,{'UNISOC_OTA_RAM':ram}), \
                     patch.object(self.rpc,'Patch') as archive, \
                     patch.object(self.rpc,'prepare_runtime',return_value=home) as prepare, \
                     patch.object(self.rpc.subprocess,'run',return_value=Mock(returncode=0,stdout=json.dumps({'id':'b'*64,'applicable':True}),stderr='')) as run, \
                     patch.object(self.rpc.os,'execve',side_effect=SystemExit) as execute:
                    archive.return_value.__enter__.return_value.id='b'*64
                    fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    with self.assertRaises(SystemExit):self.rpc.prepare_apply(str(upload),'b'*64,held.fileno())
                    prepare.assert_called_once()
                    self.assertEqual(prepare.call_args.kwargs,{'root':'/proc/1/root' if ram else d})
                    setup=run.call_args.args[0]
                    self.assertEqual(setup[-2:],[ 'inspect','/tmp/unisoc-ota-'+'b'*64])
                    argv=execute.call_args.args[1]
                    self.assertEqual(argv[-4:],['/ota/rpc.py','_worker','apply','/target/etc/unisoc-ota/reviewed-'+'b'*64+'.ota'])
                    self.assertEqual(execute.call_args.args[2]['UNISOC_OTA_ROOT'],'/target')
                    if ram:self.assertEqual(setup[:3],['/ota/busybox','chroot','/proc/1/root'])
                self.assertFalse(upload.exists())
                self.assertEqual((self.rpc.STATE/'active-runtime').read_text(),'/tmp/unisoc-ota-'+'b'*64+'/root\n')
                self.assertEqual(json.loads(self.rpc.JOB.read_text())['phase'],'applying')
                self.assertFalse(list(self.rpc.STATE.glob('candidate-*')))

    def test_changed_upload_fails_before_runtime_or_system_writes(self):
        with tempfile.TemporaryDirectory() as d:
            self.rpc.STATE=Path(d);self.rpc.JOB=Path(d)/'job.json';upload=Path(d)/'upload.ota';upload.write_bytes(b'changed')
            with (Path(d)/'job.lock').open('a') as held, patch.object(self.rpc,'Patch') as archive, \
                 patch.object(self.rpc,'prepare_runtime') as prepare, patch.object(self.rpc.os,'execve') as execute:
                archive.return_value.__enter__.return_value.id='c'*64
                self.rpc.prepare_apply(str(upload),'b'*64,held.fileno())
            self.assertEqual(json.loads(self.rpc.JOB.read_text())['status'],'failed')
            self.assertTrue(upload.exists())
            self.assertFalse(list(Path(d).glob('candidate-*')))
            prepare.assert_not_called();execute.assert_not_called()

    def test_reload_uses_stock_service_and_health_checks_before_success(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); service=root/'etc/init.d/rpcd'; service.parent.mkdir(parents=True); service.touch()
            self.rpc.ROOT = d
            result = Mock(returncode=0, stdout=json.dumps({'identity':{'version':'test'}}), stderr='')
            with patch.dict(os.environ, {'UNISOC_OTA_RAM':'/'}), patch.object(self.rpc,'mounted',return_value=True), patch.object(self.rpc.subprocess,'run',return_value=result) as run:
                self.assertEqual(self.rpc.activate_rpc(), {'status':'ready'})
                self.assertEqual(run.call_args_list[0].args[0], ['/ota/busybox','chroot',d,'/etc/init.d/rpcd','reload'])
                self.assertEqual(run.call_args_list[1].args[0][-1], 'ubus call unisoc-ota status')

    def test_ram_activation_binds_host_service_runtime_before_reload(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); service=root/'etc/init.d/rpcd'; service.parent.mkdir(parents=True); service.touch(); self.rpc.ROOT=d
            missing=Mock(returncode=1); ok=Mock(returncode=0,stdout='{"identity":{}}',stderr='')
            with patch.dict(os.environ, {'UNISOC_OTA_RAM':'/'}), patch.object(self.rpc,'mounted',return_value=False), patch.object(self.rpc.subprocess,'run',side_effect=[ok,ok,ok,ok]) as run:
                self.assertEqual(self.rpc.activate_rpc(), {'status':'ready'})
                self.assertEqual(run.call_args_list[0].args[0], ['/ota/busybox','mount','--bind','/proc/1/root/tmp',d+'/tmp'])
                self.assertEqual(run.call_args_list[1].args[0], ['/ota/busybox','mount','--bind','/proc/1/root/proc',d+'/proc'])
                self.assertEqual(run.call_args_list[2].args[0][-2:], ['/etc/init.d/rpcd','reload'])

    def test_mount_table_decodes_paths_and_matches_exact_bind_mount(self):
        with tempfile.TemporaryDirectory() as d:
            table = Path(d)/'mountinfo'
            table.write_text('31 20 0:1 / /target rw - ext4 /dev/root rw\n'
                             '32 31 0:2 / /target/tmp rw - tmpfs tmpfs rw\n'
                             r'33 31 0:2 / /target/with\040space rw - tmpfs tmpfs rw'+'\n')
            self.assertTrue(self.rpc.mounted('/target/tmp', table))
            self.assertTrue(self.rpc.mounted('/target/with space', table))
            self.assertFalse(self.rpc.mounted('/target/tm', table))
            self.assertFalse(self.rpc.mounted('/target/tmp/child', table))

    def test_reload_failure_is_warning_not_filesystem_failure(self):
        with tempfile.TemporaryDirectory() as d:
            service=Path(d)/'etc/init.d/rpcd'; service.parent.mkdir(parents=True); service.touch(); self.rpc.ROOT=d
            with patch.object(self.rpc.subprocess,'run',return_value=Mock(returncode=1,stdout='',stderr='reload failed')):
                self.assertEqual(self.rpc.activate_rpc()['status'], 'warning')

    def test_worker_publishes_complete_after_activation(self):
        events=[]
        result=Mock(returncode=0,stdout='{"status":"complete"}',stderr='')
        with patch.object(sys,'argv',['rpc.py','_worker','apply','']), patch.object(self.rpc.subprocess,'run',return_value=result), patch.object(self.rpc,'activate_rpc',side_effect=lambda:(events.append('reload') or {'status':'ready'})), patch.object(self.rpc,'atomic_json',side_effect=lambda path,data:events.append(data)):
            self.rpc.main()
        self.assertEqual(events[0], 'reload')
        self.assertEqual(events[1]['status'], 'complete')
        self.assertEqual(events[1]['result']['activation'], {'status':'ready'})

    def test_failed_worker_does_not_activate_partial_files(self):
        result=Mock(returncode=1,stdout='{"error":"write failed"}',stderr='')
        with patch.object(sys,'argv',['rpc.py','_worker','apply','']), patch.object(self.rpc.subprocess,'run',return_value=result), patch.object(self.rpc,'activate_rpc') as activate, patch.object(self.rpc,'atomic_json') as publish:
            self.rpc.main()
        activate.assert_not_called()
        self.assertEqual(publish.call_args.args[1]['status'], 'failed')

    def test_completion_waits_for_worker_lock_before_reboot_is_offered(self):
        import fcntl
        with tempfile.TemporaryDirectory() as d:
            self.rpc.STATE=Path(d); self.rpc.JOB=Path(d)/'job.json'
            self.rpc.JOB.write_text('{"status":"complete"}')
            with patch.object(self.rpc,'Engine') as engine:
                engine.return_value.status.return_value={'identity':{'version':'test'}}
                with (Path(d)/'job.lock').open('a') as held:
                    fcntl.flock(held, fcntl.LOCK_EX|fcntl.LOCK_NB)
                    self.assertEqual(self.rpc.dispatch('status')['job']['status'], 'running')
                self.assertEqual(self.rpc.dispatch('status')['job']['status'], 'complete')

    def test_reconciled_completion_retains_the_requested_patch_identity(self):
        with tempfile.TemporaryDirectory() as d:
            self.rpc.STATE=Path(d);self.rpc.JOB=Path(d)/'job.json'
            self.rpc.JOB.write_text(json.dumps({'status':'running','command':'apply','patch_id':'b'*64,'phase':'applying'}))
            with patch.object(self.rpc,'Engine') as engine:
                engine.return_value.status.return_value={'transaction':{'status':'complete','id':'b'*64}}
                result=self.rpc.dispatch('status')['job']
            self.assertEqual((result['status'],result['command'],result['patch_id']),('complete','apply','b'*64))

    def test_reboot_ack_window_retains_job_lock_until_child_exits(self):
        import fcntl
        import subprocess
        import shutil
        with tempfile.TemporaryDirectory() as d:
            self.rpc.STATE=Path(d)
            launch=subprocess.Popen
            children=[]
            def harmless(command, **kwargs):
                command=list(command); command[-1]=shutil.which('true')
                kwargs['stderr']=subprocess.PIPE
                child=launch(command,**kwargs);children.append(child);return child
            try:
                with patch.dict(os.environ,{'UNISOC_OTA_RAM':''}), patch.object(self.rpc.subprocess,'Popen',side_effect=harmless):
                    self.assertEqual(self.rpc.dispatch('reboot')['status'],'rebooting')
                with (Path(d)/'job.lock').open('a') as held:
                    with self.assertRaises(BlockingIOError): fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    _, stderr = children[0].communicate(timeout=10)
                    self.assertEqual(children[0].returncode,0,stderr.decode())
                    fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
            finally:
                for child in children:
                    if child.poll() is None: child.terminate();child.wait()

    def test_reboot_prompt_compares_boot_id_without_clearing_undo_history(self):
        with tempfile.TemporaryDirectory() as d:
            self.rpc.STATE=Path(d); self.rpc.JOB=Path(d)/'job.json'
            with patch.object(self.rpc,'Engine') as engine, patch.object(self.rpc,'boot_id',return_value='boot-now'):
                engine.return_value.status.return_value={'identity':{'version':'r2'},'transaction':{'status':'complete'}}
                for completed, expected in (('boot-now',True),('boot-before',False),(None,None)):
                    record={'status':'complete','result':{}}
                    if completed: record['completed_boot_id']=completed
                    self.rpc.JOB.write_text(json.dumps(record))
                    status=self.rpc.dispatch('status')
                    self.assertIs(status['reboot_required'],expected)
                    self.assertEqual(status['transaction']['status'],'complete')
                    self.assertEqual(json.loads(self.rpc.JOB.read_text()),record)

    def test_discard_cannot_race_a_worker_or_reboot(self):
        import fcntl
        with tempfile.TemporaryDirectory() as d:
            self.rpc.STATE=Path(d)
            with patch.object(self.rpc,'Engine') as engine:
                with (Path(d)/'job.lock').open('a') as held:
                    fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    with self.assertRaises(BlockingIOError):self.rpc.dispatch('discard_recovery')
                engine.return_value.discard_recovery.assert_not_called()
                self.rpc.dispatch('discard_recovery')
                engine.return_value.discard_recovery.assert_called_once()
