#!/usr/bin/env python3
"""Refresh installer-only members of a previously verified local release.

Stream the existing ZIP without extracting boot or rootfs. The firmware keeps
its exact identity; a separate installer revision records the new host inputs.
Called only by tools/build-release.py under its workspace lock.
"""
import copy
import hashlib
import io
import json
import runpy
from pathlib import Path
import zipfile

TOP=Path(__file__).resolve().parents[1]


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1<<20),b''):h.update(block)
    return h.hexdigest()


def refresh(source,target,tools,revision):
    if target.exists():raise ValueError('Installer refresh target already exists')
    replacement={'files/part-write.sh':(TOP/'openwrt/bundle/part-write.sh').read_bytes().replace(b'\r\n',b'\n'),
                 'scripts/installer/part-two-stage.py':(TOP/'openwrt/bundle/part-two-stage.py').read_bytes().replace(b'\r\n',b'\n')}
    for name, path in {
        'OpenWrt-Installer.py':'openwrt/bundle/flash.py',
        'scripts/installer/storage-api.py':'installer/backend/storage.py',
        'scripts/installer/storage-lib.sh':'boot/storage.sh',
        'scripts/installer/gpt-api.py':'installer/backend/gpt.py',
        'files/storage.sh':'boot/storage.sh',
        'files/device-flash-boot.sh':'openwrt/device-flash-boot.sh',
        'files/nv-persistence.py':'openwrt/luci-app-t551m/root/usr/lib/e5/nv_persistence.py',
        'scripts/tools/e5-update.py':'tools/e5-update.py',
    }.items():
        replacement[name]=(TOP/path).read_bytes().replace(b'\r\n',b'\n')
    with zipfile.ZipFile(source) as original:
        replacement['files/install-lock-tools.tar']=runpy.run_path(str(TOP/'openwrt/prepare-ext4-tools.py'))['lock_addon'](original.read('files/ext4-tools.tar'),tools)
        sums={name:checksum for line in original.read('files/SHA256SUMS').decode().splitlines()
              for checksum,name in [line.split(None,1)]}
        payload={n:sums[n] for n in ('files/boot.img','files/openwrt.ext4.gz','files/ext4-tools.tar')}
        record={'format':1,'kind':'installer-only','source_commit':revision['source_commit'],
                'source_fingerprint':revision['source_fingerprint'],'base_archive':source.name,
                'base_sha256':digest(source),'firmware_unchanged':payload,
                'changed':{n:hashlib.sha256(v).hexdigest() for n,v in replacement.items()}}
        replacement['files/INSTALLER-REVISION.json']=(json.dumps(record,indent=2)+'\n').encode()
        replacement['START-HERE.md']=original.read('START-HERE.md')+(
            '\n## Installer revision / 安装器修订\n\n'
            '随包安装脚本已刷新，包含分区续装、安装锁及 NV 保留／备份流程。'
            '系统镜像和 boot 与基础发布包完全一致；详见 files/INSTALLER-REVISION.json。\n').encode()
        sums.update({n:hashlib.sha256(v).hexdigest() for n,v in replacement.items()})
        replacement['files/SHA256SUMS']=''.join(h+'  '+n+'\n' for n,h in sorted(sums.items())).encode()
        with zipfile.ZipFile(target,'x') as output:
            for member in original.infolist():
                info=copy.copy(member)
                with output.open(info,'w',force_zip64=True) as destination:
                    if member.filename in replacement:destination.write(replacement[member.filename])
                    else:
                        with original.open(member) as stream:
                            for block in iter(lambda:stream.read(1<<20),b''):destination.write(block)
            for name,data in replacement.items():
                if name in original.namelist():continue
                info=zipfile.ZipInfo(name);info.create_system=3;info.external_attr=0o100644<<16
                info.compress_type=zipfile.ZIP_DEFLATED
                output.writestr(info,data)
    return record
