#!/usr/bin/env python3
"""Read selected T512N stock drivers/RIL and their init policy, never raw NV."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--serial', required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    adb = ['adb', '-s', args.serial, 'exec-out']
    def read(command):
        return subprocess.check_output(adb + ['sh', '-c', command], timeout=60)
    if read('getprop ro.product.model').strip() != b'T512N' or read('getprop ro.product.device').strip() != b'PB8':
        raise SystemExit('Expected T512N/PB8')
    if b'uid=0(root)' not in read('id'):
        raise SystemExit('Root adbd required')
    paths = ['/vendor/lib/modules/' + name + '.ko' for name in (
        'sprd-drm', 'focaltech_ft6336u_ts', 'bq2560x-charger', 'sc27xx_fuel_gauge',
        'sprd-charger-manager', 'sprd_battery_info', 'sc27xx_typec', 'sc27xx_pd',
        'musb_sprd', 'phy-sprd-qogirn6lite', 'snd-soc-sprd-pa-ucp1301',
        'sprd_wlan_combo', 'wcn_bsp')]
    paths += ['/vendor/bin/' + name for name in ('modem_control', 'cp_diskserver', 'refnotify')]
    paths += ['/vendor/lib/libimpl-ril.so', '/vendor/lib64/libimpl-ril.so']
    paths += ['/vendor/etc/init/' + name + '.rc' for name in ('modem_control', 'cp_diskserver', 'refnotify', 'unisoc.rild', 'connmgr', 'charge', 'charged')]
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for path in paths:
        target = args.out / path.lstrip('/')
        remote_digest = read('sha256sum ' + path).decode().split()[0]
        data = target.read_bytes() if target.exists() else b''
        for attempt in range(3):
            if data and hashlib.sha256(data).hexdigest() == remote_digest:
                break
            data = read('cat ' + path)
        digest = hashlib.sha256(data).hexdigest()
        if not data or digest != remote_digest:
            raise RuntimeError('Stock file verification failed: ' + path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        manifest[path] = {'bytes': len(data), 'sha256': digest}
        (args.out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        print(path, len(data), flush=True)


if __name__ == '__main__':
    main()
