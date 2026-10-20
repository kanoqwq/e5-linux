#!/usr/bin/env python3
"""Collect K1-owned vendor files through the shared installer, without installing."""
import argparse
from contextlib import ExitStack
import importlib.util
import hashlib
import json
from pathlib import Path
import secrets
import re
import shutil
import sys
import tempfile

HERE = Path(__file__).resolve().parent
TOP = HERE.parents[1]
sys.path.insert(0, str(TOP / 'installer'))
from backend.state import DeviceLock, app_directory, atomic_json, sha
from backend.transport import Adb


def collect(adb, destination, busybox):
    if not adb.serial or adb.root_status() != 'ready':
        raise ValueError('Select one rooted Android device')
    if (adb.shell('getprop ro.product.model').strip() != 'angelcare K1' or
            adb.shell('getprop ro.product.device').strip() != 'ums9157_2h10'):
        raise ValueError('Expected angelcare K1 / ums9157_2h10')
    slot = adb.shell('getprop ro.boot.slot_suffix').strip()
    if slot not in ('_a', '_b'):
        raise ValueError('Android slot unavailable')
    cid = adb.shell('cat /sys/class/block/mmcblk0/device/cid', root=True).strip()
    if not re.fullmatch('[a-f0-9]{32}', cid):
        raise ValueError('Invalid K1 eMMC identity')
    destination = Path(destination).resolve()
    with ExitStack() as locks:
        for identity in sorted({adb.serial, cid}):
            locks.enter_context(DeviceLock(app_directory(), identity))
        destination.mkdir(parents=True, exist_ok=False, mode=0o700)
        spec = importlib.util.spec_from_file_location('k1_shared_firmware', TOP / 'openwrt/bundle/flash.py')
        flash = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(flash)
        flash.ADB, flash.SERIAL, flash.SU = adb.exe, adb.serial, adb.su
        flash.ANDROID_SLOT, flash.LINUX_SLOT = slot[1:], 'b' if slot == '_a' else 'a'
        flash.SCRIPTS = str(TOP)
        with tempfile.TemporaryDirectory() as directory:
            files = Path(directory)
            shutil.copyfile(busybox, files / 'busybox')
            shutil.copyfile(TOP / 'openwrt/bundle/collect-device-files.sh', files / 'collect-device-files.sh')
            (files / 'device-profile.json').write_text(json.dumps({'profile': 'k1', 'android_models': ['angelcare K1']}))
            flash.F = str(files)
            remote = '/data/local/tmp/k1-vendor-' + secrets.token_hex(16) + '.tar'
            try:
                flash.android_tools()
                flash.pull_device_files(str(destination), remote)
                expected = adb.shell('sha256sum ' + remote, root=True, timeout=120).split()[0]
                size = int(adb.shell('wc -c < ' + remote, root=True).strip())
                target = destination / 'device-files.tar'
                adb.call('pull', remote, str(target), timeout=120)
                if target.stat().st_size != size or sha(target) != expected:
                    raise ValueError('Collected vendor archive verification failed')
                if adb.shell('getprop ro.boot.slot_suffix').strip() != slot:
                    raise ValueError('Android slot changed during firmware collection')
                atomic_json(destination / 'manifest.json', {
                    'profile': 'k1', 'android_slot': slot[1:], 'cid_sha256': hashlib.sha256((cid + '\n').encode()).hexdigest(),
                    'archive_sha256': expected, 'archive_bytes': size,
                    'raw': {p.name: sha(p) for p in (destination / 'raw').iterdir() if p.is_file()}})
                return target
            finally:
                adb.shell('rm -f ' + remote, root=True)
                if flash.ANDROID_BUSYBOX:
                    adb.shell('rm -f ' + flash.ANDROID_BUSYBOX + '; rmdir ' + str(Path(flash.ANDROID_BUSYBOX).parent).replace('\\', '/'), root=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--busybox', type=Path, required=True)
    args = parser.parse_args()
    print(collect(Adb(TOP, args.serial), args.out, args.busybox))


if __name__ == '__main__':
    main()
