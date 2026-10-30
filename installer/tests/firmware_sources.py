"""Optional firmware integration sources; core installer tests remain standalone."""
import os
from pathlib import Path
import unittest


def firmware_root(family):
    name = {'e5':'OPENWRT_E5_SOURCE','mu300':'OPENWRT_MU300_SOURCE'}[family]
    marker = {'e5':'openwrt/bundle/flash.py','mu300':'desktop/installer.py'}[family]
    root = Path(os.environ[name]).resolve() if name in os.environ else Path(__file__).resolve().parents[2]
    if not (root/marker).is_file():
        raise unittest.SkipTest('Optional '+family+' firmware integration: set '+name)
    return root
