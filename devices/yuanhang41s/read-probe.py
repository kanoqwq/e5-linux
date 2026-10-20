#!/usr/bin/env python3
"""Read the bounded probe's log from its own boot tail after Android returns."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--image', required=True, type=Path)
    args = parser.parse_args()
    meta = json.loads(args.image.with_suffix('.json').read_text(encoding='utf-8'))
    if meta.get('profile') != 'yuanhang41s' or meta.get('probe') is not True:
        raise ValueError('Not a 41S probe')
    adb = ['adb', '-s', args.serial]

    def read(command):
        return subprocess.check_output(adb + ['exec-out', 'su', '-c',
            'stty -onlcr 2>/dev/null; ' + command], timeout=45)

    if read('getprop ro.product.device').strip() != b'P720S11':
        raise ValueError('Different device')
    if read('getprop ro.boot.slot_suffix').strip() != ('_' + meta['android_slot']).encode():
        raise ValueError('Device did not return to its original Android slot')
    output = args.image.with_name(args.image.stem + '-logs')
    output.mkdir(exist_ok=True)
    data = read('dd if=/dev/block/by-name/boot_' + meta['linux_slot'] +
                ' bs=4096 skip=14336 count=1024 2>/dev/null')
    start = data.find(b'E5-PERSIST-BEGIN')
    end = data.find(b'E5-PERSIST-END', start)
    if start < 0 or end < 0:
        raise ValueError('No complete probe log; inspect pstore before another boot')
    data = data[start:end + len(b'E5-PERSIST-END')]
    (output / 'persist.txt').write_bytes(data + b'\n')
    (output / 'pstore.txt').write_bytes(read('cat /sys/fs/pstore/* 2>/dev/null || :'))
    print(data[:300].decode(errors='replace'))
    print('Saved probe logs:', output)


if __name__ == '__main__':
    main()
