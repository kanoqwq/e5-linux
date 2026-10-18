#!/usr/bin/env python3
"""Read T512N stock evidence and boot recovery images through root adbd.

No su wrapper, partition writes, slot changes or NV/calibration reads.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    adb = ['adb', '-s', args.serial, 'exec-out']

    def read(command):
        return subprocess.check_output(adb + ['sh', '-c', command], timeout=180)

    if read('getprop ro.product.model').strip() != b'T512N':
        raise SystemExit('Expected T512N; refusing another device')
    if read('getprop ro.product.device').strip() != b'PB8':
        raise SystemExit('Unexpected T512N board; inspect before collecting')
    if b'uid=0(root)' not in read('id'):
        raise SystemExit('Run adb root first')
    args.out.mkdir(parents=True, exist_ok=False)
    manifest = {}

    def save(name, command, verify=False):
        data = read(command)
        if not data:
            raise RuntimeError('Empty evidence: ' + name)
        digest = hashlib.sha256(data).hexdigest()
        if verify:
            remote = read('sha256sum ' + command.removeprefix('cat ')).decode().split()[0]
            if digest != remote:
                raise RuntimeError('Read-back hash mismatch: ' + name)
        (args.out / name).write_bytes(data)
        manifest[name] = {'bytes': len(data), 'sha256': digest}
        (args.out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        print(name, len(data), flush=True)

    for name, command in {
        'properties.txt': 'getprop',
        'uname.txt': 'uname -a',
        'cid.txt': 'cat /sys/block/mmcblk0/device/cid',
        'cmdline.txt': 'cat /proc/cmdline',
        'modules.txt': 'cat /proc/modules',
        'inputs.txt': 'cat /proc/bus/input/devices',
        'partitions.txt': 'ls -l /dev/block/by-name; cat /proc/partitions',
        'mounts.txt': 'cat /proc/mounts; df -h /data',
        'dmesg.txt': 'dmesg',
        'live.dtb': 'cat /sys/firmware/fdt',
        'config.gz': 'cat /proc/config.gz',
        'i2c.txt': 'for d in /sys/bus/i2c/devices/*; do echo "$d"; cat "$d/name" 2>/dev/null; readlink "$d/driver"; done; true',
        'power.txt': 'for d in /sys/class/power_supply/*; do echo "$d"; cat "$d/uevent"; done',
        'display.txt': 'for d in /sys/class/backlight/*; do echo "$d"; cat "$d/brightness" "$d/max_brightness"; done; dumpsys SurfaceFlinger --display-id; dumpsys display',
        'modules-load.txt': 'cat /vendor/lib/modules/modules.load*',
        'crypto.txt': 'getprop ro.crypto.state; getprop ro.crypto.type; getprop ro.crypto.metadata.enabled; cat /vendor/etc/fstab*; cat /proc/mounts',
        'runtime-files.txt': 'ls -l /vendor/bin/modem_control /vendor/bin/cp_diskserver /vendor/bin/refnotify /vendor/lib/libimpl-ril.so /vendor/lib64/libimpl-ril.so /vendor/firmware; true',
    }.items():
        save(name, command)
    for part in ('boot_a', 'boot_b', 'vendor_boot_a', 'vendor_boot_b',
                 'dtb_a', 'dtb_b', 'dtbo_a', 'dtbo_b', 'vbmeta_a', 'vbmeta_b', 'sml_a', 'sml_b'):
        block = '/dev/block/by-name/' + part
        if read('test -e ' + block + ' && echo yes || true').strip() != b'yes':
            continue
        save(part + '.img', 'cat ' + block, verify=True)
    save('misc-head.bin', 'dd if=/dev/block/by-name/misc bs=4096 count=1 2>/dev/null')


if __name__ == '__main__':
    main()
