#!/usr/bin/env python3
"""Save verified K1 boot recovery images; no partition writes or reboots."""
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
    parser.add_argument('--serial', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    profile = json.loads((HERE / 'profile.json').read_text(encoding='utf-8'))
    runpy.run_path(str(TOP / 'devices/common/stock-backup.py'))['collect'](
        Adb(TOP, args.serial), args.out, profile)


if __name__ == '__main__':
    main()
