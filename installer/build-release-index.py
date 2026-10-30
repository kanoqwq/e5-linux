#!/usr/bin/env python3
"""Build the single-file Windows release summary CLI; invoke via build-release.py run."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if sys.platform != 'win32':
        raise SystemExit('Build this EXE with native Windows Python, not WSL.')
    root = Path(__file__).resolve().parent
    default = root/'out/releases/tools/release-index'
    output = (args.output or default).resolve()
    work = root/'work/release-index-build'
    source = root/'release-index.py'
    modules = root
    engine = root/'runtime/luci-app-unisoc-ota/root/usr/lib/unisoc-ota/engine.py'
    inputs = [source, Path(__file__), engine, modules/'backend/ota_engine.py',
              modules/'backend/release_index.py', modules/'backend/ota_package.py']
    fingerprint = lambda: {str(p.relative_to(root)).replace('\\', '/'):
                          hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest() for p in inputs}
    before = fingerprint()
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--noupx',
                    '--onefile', '--console', '--name', 'OpenWrt-Release-Index',
                    '--distpath', str(output), '--workpath', str(work/'objects'), '--specpath', str(work),
                    '--paths', str(modules), '--collect-all', 'dissect.extfs', '--hidden-import', 'subprocess',
                    '--add-data', str(engine)+';.', '--icon', str(modules/'ui/src-tauri/icons/icon.ico'),
                    str(source)], cwd=root, check=True)
    if fingerprint() != before:
        raise ValueError('Summary tool inputs changed during compilation')
    exe = output/'OpenWrt-Release-Index.exe'
    subprocess.run([str(exe), '--help'], check=True, stdout=subprocess.DEVNULL)
    checksum = hashlib.sha256(exe.read_bytes()).hexdigest()
    (output/(exe.name+'.sha256')).write_text(checksum+'  '+exe.name+'\n', encoding='utf-8', newline='\n')
    (output/'build.json').write_text(json.dumps(dict(platform='windows-x64', source=before,
                                                  file=exe.name, sha256=checksum, bytes=exe.stat().st_size), indent=2)+'\n',
                                   encoding='utf-8', newline='\n')
    print('Built: '+str(exe), flush=True)


if __name__ == '__main__':
    main()
