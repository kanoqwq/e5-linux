"""Embedded protocol endpoint, sharing the desktop transaction coordinator."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import tempfile

from .adb import Registry


class Provider:
    def __init__(self,resources,directory,usb,notify,apk=None):
        self.resources=Path(resources)
        self.directory=Path(directory)
        self.registry=Registry(usb,Path(directory)/'keys',notify)
        self.execution_lock=threading.RLock()
        self.apk=apk

    def adb(self,serial=None):return self.registry.driver(serial)

    def recovery_import_root(self,directory):
        import re
        base=(self.directory/'recovery-imports').resolve()
        relative=Path(directory).resolve().relative_to(base)
        if not relative.parts or not re.fullmatch('[a-f0-9]{64}',relative.parts[0]):
            raise ValueError('Recovery import is outside the app workspace')
        root=base/relative.parts[0]
        marker=root/'.mobile-import.json'
        if root.is_symlink() or not marker.is_file() or json.loads(marker.read_text()).get('sha256')!=root.name:
            raise ValueError('Recovery import ownership could not be verified')
        return root

    def export_bundle(self,patch,output,release):
        from .bundle import export_bundle
        return export_bundle(patch,output,release,self.apk)

    def bind_legacy(self,adapter):
        provider=self
        class Process:
            def __getattr__(self,name):return getattr(subprocess,name)
            def run(self,command,**kw):
                if not isinstance(command,(list,tuple)) or not command:raise ValueError('Mobile host requires an argument list')
                args=list(map(str,command));head=args.pop(0)
                if head=='android-adb':
                    serial=adapter.adb.serial if adapter.adb else None
                    if args[:1]==['-s']:serial=args[1];args=args[2:]
                    result=provider.adb(serial).run(args,timeout=kw.get('timeout'),context=adapter.ctx,
                        sink=kw.get('stdout') if hasattr(kw.get('stdout'),'write') else None)
                    if kw.get('check') and result.returncode:raise subprocess.CalledProcessError(result.returncode,command,result.stdout,result.stderr)
                    return result
                if head==str(sys.executable):
                    if args[:1]==['--run-tool']:args=args[1:]
                    if not args:raise ValueError('Missing embedded Python script')
                    script=Path(args[0]).resolve();root=adapter.root.resolve()
                    if not script.is_relative_to(root) or script.suffix!='.py':raise ValueError('Unsupported embedded script')
                    output=io.StringIO()
                    from backend.helpers import execute_script
                    with provider.execution_lock,contextlib.redirect_stdout(output),contextlib.redirect_stderr(output):
                        try:execute_script(script,args[1:]);code=0
                        except SystemExit as e:code=0 if e.code is None else e.code if isinstance(e.code,int) else 1
                    data=output.getvalue().encode()
                    if kw.get('check') and code:raise subprocess.CalledProcessError(code,command,data)
                    return subprocess.CompletedProcess(command,code,data,b'')
                raise ValueError('Desktop executable cannot run on Android: '+Path(head).name)
            def Popen(self,*args,**kw):raise ValueError('Unported desktop subprocess / 尚未适配的桌面子进程')
        adapter.legacy.subprocess=Process()
        if adapter.kind=='mu300':
            def call(android,*args,timeout=1800,serial=None):
                selected=serial if serial is not None else getattr(android,'serial',None)
                result=provider.adb(selected).run(list(args),timeout=timeout,context=adapter.ctx)
                if result.returncode:raise ConnectionError(result.stdout.decode('utf-8','replace'))
                return result.stdout
            adapter.legacy.Android.call=call

    def run_helper(self,context,root,script,arguments,timeout):
        from backend.helpers import execute_script
        output=io.StringIO();started=time.monotonic()
        previous_trace=sys.gettrace();events=0
        def guard(frame,event,arg):
            nonlocal events
            events+=1
            if events%512==0:
                context.boundary()
                if time.monotonic()-started>timeout:raise TimeoutError('Firmware converter exceeded its time budget')
            return guard
        with self.execution_lock,contextlib.redirect_stdout(output),contextlib.redirect_stderr(output):
            try:
                sys.settrace(guard)
                execute_script(script,list(map(str,arguments)))
            except SystemExit as e:
                if e.code not in (None,0):raise ValueError('Firmware converter failed: '+str(e.code)) from None
            finally:sys.settrace(previous_trace)
        context.log(output.getvalue()[-32768:],scope='helper')
        context.boundary()
        if time.monotonic()-started>timeout:raise TimeoutError('Firmware converter exceeded its time budget')


class EventStream(io.TextIOBase):
    def __init__(self,native):self.native=native
    def write(self,value):
        self.native.emitProtocol(value)
        return len(value)
    def flush(self):pass


class Runtime:
    def __init__(self,native,usb,directory,resources,apk=None):
        self.native=native;self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.provider=Provider(resources,directory,usb,self.emit,apk)
        from backend import host
        host.install(self.provider)
        from backend.service import Service
        from backend.state import Store
        # Temporary files and transaction backups stay in app-private storage,
        # rather than Chaquopy's package directory or shared downloads.
        cache=self.directory/'cache';cache.mkdir(exist_ok=True)
        import tempfile
        tempfile.tempdir=str(cache)
        self.service=Service(self.directory/'packages',Store(self.directory),EventStream(native))
        self.dispatch_lock=threading.RLock()

    def emit(self,kind,data):
        self.native.emitProtocol(json.dumps({'protocol':1,'type':kind,'data':data})+'\n')

    def request(self,command,parameters):
        # Match the existing protocol and let the coordinator own worker state.
        with self.dispatch_lock:
            params=json.loads(str(parameters))
            if command=='usb_permission_retry':
                self.provider.registry.usb.retryPermissions()
                result=True
            elif command in ('mobile_index','mobile_index_plan'):
                if self.busy():raise ValueError('Another installer task is running / 另一个安装任务正在运行')
                result=self.index(params,plan=command.endswith('_plan'))
            else:result=self.service.dispatch({'protocol':1,'command':str(command),'params':params})
            if command=='hello':
                result['active']=self.service.active.snapshot() if self.service.active and self.busy() else None
        return json.dumps(result,ensure_ascii=False)

    def index(self,params,plan=False):
        import runpy,zipfile
        from concurrent.futures import ThreadPoolExecutor,as_completed
        from backend import host
        from backend.release_index import write_index
        tool=runpy.run_path(str(host.resource('release-index.py')))
        source=Path(params['path']).resolve()
        batch=source.is_dir() and not tool['complete_directory'](source)
        inputs=tool['directory_inputs'](source) if batch else [source]
        if not inputs:raise ValueError('No releases or rootfs found / 未找到发行包或 rootfs')
        if plan:return dict(batch=batch,inputs=len(inputs),raw=not source.is_dir() and not zipfile.is_zipfile(source))
        boot=params.get('boot') or None
        if batch and boot:raise ValueError('Optional boot is only for one input / 可选 boot 只能用于单个输入')
        jobs=params.get('jobs',2)
        if type(jobs)!=int or not 1<=jobs<=8:raise ValueError('Choose 1–8 workers / 请选择 1–8 个线程')
        output=Path(params['output']);failures=[];completed=[]
        with tempfile.TemporaryDirectory(prefix='mobile-index-') as temporary:
            folder=Path(temporary);commit=threading.Lock()
            def generate(path):
                last=0
                def progress(count,name):
                    nonlocal last
                    now=time.monotonic()
                    if now-last>=.2:
                        self.emit('ota_progress',dict(stage='index',path=path.name+' · '+str(count)+' · '+name));last=now
                data=tool['summarize'](path,boot=boot,progress=progress)
                with commit:
                    name=tool['filename'](data) if batch else 'index.json'
                    write_index(folder/name,data,reuse=True)
                return name
            with ThreadPoolExecutor(max_workers=min(jobs,len(inputs))) as pool:
                pending={pool.submit(generate,path):path for path in inputs}
                for future in as_completed(pending):
                    try:completed.append(future.result())
                    except Exception as error:
                        if not batch:raise
                        failures.append(dict(input=pending[future].name,error=str(error)))
            if not completed:raise ValueError('No indexes generated / 未生成摘要: '+str(failures))
            if batch:
                with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as archive:
                    for name in sorted(set(completed)):archive.write(folder/name,name)
                    archive.writestr('results.json',json.dumps(dict(succeeded=len(completed),failed=failures),ensure_ascii=False,indent=2))
            else:
                import shutil
                if output.exists():raise ValueError('Output already exists')
                shutil.copyfile(folder/'index.json',output)
        return dict(path=str(output),succeeded=len(completed),failed=failures)

    def busy(self):
        worker=self.service.worker
        return bool(worker and worker.is_alive())

    def import_package(self,path):
        from .files import extract_package
        return str(extract_package(Path(str(path)),self.directory/'packages',self.emit))

    def import_recovery(self,path):
        from .files import extract_recovery
        return str(extract_recovery(Path(str(path)),self.directory/'recovery-imports',self.emit))

    def import_directory(self,path,digest,kind='package'):
        import re,shutil
        from .files import locate
        source=Path(str(path)).resolve();base=(self.directory/'imports').resolve()
        if not source.is_relative_to(base) or source.parent.parent!=base or not re.fullmatch('[a-f0-9]{64}',str(digest)):
            raise ValueError('Directory is outside the mobile importer')
        if kind not in ('package','index'):raise ValueError('Unknown directory import')
        if kind=='package':locate(source,'package')
        wrapper=source.parent
        if list(wrapper.iterdir())!=[source]:raise ValueError('Unexpected directory import workspace')
        destination=self.directory/('packages' if kind=='package' else 'input-directories')/('directory-'+str(digest))
        destination.parent.mkdir(exist_ok=True)
        if destination.exists():
            marker=destination/'.directory-import.json'
            if marker.is_file():
                name=json.loads(marker.read_text())['name']
                if Path(name).name!=name or name in ('.','..'):raise ValueError('Invalid cached directory name')
                cached=destination/name
            else:cached=destination  # Published by the initial Android importer.
            selected=locate(cached,'package') if kind=='package' else cached
            shutil.rmtree(wrapper)
        else:
            (wrapper/'.directory-import.json').write_text(json.dumps(dict(name=source.name,sha256=str(digest))))
            os.replace(wrapper,destination)
            cached=destination/source.name
            selected=locate(cached,'package') if kind=='package' else cached
        return str(selected)

    def retain_input(self,path):
        from backend.state import sha
        source=Path(str(path)).resolve();base=(self.directory/'imports').resolve()
        if not source.is_relative_to(base) or not source.is_file():raise ValueError('Input is outside the mobile importer')
        folder=base/sha(source);folder.mkdir(exist_ok=True)
        previous=[p for p in folder.iterdir() if p.is_file()]
        target=previous[0] if previous else folder/source.name
        if not previous:os.replace(source,target)
        else:
            if sha(target)!=folder.name:raise ValueError('Cached input checksum changed')
            source.unlink()
        source.parent.rmdir()
        return str(target)

    def import_patch(self,path):
        import shutil,zipfile
        from backend.ota_engine import Patch,MAX_BYTES
        path=Path(str(path))
        raw=False
        with zipfile.ZipFile(path) as archive:
            if 'manifest.json' in archive.namelist():
                destination=path.with_suffix('.ota')
                raw=True
            else:
                names=[n for n in archive.namelist() if n=='update.ota' or n.endswith('/update.ota')]
                if len(names)!=1:raise ValueError('Select a patch or ZIP containing one update.ota')
                info=archive.getinfo(names[0])
                if info.file_size>MAX_BYTES or shutil.disk_usage(path.parent).free<info.file_size+(64<<20):
                    raise ValueError('Not enough phone space to import this patch')
                destination=path.parent/'update.ota'
                with archive.open(info) as src,destination.open('xb') as out:shutil.copyfileobj(src,out,1<<20)
        if raw and path!=destination:os.replace(path,destination)
        with Patch(destination):pass
        if path!=destination and path.exists():path.unlink()
        return self.retain_input(destination)
