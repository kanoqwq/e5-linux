"""Export one OTA payload plus verified desktop tools and the Android host APK."""
import copy
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import zipfile


def export_bundle(patch,output,release,apk):
    output=Path(output);release=Path(release)
    if output.exists():raise ValueError('Output already exists')
    if not apk or not Path(apk).is_file():raise ValueError('Installed Android APK is unavailable')
    temporary=output.with_name(output.name+'.building')
    if temporary.exists():raise ValueError('Export staging already exists')
    try:
        with zipfile.ZipFile(release) as source,zipfile.ZipFile(temporary,'x',compression=zipfile.ZIP_DEFLATED) as target:
            names=[n for n in source.namelist() if n.endswith('diff-tool.json')]
            if len(names)!=1:raise ValueError('New release lacks a unique desktop tool manifest')
            prefix=names[0][:-len('diff-tool.json')]
            meta=json.loads(source.read(names[0]))
            if meta.get('protocol')!=1 or not meta.get('files'):raise ValueError('Invalid desktop tool manifest')
            target.write(patch,'update.ota')
            target.write(apk,'OpenWrt-Tools.apk')
            for name,digest in meta['files'].items():
                safe=PurePosixPath(name)
                if safe.is_absolute() or '..' in safe.parts or '\\' in name:raise ValueError('Unsafe desktop tool path')
                if name=='OpenWrt-Diff-Studio.exe':destination='OpenWrt-Updater.exe'
                elif name.startswith('OpenWrt-Diff-Studio.app/'):destination=name.replace('OpenWrt-Diff-Studio.app/','OpenWrt-Updater.app/',1)
                elif name=='OpenWrt-Diff-Studio.py':destination='OpenWrt-Updater.py'
                elif name.startswith(('tools/','backend/')):destination=name
                else:continue
                info=copy.copy(source.getinfo(prefix+name));info.filename=destination
                checksum=hashlib.sha256()
                with source.open(prefix+name) as src,target.open(info,'w',force_zip64=True) as dst:
                    for block in iter(lambda:src.read(1<<20),b''):checksum.update(block);dst.write(block)
                if checksum.hexdigest()!=digest:raise ValueError('Desktop runtime checksum differs: '+name)
            target.writestr('README.md','# OpenWrt update\n\n'
                'Android: install OpenWrt-Tools.apk, select Patch Updater and import update.ota.\n'
                'Windows/macOS: extract all files and open OpenWrt-Updater.exe / .app.\n'
                'LuCI: import update.ota in System patches.\n\n'
                '安卓：安装 OpenWrt-Tools.apk，进入补丁更新并导入 update.ota。\n'
                '电脑：完整解压，打开 OpenWrt-Updater.exe / .app。\n'
                '网页：系统补丁中导入 update.ota。\n')
        os.replace(temporary,output)
    finally:
        if temporary.exists():temporary.unlink()
