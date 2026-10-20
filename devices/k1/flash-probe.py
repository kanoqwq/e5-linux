#!/usr/bin/env python3
"""Check a K1 probe by default; --execute explicitly writes its inactive slot."""
import argparse
import json
from pathlib import Path
import runpy
import sys

HERE = Path(__file__).resolve().parent
TOP = HERE.parents[1]
sys.path.insert(0, str(TOP / 'installer'))
from backend.transport import Adb


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('backup', 'image'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--busybox', type=Path)
    parser.add_argument('--reboot', action='store_true')
    parser.add_argument('--previous-image', type=Path)
    args = parser.parse_args()
    if args.reboot and not args.execute or args.execute and not args.busybox:
        parser.error('--reboot needs --execute; --execute needs --busybox')
    profile = json.loads((HERE / 'profile.json').read_text(encoding='utf-8'))
    helper = runpy.run_path(str(TOP / 'devices/common/probe-deploy.py'))
    adb = Adb(TOP, args.serial)
    if args.execute:
        result = helper['execute'](adb, args.backup, args.image, args.busybox, profile, reboot=args.reboot, previous_image=args.previous_image)
    else:
        result = helper['preflight'](adb, args.backup, args.image, profile, previous_image=args.previous_image)
    print(json.dumps({k: v for k, v in result.items() if k != 'cid'}, indent=2))


if __name__ == '__main__':
    main()
