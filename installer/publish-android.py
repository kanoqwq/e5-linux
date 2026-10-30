#!/usr/bin/env python3
"""Publish a verified native Android host; no firmware build or device access.

Run through tools/build-release.py run with the existing shared workspace.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import zipfile

HERE=Path(__file__).resolve().parent


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--sdk',type=Path,required=True)
    parser.add_argument('--jdk',type=Path,required=True)
    parser.add_argument('--release-root',type=Path,help='Explicit parent release root when publishing through a firmware build')
    parser.add_argument('--clean-staging',action='store_true')
    args=parser.parse_args()
    allowed=(args.release_root or HERE/'out/releases').resolve();output=args.output.resolve()
    if not output.is_relative_to(allowed) or output==allowed:raise ValueError('Android releases must stay under out/releases')
    stage=args.workspace/'package-work/e5-release/android-host/assembly';owner=stage/'.android-host-staging.json'
    if args.clean_staging:
        if stage.exists():
            if not owner.is_file() or json.loads(owner.read_text())!={'component':'android-control-app'} or stage.is_symlink():raise ValueError('Unrecognized Android staging owner')
            shutil.rmtree(stage)
        print('Owned Android staging cleaned');return
    if stage.exists():raise ValueError('Android staging retained; inspect it, then explicitly use --clean-staging')
    if output.exists():raise ValueError('Output already exists; previous releases are never overwritten')
    artifact=HERE/'artifacts/android';metadata=artifact/'OpenWrt-Tools-build.json'
    build=json.loads(metadata.read_text())
    source=runpy.run_path(str(HERE/'build-android.py'))['sources']()
    if build.get('source')!=source or build.get('debug') is not False or build.get('platform')!='android-aarch64':raise ValueError('Rebuild the current ARM64 release host before publishing')
    if set(build.get('files',{}))!={'OpenWrt-Tools.apk'}:raise ValueError('Android release artifact manifest is incomplete')
    apk=artifact/'OpenWrt-Tools.apk'
    if sha(apk)!=build['files'][apk.name]:raise ValueError('APK checksum differs')
    from mobile.apk import validate
    if validate(apk,'aarch64')!=build['validation']:raise ValueError('APK validation differs')
    with zipfile.ZipFile(apk) as archive:
        if archive.testzip() is not None:raise ValueError('APK ZIP integrity failed')
    versions=sorted((args.sdk/'build-tools').iterdir(),key=lambda p:tuple(int(v) for v in p.name.split('.') if v.isdigit()))
    signer=next((p/'apksigner.bat' for p in reversed(versions) if (p/'apksigner.bat').is_file()),None)
    if signer is None:raise ValueError('Selected SDK lacks apksigner')
    env=dict(os.environ,JAVA_HOME=str(args.jdk))
    subprocess.run([str(signer),'verify','--verbose',str(apk)],env=env,check=True)
    stage.mkdir(parents=True);owner.write_text(json.dumps({'component':'android-control-app'}))
    shutil.copy2(apk,stage/apk.name)
    shutil.copy2(metadata,stage/'build.json')
    shutil.copy2(HERE/'mobile/README.md',stage/'START-HERE.md')
    names=('OpenWrt-Tools.apk','build.json','START-HERE.md')
    sums={name:sha(stage/name) for name in names}
    if sums[apk.name]!=build['files'][apk.name]:raise ValueError('Staged APK checksum differs')
    (stage/'SHA256SUMS').write_text(''.join(value+'  '+name+'\n' for name,value in sums.items()))
    output.mkdir(parents=True)
    for name in (*names,'SHA256SUMS'):
        partial=output/(name+'.partial');shutil.copy2(stage/name,partial)
        if sha(partial)!=sha(stage/name):raise ValueError('Published bytes differ: '+name)
        os.replace(partial,output/name)
    shutil.rmtree(stage)
    print(output)


if __name__=='__main__':main()
