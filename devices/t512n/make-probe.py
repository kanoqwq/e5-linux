#!/usr/bin/env python3
"""Build a bounded T512N RAM-only hardware probe; does not access ADB or flash."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

TOP = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('backup', 'kernel', 'busybox', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--stay', type=int, default=90)
    args = parser.parse_args()
    if not 30 <= args.stay <= 180:
        parser.error('stay must be 30..180 seconds')
    manifest = json.loads((args.backup / 'manifest.json').read_text())
    for name in ('boot_b.img', 'misc-head.bin', 'properties.txt', 'cid.txt'):
        if hashlib.sha256((args.backup / name).read_bytes()).hexdigest() != manifest[name]['sha256']:
            raise ValueError('Backup checksum mismatch: ' + name)
    if '[ro.product.model]: [T512N]' not in (args.backup / 'properties.txt').read_text():
        raise ValueError('Not a T512N stock backup')
    release = (args.kernel / 'kernel.release').read_text().strip()
    if '-t512n-' not in release:
        raise ValueError('Not a T512N kernel: ' + release)
    if b'T512N-0x11' not in (args.kernel / 'modules/bq2560x-charger.ko').read_bytes():
        raise ValueError('Rebuild the T512N charger module before preparing a probe')
    cid = (args.backup / 'cid.txt').read_text().strip()
    if len(cid) != 32 or any(c not in '0123456789abcdef' for c in cid):
        raise ValueError('Invalid physical device identity')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    text = (TOP / 'upstream/init-bringup').read_text()
    anchor = 'log "userspace up: $(uname -a)"'
    if text.count(anchor) != 1:
        raise ValueError('Unexpected shared probe init')
    preparation = '''
# Restore Android A before board module probes. Never mount or format userdata.
i=0
while [ ! -r /sys/block/mmcblk0/device/cid ] && [ "$i" -lt 10 ]; do
    sleep 1; i=$((i + 1))
done
if [ "$(cat /sys/block/mmcblk0/device/cid 2>/dev/null)" != "EXPECTED_CID" ]; then
    log "T512N identity mismatch; refusing partition writes"
    reboot -f
    while :; do sleep 60; done
fi
misc=
for u in /sys/class/block/mmcblk0p*/uevent; do
    grep -qx PARTNAME=misc "$u" && misc=/dev/$(basename "$(dirname "$u")")
done
if [ ! -b "$misc" ]; then
    log "No misc; aborting probe"; reboot -f
    while :; do sleep 60; done
fi
if ! dd if=/etc/misc-bc-slot-a.bin of="$misc" bs=1 seek=2048 conv=notrunc ||
   ! dd if="$misc" of=/tmp/bc.bin bs=1 skip=2048 count=32 ||
   ! cmp /tmp/bc.bin /etc/misc-bc-slot-a.bin; then
    log "Android fallback verification failed"; reboot -f
    while :; do sleep 60; done
fi
sync
log "Android A fallback restored and verified"
(sleep WATCHDOG_SECONDS; persist; sync; reboot -f) &
while read -r module; do
    [ -n "$module" ] || continue
    insmod "/linux-modules/$module"
    log "module $module rc=$?"
done < /etc/module-order
for p in /sys/class/power_supply/*; do
    log "power ${p##*/}: $(tr '\\n' ' ' < "$p/uevent")"
done
log "input: $(cat /proc/bus/input/devices)"
log "display: $(ls /sys/class/drm /sys/class/backlight)"
persist
'''.replace('EXPECTED_CID', cid).replace('WATCHDOG_SECONDS', str(args.stay + 120))
    text = text.replace(anchor, anchor + preparation)
    text = text.replace('stay=${E5_STAY:-180}', 'stay=' + str(args.stay))
    text = text.replace('E5LINUX', 'T512NPROBE' + cid[-8:])
    text = text.replace('E5 Linux USB Ethernet (mainline bring-up)', 'T512N hardware probe')
    text = text.replace('02:50:00:00:e5:01', '02:50:' + ':'.join(cid[-8:][i:i+2] for i in range(0, 8, 2)))
    text = text.replace('02:50:00:00:e5:02', '02:51:' + ':'.join(cid[-8:][i:i+2] for i in range(0, 8, 2)))
    init = args.out.with_suffix('.init')
    init.write_text(text)
    # First pass exercises battery/USB, display and touch, without starting CP
    # or requesting Wi-Fi/audio firmware from inaccessible encrypted userdata.
    needed = '''sprd_soc_thm.ko sprd_vpu_pw_domain.ko unisoc-iommu.ko
drm_kms_helper.ko drm_dma_helper.ko drm_client_lib.ko ocp2131.ko sprd-drm.ko
sprd_battery_info.ko sprd-charger-manager.ko ump96xx_fuel_gauge.ko
bq2560x-charger.ko edt-ft5x06.ko sc27xx-vibra.ko matrix-keymap.ko sprd_keypad.ko'''.split()
    order = args.out.with_suffix('.modules')
    order.write_text(''.join(n + '\n' for n in needed))
    subprocess.run([sys.executable, str(TOP / 'boot/build-boot-image.py'),
        '--stock-boot', str(args.backup / 'boot_b.img'),
        '--misc-head', str(args.backup / 'misc-head.bin'),
        '--kernel', str(args.kernel / 'Image.lk'), '--modules', str(args.kernel / 'modules'),
        '--module-order', str(order), '--init', str(init), '--busybox', str(args.busybox),
        '--cmdline', '', '--out', str(args.out)], check=True)
    info_path = args.out.with_suffix('.json')
    info = json.loads(info_path.read_text())
    info.update(profile='t512n', probe=True, kernel_release=release,
                stock_boot_sha256=manifest['boot_b.img']['sha256'],
                cid_sha256=manifest['cid.txt']['sha256'],
                init_sha256=hashlib.sha256(init.read_bytes()).hexdigest())
    info_path.write_text(json.dumps(info, indent=2) + '\n')


if __name__ == '__main__':
    main()
