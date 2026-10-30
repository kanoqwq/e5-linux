"""ADB protocol over TCP or Android USB Host; no adb executable or PTY.

The adb-shell library owns framing/authentication/filesync. This layer supplies
Android USB bytes and the small CLI contract used by the existing installer.
"""
import ipaddress
import json
from pathlib import Path
import secrets
import shlex
import subprocess
import threading
import time


class Registry:
    def __init__(self, usb, directory, notify=lambda *_:None):
        self.usb=usb;self.directory=Path(directory);self.notify=notify
        self.clients={};self.locks={};self.tcp={};self.state={}
        self.guard=threading.RLock()

    def signer(self):
        from adb_shell.auth.keygen import keygen
        from adb_shell.auth.sign_cryptography import CryptographySigner
        path=self.directory/'adbkey';self.directory.mkdir(parents=True,exist_ok=True)
        with self.guard:
            if not path.exists():keygen(str(path));path.chmod(0o600)
            return CryptographySigner(str(path))

    def driver(self,serial=None):return Driver(self,serial)

    def lock(self,serial):
        with self.guard:return self.locks.setdefault(serial,threading.RLock())

    def usb_devices(self):
        return json.loads(str(self.usb.enumerate()))

    def close(self,serial):
        with self.lock(serial):
            client=self.clients.pop(serial,None)
            if client is not None:client.close()
            self.state.pop(serial,None)

    def client(self,serial):
        from adb_shell.adb_device import AdbDevice,AdbDeviceTcp
        from adb_shell.transport.base_transport import BaseTransport
        if not serial:raise ValueError('Select a connected device / 请选择已连接的设备')
        if serial in self.clients:return self.clients[serial]
        if serial in self.tcp:
            hostname,port=self.tcp[serial];client=AdbDeviceTcp(hostname,port,default_transport_timeout_s=20)
        else:
            rows={row['serial']:row for row in self.usb_devices()}
            row=rows.get(serial)
            if row is None:raise ConnectionError('USB device disconnected / USB 设备已断开')
            if not row['permission']:
                self.usb.requestPermission(serial)
                raise PermissionError('Allow USB access on this phone / 请在手机上允许访问 USB 设备')
            host=self.usb
            class UsbTransport(BaseTransport):
                def __init__(self):self.handle=None
                def connect(self,timeout):self.handle=str(host.open(serial))
                def close(self):
                    if self.handle is not None:host.close(self.handle);self.handle=None
                def bulk_read(self,size,timeout):
                    try:return bytes(host.read(self.handle,size,max(1,int((timeout or 30)*1000))))
                    except Exception as error:raise ConnectionError(str(error)) from None
                def bulk_write(self,data,timeout):
                    try:return int(host.write(self.handle,data,max(1,int((timeout or 30)*1000))))
                    except Exception as error:raise ConnectionError(str(error)) from None
            client=AdbDevice(UsbTransport(),default_transport_timeout_s=20)
        try:
            client.connect(rsa_keys=[self.signer()],auth_timeout_s=5,
                auth_callback=lambda *_:self.notify('notice',{'code':'adb_authorization','level':'info','message':'Allow this phone’s ADB key on the target / 请在目标设备允许此手机的 ADB 调试授权'}))
        except BaseException:
            client.close();raise
        self.clients[serial]=client;self.state[serial]='device'
        return client

    def devices(self):
        from adb_shell.exceptions import DeviceAuthError,AdbTimeoutError
        rows=self.usb_devices();rows += [dict(serial=s,permission=True,label=s,wireless=True) for s in self.tcp]
        present={r['serial'] for r in rows}
        for serial in set(self.clients)-present:self.close(serial)
        result=[]
        for row in rows:
            serial=row['serial']
            if not row['permission']:
                self.usb.requestPermission(serial);status='unauthorized'
            else:
                try:
                    with self.lock(serial):self.client(serial)
                    status='device'
                except (DeviceAuthError,AdbTimeoutError,PermissionError):status='unauthorized'
                except Exception:status='offline'
            result.append(dict(serial=serial,state=status,label=row.get('label',serial),wireless=serial in self.tcp))
        return result


class Driver:
    def __init__(self,registry,serial):self.registry=registry;self.serial=serial

    def pull(self,remote,local,progress=None,timeout=45):
        with self.registry.lock(self.serial):
            client=self.registry.client(self.serial)
            try:client.pull(remote,str(local),progress_callback=(lambda _name,done,total:progress(done,total)) if progress else None,
                            transport_timeout_s=timeout,read_timeout_s=timeout)
            except BaseException:
                self.registry.close(self.serial);raise

    def shell(self,command,timeout=20,binary=False,sink=None):
        started=time.monotonic()
        if timeout is not None and timeout<=0:raise subprocess.TimeoutExpired(['android-adb','shell'],timeout)
        token='__OPENWRT_EXIT_'+secrets.token_hex(16)+'__'
        script=command if binary else '( '+command+'\n); rc=$?; printf "\\n'+token+'%s\\n" "$rc"'
        output=bytearray()
        client=self.registry.client(self.serial)
        for chunk in client.streaming_shell(script,decode=False,transport_timeout_s=timeout or 30,
                                            read_timeout_s=timeout or 1800):
            if timeout is not None and time.monotonic()-started>timeout:
                raise subprocess.TimeoutExpired(['android-adb','shell'],timeout)
            if sink is not None:sink.write(chunk)
            else:
                output.extend(chunk)
                if len(output)>32<<20:raise ValueError('ADB response exceeds limit / ADB 响应过大')
        if binary:return 0,bytes(output)
        marker=b'\n'+token.encode();at=output.rfind(marker)
        if at<0:raise ConnectionError('ADB command lost its final status / ADB 命令未返回完整状态')
        try:code=int(output[at+len(marker):].strip())
        except ValueError:raise ConnectionError('Invalid ADB exit status / ADB 命令状态无效') from None
        return code,bytes(output[:at])

    def run(self,args,timeout=20,context=None,sink=None):
        if not args:raise ValueError('Missing ADB operation')
        op,*tail=args;r=self.registry;output=b'';code=0
        if op=='devices':
            output=('List of devices attached\n'+''.join(row['serial']+'\t'+row['state']+' model:'+row['label'].replace(' ','_')+'\n' for row in r.devices())).encode()
        elif op=='connect':
            from backend.transport import adb_endpoint
            endpoint=adb_endpoint(tail[0]);hostname,port=endpoint.rsplit(':',1);hostname=hostname.strip('[]')
            r.tcp[endpoint]=(hostname,int(port))
            with r.lock(endpoint):r.client(endpoint)
            output=('connected to '+endpoint+'\n').encode()
        elif op=='disconnect':
            r.close(tail[0]);r.tcp.pop(tail[0],None)
        else:
            with r.lock(self.serial):
                try:
                    if op in ('shell','exec-out'):
                        command=tail[0] if len(tail)==1 else shlex.join(tail)
                        code,output=self.shell(command,timeout,binary=op=='exec-out',sink=sink)
                    elif op=='push':
                        if len(tail)!=2 or not Path(tail[0]).is_file():raise ValueError('Push requires one local file')
                        r.client(self.serial).push(tail[0],tail[1],transport_timeout_s=timeout or 30,read_timeout_s=timeout or 1800)
                    elif op=='pull':
                        if len(tail)!=2:raise ValueError('Pull requires source and destination')
                        self.pull(tail[0],Path(tail[1]),timeout=timeout or 45)
                    elif op=='reboot':
                        client=r.client(self.serial)
                        client.reboot(transport_timeout_s=timeout or 20,read_timeout_s=timeout or 20)
                        r.close(self.serial)
                    elif op=='get-state':r.client(self.serial);output=b'device\n'
                    else:raise ValueError('Unsupported mobile ADB operation: '+op)
                except (ValueError,KeyboardInterrupt,SystemExit,subprocess.TimeoutExpired):
                    r.close(self.serial);raise
                except Exception as error:
                    r.close(self.serial);raise ConnectionError(str(error)) from None
        return subprocess.CompletedProcess(['android-adb',*args],code,output,b'')
