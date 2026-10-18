#!/usr/bin/env python3
"""Write a verified T512N probe to inactive B; keep stock Android A intact."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import zlib


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--previous-image', type=Path,
                        help='Previously verified probe, excluding its diagnostic log tail')
    parser.add_argument('--reboot', action='store_true')
    args = parser.parse_args()
    adb = ['adb', '-s', args.serial]
    def read(command):
        return subprocess.check_output(adb + ['exec-out', 'sh', '-c', command], timeout=180)
    def value(command):
        return read(command).decode().strip()
    if (value('getprop ro.product.model') != 'T512N' or
            value('getprop ro.product.device') != 'PB8' or
            'uid=0(root)' not in value('id')):
        raise ValueError('Expected T512N/PB8 with root adbd')
    backup = json.loads((args.backup / 'manifest.json').read_text())
    for name in ('boot_a.img', 'boot_b.img', 'misc-head.bin', 'cid.txt'):
        if digest((args.backup / name).read_bytes()) != backup[name]['sha256']:
            raise ValueError('Backup checksum mismatch: ' + name)
    if value('cat /sys/block/mmcblk0/device/cid') != (args.backup / 'cid.txt').read_text().strip():
        raise ValueError('Different physical device')
    if value('getprop ro.boot.slot_suffix') != '_a':
        raise ValueError('Start this first probe from stock Android A')
    # The owner confirmed AVB is not enforced on this engineering firmware.
    # Do not unlock, erase or change vbmeta based on its reported lock state.
    if int(value('cat /sys/class/power_supply/battery/capacity')) < 10:
        raise ValueError('Charge the battery before the first boot test')
    for slot in ('a', 'b'):
        if slot == 'b' and args.previous_image:
            previous = json.loads(args.previous_image.with_suffix('.json').read_text())
            if digest(args.previous_image.read_bytes()) != previous['sha256']:
                raise ValueError('Previous probe checksum mismatch')
            actual = value('dd if=/dev/block/by-name/boot_b bs=1M count=56 2>/dev/null | sha256sum').split()[0]
            expected = previous['sha256_head56m']
        else:
            actual = value('sha256sum /dev/block/by-name/boot_' + slot).split()[0]
            expected = backup['boot_' + slot + '.img']['sha256']
        if actual != expected:
            raise ValueError('Stock boot changed; collect and review it before flashing')
    manifest = json.loads(args.image.with_suffix('.json').read_text())
    if (manifest.get('profile') != 't512n' or manifest.get('probe') is not True or
            manifest.get('stock_boot_sha256') != backup['boot_b.img']['sha256'] or
            manifest.get('cid_sha256') != backup['cid.txt']['sha256']):
        raise ValueError('Probe was not built for this stock T512N backup')
    image = args.image.read_bytes()
    if digest(image) != manifest['sha256'] or len(image) != int(value('blockdev --getsize64 /dev/block/by-name/boot_b')):
        raise ValueError('Probe size/checksum mismatch')
    init = args.image.with_suffix('.init').read_text()
    if (digest(args.image.with_suffix('.init').read_bytes()) != manifest.get('init_sha256') or
            'Android A fallback restored and verified' not in init or 'Never mount or format userdata' not in init):
        raise ValueError('Expected the bounded T512N probe init')
    # The generated image checksum protects the embedded init; guard its kernel
    # profile too, rather than accepting an arbitrary previously built boot.img.
    if args.image.with_suffix('.modules').read_text().count('edt-ft5x06.ko') != 1:
        raise ValueError('Expected the T512N touchscreen probe')
    current = read('dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null')
    allowed_control = [(args.backup / 'misc-head.bin').read_bytes()[2048:2080]]
    if args.previous_image:
        # The bounded probe deliberately restores the generated successful-A
        # control (tries=1), which can differ from the original stock tries=7.
        allowed_control.append(bytes.fromhex(previous['misc_slot_a_hex']))
    if current not in allowed_control:
        raise ValueError('Live boot control changed; inspect before arming the trial')
    trial = args.image.with_suffix('.misc-slot-b-trial.bin')
    control = trial.read_bytes()
    if (len(control) != 32 or control.hex() != manifest['misc_slot_b_trial_hex'] or
            control[4:8] != b'BCAB' or
            zlib.crc32(control[:28]) != struct.unpack('<I', control[28:])[0]):
        raise ValueError('Invalid trial boot control')
    restore = args.image.with_suffix('.misc-original.bin')
    restore.write_bytes(current)
    for source, remote in ((args.image, '/data/local/tmp/t512n-probe.img'),
                           (trial, '/data/local/tmp/t512n-probe-trial.bin'),
                           (restore, '/data/local/tmp/t512n-probe-restore.bin')):
        subprocess.run(adb + ['push', str(source), remote], check=True)
        if value('sha256sum ' + remote).split()[0] != digest(source.read_bytes()):
            raise ValueError('Upload checksum mismatch')
    read('dd if=/data/local/tmp/t512n-probe.img of=/dev/block/by-name/boot_b bs=4M && sync')
    if value('sha256sum /dev/block/by-name/boot_b').split()[0] != manifest['sha256']:
        raise ValueError('Boot B read-back failed; Android A remains selected')
    read('dd if=/data/local/tmp/t512n-probe-trial.bin of=/dev/block/by-name/misc bs=1 seek=2048 conv=notrunc && sync')
    if read('dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null') != control:
        read('dd if=/data/local/tmp/t512n-probe-restore.bin of=/dev/block/by-name/misc bs=1 seek=2048 conv=notrunc && sync')
        if read('dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null') != current:
            raise RuntimeError('Boot control restoration failed; do not reboot')
        raise RuntimeError('Trial verification failed; original boot control restored')
    print('Probe verified in B; stock Android A and userdata preserved', flush=True)
    if args.reboot:
        subprocess.run(adb + ['reboot'], check=True)


if __name__ == '__main__':
    main()
