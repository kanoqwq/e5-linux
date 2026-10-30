"""Five emulator-only rounds using the real Android WebView, JNI and Python.

Does not contact a physical device or invoke installation, GPT, NV or reboot.
Fixtures are synthetic releases copied only into the debug emulator app.
"""
import asyncio
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import socket
import struct
import sys
import tempfile
import threading
import urllib.request
import zipfile

import websockets

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE/'tests'))
from test_ota import mu_release

ADB='D:/Android/Sdk/platform-tools/adb.exe'
SERIAL='emulator-5580'
CONTROL_PHONE=False
SSH_FIXTURE_HOST=None
APP='cn.kanokano.openwrt.tools'
REMOTE='files/openwrt/native-smoke'


def adb(*args,**kw):
    assert SERIAL.startswith('emulator-') or CONTROL_PHONE
    return subprocess.run([ADB,'-s',SERIAL,*args],check=True,capture_output=True,**kw).stdout


class Session:
    def __init__(self,ws):self.ws=ws;self.id=0;self.errors=[]
    async def call(self,method,params=None):
        self.id+=1;identity=self.id
        await self.ws.send(json.dumps(dict(id=identity,method=method,params=params or {})))
        while True:
            message=json.loads(await asyncio.wait_for(self.ws.recv(),40))
            if message.get('id')==identity:
                if 'error' in message:raise RuntimeError(message['error'])
                return message.get('result',{})
            if message.get('method')=='Runtime.exceptionThrown':self.errors.append(message)
    async def evaluate(self,expression):
        result=await self.call('Runtime.evaluate',dict(expression=expression,awaitPromise=True,returnByValue=True))
        if result.get('exceptionDetails'):raise RuntimeError(result['exceptionDetails'])
        return result.get('result',{}).get('value')
    async def request(self,command,params=None):
        data=dict(command=command,parameters=json.dumps(params or {}))
        return await self.evaluate('window.__TAURI_INTERNALS__.invoke("plugin:mobile-host|request",'+json.dumps(data)+').then(r=>r.result)')


async def network_transports(session):
    """Actual Android cryptography/Paramiko/ADB libraries against local fixtures."""
    server=socket.socket();server.bind(('127.0.0.1',0));server.listen(1);server.settimeout(15)
    errors=[]
    def adb_fixture():
        try:
            with server.accept()[0] as conn:
                conn.settimeout(15)
                def exact(size):
                    result=b''
                    while len(result)<size:
                        chunk=conn.recv(size-len(result))
                        if not chunk:raise EOFError('ADB fixture disconnected')
                        result+=chunk
                    return result
                cmd,a,b,size,checksum,magic=struct.unpack('<6I',exact(24));payload=exact(size)
                assert cmd.to_bytes(4,'little')==b'CNXN' and checksum==sum(payload)&0xffffffff
                value=int.from_bytes(b'CNXN','little');payload=b'device::ro.product.name=emulator-fixture;\0'
                conn.sendall(struct.pack('<6I',value,0x01000000,4096,len(payload),sum(payload),value^0xffffffff)+payload)
                assert conn.recv(1)==b''
        except BaseException as error:errors.append(str(error))
        finally:server.close()
    thread=threading.Thread(target=adb_fixture,daemon=True);thread.start()
    port=server.getsockname()[1]
    if CONTROL_PHONE:adb('reverse','tcp:'+str(port),'tcp:'+str(port))
    endpoint=('127.0.0.1' if CONTROL_PHONE else '10.0.2.2')+':'+str(port)
    connected=await session.request('adb_connect',dict(address=endpoint))
    assert any(d['serial']==endpoint and d['state']=='device' for d in connected['devices']),connected
    await session.request('adb_disconnect',dict(serial=endpoint));thread.join(5);assert not thread.is_alive() and not errors,errors
    if CONTROL_PHONE:adb('reverse','--remove','tcp:'+str(port))
    import paramiko
    authenticated=[];listener=socket.socket();listener.bind((SSH_FIXTURE_HOST or '127.0.0.1',22));listener.listen(1);listener.settimeout(15)
    hostkey=paramiko.RSAKey.generate(2048)
    class Auth(paramiko.ServerInterface):
        def check_auth_password(self,username,password):
            authenticated.append(username=='root' and password=='fixture-password')
            return paramiko.AUTH_SUCCESSFUL if authenticated[-1] else paramiko.AUTH_FAILED
        def get_allowed_auths(self,username):return 'password'
    def ssh_fixture():
        try:
            with listener.accept()[0] as conn:
                transport=paramiko.Transport(conn);transport.add_server_key(hostkey);transport.start_server(server=Auth())
                for _ in range(150):
                    if not transport.is_active():break
                    threading.Event().wait(.1)
                transport.close()
        except BaseException as error:errors.append(str(error))
        finally:listener.close()
    thread=threading.Thread(target=ssh_fixture,daemon=True);thread.start()
    if CONTROL_PHONE and not SSH_FIXTURE_HOST:adb('reverse','tcp:22','tcp:22')
    try:await session.request('ota_probe',dict(host=SSH_FIXTURE_HOST or ('127.0.0.1' if CONTROL_PHONE else '10.0.2.2'),password='fixture-password',path='/nonexistent-native-fixture.ota'))
    except RuntimeError as error:assert 'FileNotFoundError' in str(error),error
    else:raise AssertionError('Missing local patch unexpectedly opened')
    finally:
        if CONTROL_PHONE and not SSH_FIXTURE_HOST:adb('reverse','--remove','tcp:22')
    thread.join(5);assert authenticated==[True] and not errors,(authenticated,errors)


async def run():
    pid=adb('shell','pidof',APP).decode().strip()
    adb('forward','tcp:19222','localabstract:webview_devtools_remote_'+pid)
    meta=json.load(urllib.request.urlopen('http://127.0.0.1:19222/json'))[0]
    report=[]
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)
        for name,version in (('old','mu300-r20261030.010000-g11111111-s11111111'),('new','mu300-r20261030.020000-g22222222-s22222222')):
            folder=root/name;mu_release(folder,version,{'usr/lib/test':name.encode()})
            runtime={'OpenWrt-Diff-Studio.exe':b'fixture only - never executed','tools/backend/runtime':b'fixture runtime'}
            (folder/'diff-tool.json').write_text(json.dumps({'protocol':1,'files':{k:hashlib.sha256(v).hexdigest() for k,v in runtime.items()}}))
            for key,value in runtime.items():
                path=folder/key;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(value)
            archive=root/(name+'.zip')
            with zipfile.ZipFile(archive,'w') as z:
                for path in folder.rglob('*'):
                    if path.is_file():z.write(path,'release/'+path.relative_to(folder).as_posix())
        adb('shell','run-as',APP,'mkdir','-p',REMOTE)
        for name in ('old','new'):
            payload=(root/(name+'.zip')).read_bytes()
            adb('exec-in','run-as',APP,'sh','-c',f'cat > {REMOTE}/{name}.zip',input=payload)
            copied=adb('exec-out','run-as',APP,'cat',REMOTE+'/'+name+'.zip')
            assert copied==payload,'Emulator fixture transport must preserve binary bytes'
        base='/data/user/0/'+APP+'/'+REMOTE
        async with websockets.connect(meta['webSocketDebuggerUrl'],max_size=16<<20) as ws:
            s=Session(ws);await s.call('Runtime.enable');await s.call('Page.enable')
            hello=await s.request('hello');assert hello['protocol']==1
            assert await s.request('devices')==[]
            assert await s.request('can_close') is True
            await network_transports(s)
            report.append(dict(round=1,test='Real JNI/Python, USB enumeration, TCP ADB and authenticated Paramiko SSH',passed=True))
            result=await s.request('ota_analyze',dict(old=base+'/old.zip',new=base+'/new.zip'))
            assert result['can_export'] and result['files']>0,result
            report.append(dict(round=2,test='Real Android tar/ZIP release analysis and OTA support resources',passed=True))
            index=base+'/old.owrt-index.json'
            await s.request('mobile_index',dict(path=base+'/old.zip',output=index))
            indexed=await s.request('ota_analyze',dict(old=index,new=base+'/new.zip'))
            assert indexed['manifest']==result['manifest'],(indexed,result)
            report.append(dict(round=3,test='Portable JSON baseline generation and identical Diff result',passed=True))
            output=base+'/patch.zip'
            exported=await s.request('ota_export',dict(old=index,new=base+'/new.zip',output=output))
            payload=adb('exec-out','run-as',APP,'cat',REMOTE+'/patch.zip')
            local=root/'patch.zip';local.write_bytes(payload)
            with zipfile.ZipFile(local) as z:
                assert z.namelist().count('update.ota')==1
                assert z.namelist().count('OpenWrt-Tools.apk')==1
                assert 'OpenWrt-Updater.exe' in z.namelist()
                assert len(z.namelist())==len(set(z.namelist()))
            report.append(dict(round=4,test='Diff export: one OTA, installed APK and deduplicated desktop runtime',passed=True))
            for page in ('diff.html','ota.html','release-index.html','index.html'):
                await s.call('Page.navigate',dict(url='http://tauri.localhost/'+page))
                for _ in range(40):
                    if await s.evaluate('document.querySelectorAll(".mobile-tools button").length')==4:break
                    await asyncio.sleep(.1)
                else:raise AssertionError('Mobile tool navigation did not render: '+page)
                assert await s.request('can_close') is True
            image=await s.call('Page.captureScreenshot',dict(format='png'))
            (HERE/('verification/android-native-phone.png' if CONTROL_PHONE else 'verification/android-native-index.png')).write_bytes(base64.b64decode(image['data']))
            assert not s.errors,s.errors
            report.append(dict(round=5,test='All native tool pages, backend persistence and JS exception check',passed=True))
    # Only the known disposable fixture directory; no transactions/backups.
    adb('shell','run-as',APP,'rm','-rf',REMOTE)
    (HERE/('verification/android-native-phone-regression.json' if CONTROL_PHONE else 'verification/android-native-regression.json')).write_text(json.dumps(report,indent=2)+'\n')
    print('Five native Android rounds passed. Only the authorized control app and local fixtures were used.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial',default=SERIAL)
    parser.add_argument('--ssh-fixture-host',help='Local PC LAN address for SSH fixture when Android cannot reverse privileged port 22')
    parser.add_argument('--control-phone-model',help='Explicitly authorized control phone model; never a target WiFi device')
    args=parser.parse_args();SERIAL=args.serial;SSH_FIXTURE_HOST=args.ssh_fixture_host
    if not SERIAL.startswith('emulator-'):
        if not args.control_phone_model:parser.error('A physical control phone requires its explicitly authorized model')
        CONTROL_PHONE=True
        model=adb('shell','getprop','ro.product.model').decode().strip()
        if model!=args.control_phone_model:parser.error('Connected model differs; refusing app test')
    asyncio.run(run())
