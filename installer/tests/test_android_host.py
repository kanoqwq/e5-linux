"""Android host behavior without Java, USB devices, SSH or block writes."""
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import runpy
import stat
import subprocess
import socket
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch
import zipfile

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE));sys.path.insert(0,str(HERE/'mobile/python'))
# Reuse the exact vendored pure-Python dependency shipped in the APK.
sys.path.insert(0,str(HERE/'mobile/vendor/adb_shell-0.4.4-py3-none-any.whl'))
from android_host.adb import Driver,Registry
from android_host.files import extract_package
from android_host.files import extract_recovery
from android_host.bundle import export_bundle
from android_host.runtime import Runtime,Provider
from backend import host,storage
from backend.transport import Adb
from mobile.apk import validate as validate_apk


class AndroidHostTests(unittest.TestCase):
    def test_apk_validation_checks_abi_and_embedded_wheel_page_alignment(self):
        def elf(alignment):
            data=bytearray(120);data[:6]=b'\x7fELF\x02\x01'
            struct.pack_into('<Q',data,32,64);struct.pack_into('<HH',data,54,56,1)
            struct.pack_into('<I',data,64,1);struct.pack_into('<Q',data,112,alignment)
            return bytes(data)
        with tempfile.TemporaryDirectory() as temp:
            for size in (4096,16384):
                wheel=io.BytesIO()
                with zipfile.ZipFile(wheel,'w') as z:z.writestr('cryptography/native.so',elf(size))
                path=Path(temp)/str(size)
                with zipfile.ZipFile(path,'w') as z:
                    z.writestr('lib/arm64-v8a/libopenwrt_tools_mobile.so',elf(16384))
                    z.writestr('assets/chaquopy/requirements-common.imy',wheel.getvalue())
                if size==4096:
                    with self.assertRaisesRegex(ValueError,'16 KiB'):validate_apk(path,'aarch64')
                else:self.assertEqual(validate_apk(path,'aarch64')['native_libraries'],2)
                with self.assertRaisesRegex(ValueError,'ABI'):validate_apk(path,'x86_64')

    def test_explicit_host_drives_adb_without_desktop_executable_or_process(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(host,'_provider',None):
            driver=Mock();driver.run.return_value=subprocess.CompletedProcess([],0,b'phone-result\n',b'')
            provider=Mock();provider.adb.return_value=driver
            host.install(provider)
            with patch('backend.transport.subprocess.run',side_effect=AssertionError('desktop process')):
                a=Adb(temp,'usb:authorized');self.assertEqual(a.exe,'android-adb')
                self.assertEqual(a.call('shell','id -u'),'phone-result\n')
                driver.run.assert_called_once_with(['shell','id -u'],timeout=20,context=None)
            with self.assertRaises(RuntimeError):host.install(Mock())

    def test_embedded_resources_cannot_escape_and_standalone_storage_remains_usable(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(host,'_provider',None):
            path=Path(temp);(path/'storage-e5.sh').write_bytes((HERE/'runtime/storage-e5.sh').read_bytes())
            host.install(type('Provider',(),{'resources':path})())
            self.assertIn('e5_storage_identity()',storage.library())
            copied=runpy.run_path(str(HERE/'backend/storage.py'))
            self.assertEqual(copied['library'](),storage.library())
            with self.assertRaises(ValueError):host.resource('../outside')

    def registry(self,temp,client=None):
        r=Registry(Mock(),temp);r.clients['usb:test']=client or Mock();return r

    def test_shell_keeps_payload_bytes_and_remote_nonzero_exit(self):
        for code in (0,7):
            with self.subTest(code=code),tempfile.TemporaryDirectory() as temp:
                client=Mock();r=self.registry(temp,client)
                def stream(command,**kwargs):
                    self.assertNotIn('timeout_s',kwargs)
                    token=re.search('__OPENWRT_EXIT_[a-f0-9]+__',command)[0]
                    value=b'hello\r\n\x00world\n'+token.encode()+str(code).encode()+b'\n'
                    return (value[n:n+3] for n in range(0,len(value),3))
                client.streaming_shell.side_effect=stream
                result=Driver(r,'usb:test').run(['shell','sh -c true'])
                self.assertEqual(result.returncode,code);self.assertEqual(result.stdout,b'hello\r\n\x00world')

    def test_streamed_output_does_not_extend_the_command_deadline(self):
        with tempfile.TemporaryDirectory() as temp:
            client=Mock();client.streaming_shell.return_value=iter([b'progress'])
            registry=self.registry(temp,client)
            with patch('android_host.adb.time.monotonic',side_effect=[0,2]):
                with self.assertRaises(subprocess.TimeoutExpired):Driver(registry,'usb:test').run(['shell','long command'],timeout=1)
            self.assertNotIn('usb:test',registry.clients)

    def test_missing_remote_exit_is_failure_and_connection_is_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            client=Mock();client.streaming_shell.return_value=iter([b'incomplete reply'])
            r=self.registry(temp,client)
            with self.assertRaises(ConnectionError):Driver(r,'usb:test').run(['shell','true'])
            client.close.assert_called_once();self.assertNotIn('usb:test',r.clients)

    def test_binary_exec_streams_to_file_without_text_codec(self):
        with tempfile.TemporaryDirectory() as temp:
            client=Mock();payload=bytes(range(256))*4096
            client.streaming_shell.return_value=iter([payload[:100],payload[100:]])
            driver=Driver(self.registry(temp,client),'usb:test');sink=io.BytesIO()
            result=driver.run(['exec-out','cat /fixture'],sink=sink)
            self.assertEqual(sink.getvalue(),payload);self.assertEqual(result.stdout,b'')

    def test_binary_backup_reports_actual_bytes_and_accepts_safe_cancel(self):
        with tempfile.TemporaryDirectory() as temp:
            destination=Path(temp)/'backup';driver=Mock()
            def pull(remote,local,progress,timeout):
                self.assertEqual(timeout,45);local.write_bytes(b'fixture')
                progress(7,7)
            driver.pull.side_effect=pull
            a=Adb.__new__(Adb);a.driver=driver
            context=Mock();a._pull_backup('/fixture',destination,7,'boot_a',context)
            context.transfer.assert_called_once();context.boundary.assert_called_once()
            self.assertEqual(context.transfer.call_args.args[:3],('backup/boot_a',7,7))
            context.boundary.side_effect=RuntimeError('cancelled')
            with self.assertRaisesRegex(RuntimeError,'cancelled'):a._pull_backup('/fixture',destination,7,'boot_a',context)

    def test_reboot_uses_adb_service_and_never_shell_or_host_command(self):
        with tempfile.TemporaryDirectory() as temp:
            client=Mock();r=self.registry(temp,client)
            self.assertEqual(Driver(r,'usb:test').run(['reboot']).returncode,0)
            client.reboot.assert_called_once();client.streaming_shell.assert_not_called();client.close.assert_called_once()

    @unittest.skipUnless(importlib.util.find_spec('adb_shell'),'mobile ADB dependency is not installed in this desktop test environment')
    def test_real_adb_library_tcp_framing_and_remote_exit_on_loopback(self):
        errors=[];ready=threading.Event()
        server=socket.socket();server.bind(('127.0.0.1',0));server.listen(1);server.settimeout(5)
        endpoint='127.0.0.1:'+str(server.getsockname()[1])
        def receive(sock):
            def exact(count):
                data=bytearray()
                while len(data)<count:
                    chunk=sock.recv(count-len(data))
                    if not chunk:raise EOFError('fixture closed')
                    data.extend(chunk)
                return bytes(data)
            command,arg0,arg1,size,checksum,magic=struct.unpack('<6I',exact(24));payload=exact(size)
            assert command^0xffffffff==magic and sum(payload)&0xffffffff==checksum
            return command.to_bytes(4,'little'),arg0,arg1,payload
        def send(sock,command,arg0,arg1,payload=b''):
            value=int.from_bytes(command,'little')
            sock.sendall(struct.pack('<6I',value,arg0,arg1,len(payload),sum(payload)&0xffffffff,value^0xffffffff)+payload)
        def device():
            try:
                with server.accept()[0] as sock:
                    sock.settimeout(5)
                    self.assertEqual(receive(sock)[0],b'CNXN')
                    send(sock,b'CNXN',0x01000000,4096,b'device::ro.product.name=fixture;\0')
                    command,local,_,payload=receive(sock);self.assertEqual(command,b'OPEN');self.assertTrue(payload.startswith(b'shell:'))
                    token=re.search(b'__OPENWRT_EXIT_[a-f0-9]+__',payload)[0]
                    send(sock,b'OKAY',1,local)
                    send(sock,b'WRTE',1,local,b'wireless\x00fixture\n'+token+b'7\n')
                    self.assertEqual(receive(sock)[0],b'OKAY')
                    send(sock,b'CLSE',1,local);receive(sock)
            except BaseException as error:errors.append(error)
            finally:server.close();ready.set()
        thread=threading.Thread(target=device,daemon=True);thread.start()
        with tempfile.TemporaryDirectory() as temp:
            usb=Mock();usb.enumerate.return_value='[]';registry=Registry(usb,temp)
            Driver(registry,None).run(['connect',endpoint])
            result=Driver(registry,endpoint).run(['shell','echo fixture'])
            self.assertEqual(result.returncode,7);self.assertEqual(result.stdout,b'wireless\x00fixture')
            registry.close(endpoint)
        self.assertTrue(ready.wait(6));self.assertEqual(errors,[])

    def package(self,path,extra=()):
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('release/files/device-profile.json','{}')
            z.writestr('release/OpenWrt-Installer.py','# fixture')
            for name,data in extra:z.writestr(name,data)

    def test_package_import_reuses_one_verified_archive_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive=root/'release.zip';self.package(archive)
            events=[];first=extract_package(archive,root/'packages',lambda *e:events.append(e))
            second=extract_package(archive,root/'packages')
            self.assertEqual(first,second);self.assertEqual(first.name,'release')
            self.assertEqual(len(list((root/'packages').iterdir())),1)
            self.assertTrue(events);self.assertEqual(events[-1][1]['done'],events[-1][1]['total'])

    def test_package_import_rejects_escape_duplicate_symlink_and_capacity_shortfall(self):
        for bad in ('escape','duplicate','symlink','capacity'):
            with self.subTest(bad=bad),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);archive=root/'release.zip'
                self.package(archive,[('../outside','x')] if bad=='escape' else [('release/OpenWrt-Installer.py','x')] if bad=='duplicate' else [])
                if bad=='symlink':
                    with zipfile.ZipFile(archive,'a') as z:
                        item=zipfile.ZipInfo('release/link');item.external_attr=(stat.S_IFLNK|0o777)<<16;z.writestr(item,'/outside')
                if bad=='capacity':
                    with patch('android_host.files.shutil.disk_usage',return_value=type('Space',(),{'free':0})()):
                        with self.assertRaises(ValueError):extract_package(archive,root/'packages')
                else:
                    with self.assertRaises(ValueError):extract_package(archive,root/'packages')
                self.assertFalse((root/'outside').exists())
                self.assertFalse(any((p/'.mobile-import.json').exists() for p in (root/'packages').iterdir()))

    def test_windows_recovery_paths_move_without_changing_backed_up_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive=root/'recovery.zip';old=str(Path('C:/Users/fixture/transactions')/('a'*32)).replace('/','\\')
            record=dict(version=1,id='a'*32,data_directory=old,legacy=old+r'\legacy-backups\transaction.json')
            original=b'private backup fixture, not NV'
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('transaction.json',json.dumps(record))
                z.writestr('legacy-backups/transaction.json',json.dumps({'backup_dir':old+r'\backup','remote':'/data/local/tmp/fixture'}))
                z.writestr('backup/boot.bin',original)
            directory=extract_recovery(archive,root/'imports')
            current=json.loads((directory/'transaction.json').read_text())
            self.assertEqual(current['data_directory'],str(directory))
            legacy=Path(current['legacy']);self.assertTrue(legacy.is_file())
            self.assertEqual(json.loads(legacy.read_text())['backup_dir'],str(directory/'backup'))
            self.assertEqual(json.loads(legacy.read_text())['remote'],'/data/local/tmp/fixture')
            self.assertEqual((directory/'backup/boot.bin').read_bytes(),original)
            self.assertEqual(json.loads((directory/'transaction.json.mobile-original').read_text()),record)
            self.assertEqual(extract_recovery(archive,root/'imports'),directory)

    def test_input_cache_deduplicates_equal_content_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime=Runtime.__new__(Runtime);runtime.directory=Path(temp)
            def incoming(name,payload):
                path=runtime.directory/'imports'/name/'release.zip';path.parent.mkdir(parents=True);path.write_bytes(payload);return path
            first=runtime.retain_input(incoming('one',b'full-release-fixture'))
            second=runtime.retain_input(incoming('two',b'full-release-fixture'))
            self.assertEqual(first,second);self.assertEqual(len(list((runtime.directory/'imports').iterdir())),1)
            Path(first).write_bytes(b'tampered')
            with self.assertRaises(ValueError):runtime.retain_input(incoming('three',b'full-release-fixture'))

    def test_embedded_legacy_adb_and_python_run_without_host_subprocesses(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);provider=Provider(root,root,Mock(),lambda *_:None)
            adapter=SimpleNamespace(kind='e5',root=root,legacy=SimpleNamespace(),adb=SimpleNamespace(serial='usb:test'),ctx=None)
            provider.bind_legacy(adapter)
            driver=Mock();driver.run.return_value=subprocess.CompletedProcess([],0,b'raw\x00bytes',b'')
            provider.adb=Mock(return_value=driver)
            result=adapter.legacy.subprocess.run(['android-adb','-s','usb:other','exec-out','cat /fixture'])
            self.assertEqual(result.stdout,b'raw\x00bytes');provider.adb.assert_called_once_with('usb:other')
            script=root/'helper.py';script.write_text('import sys\nprint("fixture-"+sys.argv[1])\n')
            previous=sys.argv
            result=adapter.legacy.subprocess.run([sys.executable,str(script),'unicode-测试'],check=True)
            self.assertEqual(result.stdout.decode().strip(),'fixture-unicode-测试');self.assertIs(sys.argv,previous)
            with self.assertRaises(ValueError):adapter.legacy.subprocess.run(['unknown-desktop.exe'])

    def test_patch_import_accepts_raw_ota_and_desktop_wrapper_and_deduplicates(self):
        from test_ota import package
        for wrapper in (False, True):
            with self.subTest(wrapper=wrapper), tempfile.TemporaryDirectory() as temp:
                runtime=Runtime.__new__(Runtime);runtime.directory=Path(temp)
                ota=Path(temp)/'fixture.ota';package(ota)
                expected=ota.read_bytes()
                for index in range(2):
                    path=Path(temp)/'imports'/str(index)/'selected.zip';path.parent.mkdir(parents=True)
                    if wrapper:
                        with zipfile.ZipFile(path,'w') as archive:archive.write(ota,'nested/update.ota')
                    else:path.write_bytes(expected)
                    selected=runtime.import_patch(path)
                    self.assertEqual(Path(selected).read_bytes(),expected)
                self.assertEqual(len(list((Path(temp)/'imports').iterdir())),1)

    def test_embedded_converter_timeout_restores_thread_trace(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);script=root/'converter.py';script.write_text('while True:\n    pass\n')
            provider=Provider(root,root,Mock(),lambda *_:None)
            previous=sys.gettrace()
            with self.assertRaises(TimeoutError):provider.run_helper(Mock(),root,script,[],.01)
            self.assertIs(sys.gettrace(),previous)

    def test_extracted_directory_import_stays_private_and_reuses_content_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);runtime=Runtime.__new__(Runtime);runtime.directory=root
            for name in ('one','two'):
                source=root/'imports'/name/'directory';(source/'files').mkdir(parents=True)
                (source/'files/device-profile.json').write_text('{}')
                selected=runtime.import_directory(source,'a'*64)
                self.assertTrue(Path(selected).is_relative_to(root/'packages'))
                self.assertTrue((Path(selected)/'files/device-profile.json').is_file())
            self.assertEqual(len(list((root/'packages').iterdir())),1)
            with self.assertRaises(ValueError):runtime.import_directory(root/'outside','a'*64)

    def test_index_tool_reuses_cli_raw_rootfs_and_parallel_batch_semantics(self):
        import shutil
        from types import SimpleNamespace
        from test_ota import mu_release
        from backend.release_index import read_index
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);resources=root/'resources';resources.mkdir()
            shutil.copyfile(HERE/'release-index.py',resources/'release-index.py')
            runtime=Runtime.__new__(Runtime);runtime.emit=Mock()
            collection=root/'collection';collection.mkdir()
            for version in (1,2):
                meta=dict(profile='mu300',image_version='mu300-r'+str(version),kernel_release='7.2.8',openwrt='25.12.5')
                release=root/str(version)
                mu_release(release,meta['image_version'],{'etc/mu300/release.json':json.dumps(meta).encode(),'usr/lib/test':str(version).encode()})
                if version==1:
                    with zipfile.ZipFile(collection/'one.zip','w') as z:
                        for path in release.rglob('*'):
                            if path.is_file():z.write(path,'release/'+path.relative_to(release).as_posix())
                else:shutil.copyfile(release/'payload/mu300-openwrt.tar.gz',collection/'two.tar.gz')
            (collection/'broken.zip').write_bytes(b'incomplete fixture')
            with patch.object(host,'_provider',SimpleNamespace(resources=resources)):
                output=root/'raw.json'
                runtime.index(dict(path=str(collection/'two.tar.gz'),output=str(output)))
                self.assertEqual(read_index(output)['release']['boot_digest'],'0'*64)
                supplied=root/'boot.img';supplied.write_bytes(b'optional boot fixture')
                with_boot=root/'with-boot.json'
                runtime.index(dict(path=str(collection/'two.tar.gz'),output=str(with_boot),boot=str(supplied)))
                import hashlib
                self.assertEqual(read_index(with_boot)['release']['boot_digest'],hashlib.sha256(supplied.read_bytes()).hexdigest())
                self.assertEqual(runtime.index(dict(path=str(collection)),plan=True),dict(batch=True,inputs=3,raw=False))
                result=runtime.index(dict(path=str(collection),output=str(root/'batch.zip'),jobs=2))
            self.assertEqual(result['succeeded'],2);self.assertEqual(len(result['failed']),1)
            with zipfile.ZipFile(root/'batch.zip') as z:
                self.assertEqual(len([n for n in z.namelist() if n.endswith('.owrt-index.json')]),2)
                self.assertEqual(json.loads(z.read('results.json'))['succeeded'],2)

    def bundle(self,root,tamper=False):
        import hashlib
        data={'OpenWrt-Diff-Studio.exe':b'exe fixture','tools/backend/library':b'shared runtime',
              'OpenWrt-Diff-Studio.app/Contents/MacOS/openwrt-installer':b'mac fixture'}
        manifest={'protocol':1,'files':{k:hashlib.sha256(v).hexdigest() for k,v in data.items()}}
        source=root/'release.zip'
        with zipfile.ZipFile(source,'w') as z:
            z.writestr('release/diff-tool.json',json.dumps(manifest))
            for name,value in data.items():z.writestr('release/'+name,b'changed' if tamper else value)
        patch=root/'update.ota';patch.write_bytes(b'one OTA payload')
        apk=root/'host.apk';apk.write_bytes(b'installed APK fixture')
        return source,patch,apk

    def test_mobile_diff_bundle_contains_one_patch_and_shared_desktop_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,patch,apk=self.bundle(root);output=root/'export.zip'
            export_bundle(patch,output,source,apk)
            with zipfile.ZipFile(output) as z:
                self.assertEqual(z.read('update.ota'),patch.read_bytes())
                self.assertEqual(z.read('OpenWrt-Tools.apk'),apk.read_bytes())
                self.assertEqual(z.read('OpenWrt-Updater.exe'),b'exe fixture')
                self.assertEqual(z.read('tools/backend/library'),b'shared runtime')
                self.assertEqual(len(z.namelist()),len(set(z.namelist())))
                self.assertNotIn('OpenWrt-Diff-Studio.exe',z.namelist())

    def test_corrupt_desktop_runtime_cannot_be_exported_as_valid_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,patch,apk=self.bundle(root,True);output=root/'export.zip'
            with self.assertRaises(ValueError):export_bundle(patch,output,source,apk)
            self.assertFalse(output.exists());self.assertFalse(output.with_name(output.name+'.building').exists())


if __name__=='__main__':unittest.main()
