#!/usr/bin/env python3
"""Check package identity and the actual staged handheld root without device I/O."""
import argparse
import json
import os
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
TOP = HERE.parents[1]


def check(root):
    assert (root/'etc/e5/device-profile').read_text().strip() == 't512n'
    assert (root/'etc/hostname').read_text().strip() == 'T512N'
    assert (root/'etc/e5/device-model').read_text().strip() == 'T512N'
    assert (root/'etc/e5/device-hostname').read_text().strip() == 'T512N'
    modem = (root/'etc/config/unisoc_modem').read_text()
    assert 'auto_work_mode' not in modem and 'lte_supported_bands' not in modem
    assert not (root/'etc/e5/receiver-output').exists()
    assert not (root/'etc/e5/receiver-volume-ceiling').exists()
    vendor = (root/'opt/e5/vendor-start.sh').read_text()
    assert '/run/e5-bootconfig.android' in vendor
    assert 'nr_fixnv*|nr_runtimenv*|nr_deltanv*|calinv' in vendor
    audio = (root/'opt/e5/e5-audio-dsp').read_text()
    assert ' snd-soc-sprd-pa-aw87xxx' not in audio
    assert 'echo sound@0 > "$drv/unbind"' not in audio
    loader = (root/'opt/e5/android-run').read_text()
    assert '2) android_lib=lib64' in loader
    for service in ('e5-board-identity', 'unisoc-battery', 't551m-at', 'e5-modemd', 'e5-charge-mode'):
        assert (root/'etc/init.d'/service).is_file(), service
    assert (root/'etc/rc.d/S86unisoc-battery').is_file()
    assert os.readlink(root/'etc/rc.d/S86unisoc-battery') == '../init.d/unisoc-battery'
    assert 't512n' in (root/'etc/uci-defaults/98-t551m-network-startup').read_text()
    assert 'INPUT_PROP_DIRECT' in (root/'usr/libexec/t551m-screen-power').read_text()
    bt = root/'etc/bluetooth/main.conf'
    assert not bt.exists() or 'Name = E5\n' not in bt.read_text()
    assert json.loads((root/'etc/e5/feature-port.json').read_text())['profile'] == 't512n'


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path)
args = parser.parse_args()
models = runpy.run_path(str(TOP/'openwrt/bundle/finalize-package.py'))['MODELS']
assert models['t512n'] == 'T512N'
assert models['e5'] == 'E5' and models['t551m'] == 'T551M' and models['m8'] == 'VIPME M8'
if args.root:
    # Shared stage is repeatable and preserves explicit user radio policy.
    stage = runpy.run_path(str(HERE/'stage.py'))['stage']
    stage(args.root)
    check(args.root)
print('T512N profile/identity checks PASS' + (' (actual offline root)' if args.root else ''))
