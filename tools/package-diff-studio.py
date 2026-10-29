#!/usr/bin/env python3
"""Attach Diff Studio entry points while sharing the installer runtimes.

Native binaries are already fingerprinted by installer/package_gui.py. Renaming
the macOS bundle directory selects diff.html without changing signed contents.
Only Diff Studio's files enter diff-tool.json; no firmware/ADB/installer payload
is included when the tool subsequently exports a patch updater.
"""
import hashlib
import json
from pathlib import Path
import shutil
import sys

TOP=Path(__file__).resolve().parents[1]
SUPPORT=('__init__.py','ota_engine.py','ota_package.py','release_index.py','ota_desktop.py',
         'ota_bundle.py','ota_runtime.py','boot.py','rpc.py','proxy.sh','lz4raw-aarch64.so',
         'errors.js','unisoc-ota.js','unisoc-ota.json')


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1<<20),b''):h.update(block)
    return h.hexdigest()


def attach(package):
    package=Path(package)
    sys.path.insert(0,str(TOP/'installer'))
    from package_gui import sources
    current=sources();native={};owned=set()
    for platform,label in (('windows-x64','GUI-BUILD.json'),('macos-arm64','GUI-BUILD-macos.json')):
        build=json.loads((package/'docs'/label).read_text())
        if build.get('protocol')!=1 or build.get('platform')!=platform or build.get('source')!=current:
            raise ValueError('Diff Studio requires current native installer: '+platform)
        for name,wanted in build['files'].items():
            path=(package/name).resolve()
            if not path.is_relative_to(package.resolve()) or not path.is_file() or digest(path)!=wanted:
                raise ValueError('Diff Studio native input mismatch: '+name)
        native[platform]=build
    destinations=('OpenWrt-Diff-Studio.exe','OpenWrt-Diff-Studio.app','OpenWrt-Diff-Studio.py','diff-tool.json','backend')
    if any((package/n).exists() or (package/n).is_symlink() for n in destinations):
        raise ValueError('Diff Studio staging already exists; do not overwrite it')
    shutil.copy2(package/'OpenWrt-Installer.exe',package/'OpenWrt-Diff-Studio.exe')
    shutil.copytree(package/'OpenWrt-Installer.app',package/'OpenWrt-Diff-Studio.app')
    owned.add('OpenWrt-Diff-Studio.exe')
    owned.update(p.relative_to(package).as_posix() for p in (package/'OpenWrt-Diff-Studio.app').rglob('*') if p.is_file())
    shutil.copy2(TOP/'installer/ota-tool.py',package/'OpenWrt-Diff-Studio.py')
    owned.add('OpenWrt-Diff-Studio.py');(package/'backend').mkdir()
    plugin=TOP/'openwrt/luci-app-unisoc-ota'
    extras={'errors.js':plugin/'htdocs/luci-static/resources/unisoc-ota/errors.js',
            'unisoc-ota.js':plugin/'htdocs/luci-static/resources/view/system/unisoc-ota.js',
            'unisoc-ota.json':plugin/'root/usr/share/rpcd/acl.d/unisoc-ota.json',
            'lz4raw-aarch64.so':TOP/'installer/ota-native/lz4raw-aarch64.so',
            'ota_engine.py':plugin/'root/usr/lib/unisoc-ota/engine.py'}
    for name in SUPPORT:
        source=extras.get(name) or (plugin/'root/usr/lib/unisoc-ota'/name if name in ('boot.py','rpc.py','proxy.sh') else TOP/'installer/backend'/name)
        shutil.copy2(source,package/'backend'/name);owned.add('backend/'+name)
    # Both platforms use the already installed tools/ backend. Do not copy it
    # into a second tool directory, nor include unrelated ADB binaries.
    for platform,prefix in (('windows-x64','tools/backend/'),('macos-arm64','tools/macos/backend/')):
        owned.update(n for n in native[platform]['files'] if n.startswith(prefix))
    manifest={'protocol':1,'platform':'windows-x64+macos-arm64','source':current,
              'files':{n:digest(package/n) for n in sorted(owned)}}
    (package/'diff-tool.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8',newline='\n')
    guide=(TOP/'docs/DIFF-STUDIO.zh-CN.md').read_text(encoding='utf-8')
    (package/'docs/DIFF-STUDIO.md').write_text(guide,encoding='utf-8',newline='\n')
    start=package/'START-HERE.md'
    with start.open('a',encoding='utf-8',newline='\n') as stream:
        stream.write('\n## Diff Studio / 差异更新工具\n\n'
                     'Windows: **OpenWrt-Diff-Studio.exe**. Apple Silicon: **OpenWrt-Diff-Studio.app**.\n\n'
                     '完整解压后打开对应程序；与安装器共用 tools 目录，请勿单独移动 EXE 或 APP。'
                     '选择旧、新完整包生成更新补丁；操作说明见 [DIFF-STUDIO.md](docs/DIFF-STUDIO.md)。\n')
    return manifest


def main(argv=None):
    import argparse
    import runpy
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--prepare',action='store_true')
    args=parser.parse_args(argv)
    args.package.mkdir(parents=True,exist_ok=True)
    if args.prepare:
        runpy.run_path(str(TOP/'installer/package_gui.py'))['attach'](args.package)
    attach(args.package)


if __name__=='__main__':main()
