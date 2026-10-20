#!/usr/bin/env python3
"""Build a bounded K1 probe; never connects to or flashes a device."""
import argparse
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('backup', 'kernel', 'busybox', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--stay', type=int, default=90)
    parser.add_argument('--display-test', type=Path)
    parser.add_argument('--ram-handoff', action='store_true')
    args = parser.parse_args()
    requested = (HERE.parent / 'm8/probe-module-order.txt').read_text().split()
    runpy.run_path(str(HERE.parent / 'common/bringup-probe.py'))['make'](
        args.backup, args.kernel, args.busybox, args.out, 'k1', requested,
        stay=args.stay, display_test=args.display_test, ram_handoff=args.ram_handoff)


if __name__ == '__main__':
    main()
