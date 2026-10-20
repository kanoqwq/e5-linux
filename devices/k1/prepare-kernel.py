#!/usr/bin/env python3
"""Reuse the UMS9620 platform while selecting K1's own charger policy."""
import argparse
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent


def apply(kernel):
    charger = runpy.run_path(str(HERE / 'charger.py'))['apply']
    runpy.run_path(str(HERE.parent / 'm8/prepare-kernel.py'))['apply'](
        kernel, charger_adapter=charger)
    # Keep the live K1 panel node, timings, JEITA/OCV tables and PDOs intact.
    # The shared driver uses the device's original bootloader-selected panel.


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kernel', type=Path)
    apply(parser.parse_args().kernel)
