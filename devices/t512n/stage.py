#!/usr/bin/env python3
"""Stage T512N identity and the shared handheld stack into a clean offline root."""
import argparse
import json
import os
from pathlib import Path
import runpy

TOP = Path(__file__).resolve().parents[2]


def stage(root):
    root = Path(root).resolve()
    if os.name == 'nt' or root == Path('/'):
        raise ValueError('Use Linux/WSL with an offline T512N root')
    marker = root / 'etc/e5/device-profile'
    if marker.exists() and marker.read_text().strip() != 't512n':
        raise ValueError('Refusing to convert another device profile')
    for name in ('etc/init.d/e5-vendor', 'etc/init.d/e5-sipc-wwan',
                 'usr/libexec/e5-infoscreen/session', 'usr/share/rpcd/ucode/e5-infoscreen.uc'):
        if not (root / name).is_file():
            raise ValueError('Missing shared base component: ' + name)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('t512n\n')
    for name, value in (('device-model', 'T512N'), ('device-hostname', 'T512N')):
        (marker.parent / name).write_text(value + '\n')
    shared = runpy.run_path(str(TOP / 'devices/e5/stage.py'))
    copy, link = shared['copy'], shared['link']
    for path in (TOP / 'rootfs/overlay/opt/e5').iterdir():
        if path.is_file():
            copy(root, path, 'opt/e5/' + path.name)
    audio = root / 'opt/e5/e5-audio-dsp'
    text = audio.read_text().replace(' snd-soc-sprd-pa-aw87xxx', '')
    start = text.index('bind_card() {')
    end = text.index('\n}\n', start) + 3
    # As with the existing UMS9621 handheld port, do not unbind a registered
    # card while its original GPIO hook can still be referenced.
    text = text[:start] + '''bind_card() {
    bind_pd_consumers || :
    for attempt in $(seq 1 20); do
        grep -q sprdphone /proc/asound/cards 2>/dev/null && return 0
        sleep 1
    done
    echo "Audio card unavailable; skipping unsafe legacy unbind" >&2
    return 1
}
''' + text[end:]
    audio.write_text(text)
    for path in ('devices/wifi-common.py', 'devices/modem-recovery.py', 'devices/battery-guard.py'):
        runpy.run_path(str(TOP / path))['stage'](root)
    for name in ('etc/init.d/e5-board-identity', 'etc/uci-defaults/97-e5-hostname'):
        copy(root, TOP / 'openwrt/overlay' / name, name)
    link(root, 'S11e5-board-identity', 'e5-board-identity')
    # UI, AT ownership, SMS, APN, SIM controls and traffic use the same code.
    runpy.run_path(str(TOP / 'devices/t551m/ui.py'))['stage'](root, profile='t512n')
    shared['stage_nv'](root)
    for source, target in (
        ('charge-mode.py', 'usr/libexec/e5-charge-mode'),
        ('charge-screen.py', 'usr/libexec/e5-charge-screen'),
        ('charge-art.zip', 'usr/share/e5-charge/art.zip'),
        ('charge-mode-service', 'etc/init.d/e5-charge-mode'),
        ('usb-service', 'etc/init.d/t551m-usb'),
        ('usb-host-storage.modules', 'etc/modules.d/usb-host-storage'),
        ('usb-host-network.modules', 'etc/modules.d/usb-host-network'),
    ):
        copy(root, TOP / 'devices/t551m' / source, target,
             0o644 if source.endswith(('.zip', '.modules')) else 0o755)
    link(root, 'S14e5-charge-mode', 'e5-charge-mode')
    link(root, 'S25t551m-usb', 't551m-usb')
    bt = root / 'etc/bluetooth/main.conf'
    if bt.exists():
        bt.write_text(bt.read_text().replace('Name = E5\n', 'Name = OpenWrt\n'))
    (root / 'usr/share/e5-infoscreen/ADAPTATION').write_text('t512n-mobile-v1\n')
    (marker.parent / 'feature-port.json').write_text(json.dumps({
        'profile': 't512n', 'schema': 1, 'status': 'bringup',
        'hardware_validation': 'pending; not a verified release',
        'vendor_abi': 'arm64', 'display': [320, 480],
    }, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    stage(parser.parse_args().root)
