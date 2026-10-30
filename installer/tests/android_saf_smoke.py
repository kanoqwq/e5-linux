"""Real Android document picker round-trip on the authorized control phone only.

Uses only the tiny named verification directory; never selects a target device.
The fixture directory is prepared under Download/OpenWrt-Android-Verification.
"""
import asyncio
import json
from pathlib import Path
import re
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET
import websockets
from android_native_smoke import Session

ADB='D:/Android/Sdk/platform-tools/adb.exe'
SERIAL='7a146791'
APP='cn.kanokano.openwrt.tools'
HERE=Path(__file__).resolve().parents[1]
FIXTURE='OpenWrt-Android-Verification'

def adb(*args):
    return subprocess.check_output([ADB,'-s',SERIAL,*args],timeout=30)

def tree():
    adb('shell','uiautomator','dump','/data/local/tmp/openwrt-picker-ui.xml')
    return ET.fromstring(adb('exec-out','cat','/data/local/tmp/openwrt-picker-ui.xml'))

def tap(node):
    x,y,right,bottom=map(int,re.findall(r'\d+',node.get('bounds')))
    assert right>x and bottom>y
    adb('shell','input','tap',str((x+right)//2),str((y+bottom)//2))

def find(text=None,description=None):
    return next((n for n in tree().iter('node') if (text is None or n.get('text')==text) and (description is None or n.get('content-desc')==description)),None)

def select(text):
    node=find(text=text);assert node is not None,'Verification picker item missing: '+text
    tap(node)

def downloads():
    node=next((node for node in [find(description='显示根目录'),find(description='Show roots')] if node is not None),None)
    assert node is not None,'Document drawer unavailable'
    tap(node)
    node=next((node for node in [find(text='下载'),find(text='Downloads')] if node is not None),None);assert node is not None,'Downloads root unavailable'
    tap(node);select(FIXTURE)

def cancel_picker():
    for _ in range(10):
        focus='\n'.join(x for x in adb('shell','dumpsys','window').decode().splitlines() if 'mCurrentFocus=' in x)
        if APP in focus:return
        if 'com.android.documentsui' in focus:adb('shell','input','keyevent','KEYCODE_BACK')
        time.sleep(.4)
    raise TimeoutError('Android picker did not return to control App')

async def launch(session,command,parameters=None):
    invocation=json.dumps(dict(command=command,parameters=json.dumps(parameters or {})))
    await session.evaluate('window.__safResult=null;window.__TAURI_INTERNALS__.invoke("plugin:mobile-host|native_command",'+invocation+').then(r=>window.__safResult={value:r.value}).catch(e=>window.__safResult={error:e});true')
    await asyncio.sleep(.5)
    tree()

async def result(session):
    for _ in range(60):
        value=await session.evaluate('window.__safResult')
        if value is not None:
            assert 'error' not in value,value
            return value['value']
        await asyncio.sleep(.2)
    raise TimeoutError('Document selection did not resolve')

async def main():
    assert adb('shell','getprop','ro.product.model').decode().strip()=='PHB110'
    pid=adb('shell','pidof',APP).decode().strip()
    adb('forward','tcp:19222','localabstract:webview_devtools_remote_'+pid)
    metadata=json.load(urllib.request.urlopen('http://127.0.0.1:19222/json'))[0]
    report=[]
    async with websockets.connect(metadata['webSocketDebuggerUrl'],max_size=16<<20,ping_interval=None) as ws:
        s=Session(ws)
        await launch(s,'pick_package');cancel_picker()
        assert await result(s) is None
        print('Completed check',len(report)+1,flush=True)
        report.append(dict(test='Cancel file selection releases picker state',passed=True))
        await launch(s,'pick_package');downloads();select('Android-Verification-E5.zip')
        package=await result(s);checked=await s.request('package',dict(path=package))
        assert checked['adapter']=='e5' and checked['profile']=='e5'
        print('Completed check',len(report)+1,flush=True)
        report.append(dict(test='Full ZIP selected with Android SAF, imported and checksum verified',passed=True))
        await launch(s,'pick_package_directory');downloads();select('Android-Verification-E5')
        node=next((node for node in [find(text='使用此文件夹'),find(text='Use this folder')] if node is not None),None);assert node is not None
        tap(node)
        node=next((node for node in [find(text='允许'),find(text='Allow')] if node is not None),None)
        if node is not None:tap(node)
        directory=await result(s);checked=await s.request('package',dict(path=directory))
        assert checked['profile']=='e5' and Path(directory).name=='Android-Verification-E5'
        print('Completed check',len(report)+1,flush=True)
        report.append(dict(test='Directory permission, stream import and original directory name',passed=True))
        await launch(s,'save_index',dict(suggested_name='Android-Verification-index.json'));downloads()
        node=next((node for node in [find(text='保存'),find(text='Save')] if node is not None),None);assert node is not None
        tap(node);output=await result(s)
        source='/data/user/0/'+APP+'/files/openwrt/native-smoke/old.zip'
        await s.request('mobile_index',dict(path=source,output=output,jobs=2))
        content=adb('exec-out','cat','/sdcard/Download/'+FIXTURE+'/Android-Verification-index.json')
        index=json.loads(content);assert index['release']['family']=='mu300'
        print('Completed check',len(report)+1,flush=True)
        report.append(dict(test='Actual JSON index generation and SAF document export',passed=True))
        await launch(s,'pick_ota_input',dict(kind='old'));cancel_picker();assert await result(s) is None
        print('Completed check',len(report)+1,flush=True)
        report.append(dict(test='Diff input picker opens and cancels after completed export',passed=True))
    (HERE/'verification/android-saf-regression.json').write_text(json.dumps(report,indent=2)+'\n')
    print('Five actual Android SAF checks passed; only local control-phone fixtures were used')

if __name__=='__main__':asyncio.run(main())
