#!/usr/bin/env python3
"""Build a temporary OpenWrt RAM boot for encrypted-data T512N bring-up.

The root archive is supplied over the direct USB link. Android A remains the
default and userdata is never mounted, repartitioned or formatted by this init.
This is a development probe, not an installation or persistent-storage mode.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
TOP = HERE.parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('backup', 'kernel', 'busybox', 'root-archive', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--port', type=int, default=18880)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Invalid USB transfer port')
    checksum = hashlib.sha256()
    with args.root_archive.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    digest = checksum.hexdigest()
    subprocess.run([sys.executable, str(HERE / 'make-probe.py'),
        '--backup', str(args.backup), '--kernel', str(args.kernel),
        '--busybox', str(args.busybox), '--out', str(args.out), '--stay', '180'], check=True)
    init = args.out.with_suffix('.init')
    text = init.read_text()
    anchor = '    stay=180 t=0'
    if text.count(anchor) != 1:
        raise ValueError('Unexpected bounded probe wait loop')
    bootstrap = '''
    log "RAM bringup: waiting for verified root over direct USB"
    if timeout 180 nc -l -p PORT > /tmp/root.tar.gz &&
       echo "ROOT_SHA  /tmp/root.tar.gz" | sha256sum -c - &&
       mkdir -p /newroot && mount -t tmpfs -o size=1200M,mode=0755 tmpfs /newroot &&
       tar -xzf /tmp/root.tar.gz -C /newroot &&
       [ -x /newroot/sbin/init ] &&
       [ "$(cat /newroot/etc/e5/device-profile)" = t512n ]; then
        rm -f /tmp/root.tar.gz
        mkdir -p /lib/firmware
        mount --bind /newroot/lib/firmware /lib/firmware
        # Use the same ordered vendor modules as the normal boot image, with
        # the device's own firmware already reachable on both sides of pivot.
        while read -r module; do
            [ -n "$module" ] || continue
            name=${module%.ko}; name=$(echo "$name" | tr '-' '_')
            grep -q "^$name " /proc/modules && continue
            insmod "/newroot/lib/modules/$(uname -r)/$module"
            log "RAM module $module rc=$?"
        done < /e5-overlay/etc/ram-module-order
        mkdir -p /run /newroot/run /newroot/proc /newroot/sys /newroot/dev
        mount -t tmpfs -o mode=0755 tmpfs /run
        mkdir -p /run/e5linux /newroot/etc/e5linux
        cp /etc/misc-bc-slot-a.bin /etc/misc-bc-slot-b-trial.bin /run/e5linux/
        echo "$misc" > /run/e5linux/misc-dev
        echo android > /newroot/etc/e5linux/default-boot
        for node in /dev/dri/* /sys/class/backlight/*/brightness /sys/class/backlight/*/bl_power; do
            [ ! -e "$node" ] || chmod 0666 "$node"
        done
        log "RAM root ready; starting full OpenWrt; Android A remains default"
        persist
        for proc in /proc/[0-9]*; do
            [ "${proc#/proc/}" = 1 ] || kill -9 "${proc#/proc/}" 2>/dev/null
        done
        umount /config 2>/dev/null
        mount --move /dev /newroot/dev
        mount --move /sys /newroot/sys
        mount --move /run /newroot/run
        mount --move /proc /newroot/proc
        export PATH=/sbin:/usr/sbin:/bin:/usr/bin HOME=/root TERM=vt100
        exec switch_root /newroot /sbin/init
    fi
    log "RAM root transfer/verification failed; bounded Android fallback retained"
'''.replace('PORT', str(args.port)).replace('ROOT_SHA', digest)
    init.write_text(text.replace(anchor, bootstrap + anchor))
    overlay = args.out.with_suffix('.overlay')
    (overlay / 'etc').mkdir(parents=True, exist_ok=True)
    (overlay / 'etc/ram-module-order').write_bytes((args.kernel / 'module-order.txt').read_bytes())
    subprocess.run([sys.executable, str(TOP / 'boot/build-boot-image.py'),
        '--stock-boot', str(args.backup / 'boot_b.img'), '--misc-head', str(args.backup / 'misc-head.bin'),
        '--kernel', str(args.kernel / 'Image.lk'), '--modules', str(args.kernel / 'modules'),
        '--module-order', str(args.out.with_suffix('.modules')), '--init', str(init),
        '--overlay', str(overlay), '--busybox', str(args.busybox), '--cmdline', '', '--out', str(args.out)], check=True)
    manifest_path = args.out.with_suffix('.json')
    info = json.loads(manifest_path.read_text())
    backup = json.loads((args.backup / 'manifest.json').read_text())
    info.update(profile='t512n', probe=True, ram_boot=True, root_archive_sha256=digest, transfer_port=args.port,
                kernel_release=(args.kernel / 'kernel.release').read_text().strip(),
                stock_boot_sha256=backup['boot_b.img']['sha256'], cid_sha256=backup['cid.txt']['sha256'],
                init_sha256=hashlib.sha256(init.read_bytes()).hexdigest())
    manifest_path.write_text(json.dumps(info, indent=2) + '\n')


if __name__ == '__main__':
    main()
