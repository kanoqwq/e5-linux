"""Explicit firmware checkout for firmware-only refresh commands."""
import os
from pathlib import Path


def firmware_root(family=None):
    configured = os.environ.get('OPENWRT_FIRMWARE_SOURCE')
    root = Path(configured).resolve() if configured else Path(__file__).resolve().parent.parent
    e5 = (root/'openwrt/bundle/flash.py').is_file()
    mu300 = (root/'desktop/installer.py').is_file()
    if family == 'e5' and not e5 or family == 'mu300' and not mu300 or not (e5 or mu300):
        raise ValueError('Set OPENWRT_FIRMWARE_SOURCE to the matching firmware checkout; installer builds need no firmware checkout')
    return root
