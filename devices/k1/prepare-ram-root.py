#!/usr/bin/env python3
"""Complete an offline K1 root for a bounded RAM-only userspace trial."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import tarfile

TOP = Path(__file__).resolve().parents[2]


def lab_dhcp(cid):
    if len(cid) != 32 or any(c not in '0123456789abcdef' for c in cid):
        raise ValueError('Invalid physical probe identity')
    tag = hashlib.sha256(cid.encode()).hexdigest()
    mac = '02:51:' + ':'.join(tag[i:i + 2] for i in range(0, 8, 2))
    # A host reservation selects dnsmasq's broadcast tag, also for Windows
    # clients which still hold the early initramfs server's lease.
    return ("config dnsmasq\n option domainneeded '1'\n option authoritative '1'\n\n"
            "config dhcp 'lan'\n option interface 'lan'\n option start '2'\n option limit '1'\n"
            " option leasetime '10m'\n list dhcp_option '3'\n list dhcp_option '6'\n\n"
            "config host 'probe_host'\n option mac '" + mac + "'\n option ip '192.168.29.2'\n"
            " option broadcast '1'\n")


def stage_ram_modules(root, kernel):
    """Use the actual full boot cohort, never the display-only probe subset."""
    root, kernel = Path(root), Path(kernel)
    release = (kernel / 'kernel.release').read_text().strip()
    if '-k1-' not in release:
        raise ValueError('Wrong RAM kernel profile')
    requested = (kernel / 'module-order.txt').read_text().split()
    if not {'panfrost.ko', 'sprd_modem_loader.ko', 'spipe.ko',
            'wcn_bsp.ko', 'sprd_wlan_combo.ko'}.issubset(requested):
        raise ValueError('Incomplete RAM hardware module order')
    ordered = runpy.run_path(str(TOP / 'devices/common/bringup-probe.py'))['module_order'](
        kernel / 'modules', requested, release)
    for name, text in [('ram-module-order', '\n'.join(ordered) + '\n'),
                       ('ram-kernel-release', release + '\n')]:
        (root / 'etc/e5' / name).write_text(text)
    helper = root / 'usr/libexec/e5-ram-modules'
    helper.write_text((TOP / 'devices/common/probe-ram-modules.sh').read_text())
    helper.chmod(0o755)
    service = root / 'etc/init.d/e5-ram-modules'
    service.write_text('#!/bin/sh /etc/rc.common\nSTART=07\nstart() {\n /usr/libexec/e5-ram-modules\n}\n')
    service.chmod(0o755)
    link = root / 'etc/rc.d/S07e5-ram-modules'
    if link.is_symlink():
        link.unlink()
    link.symlink_to('../init.d/e5-ram-modules')


def prepare(root, kernel, device_files, output, backup):
    root, kernel, device_files, output = map(lambda p: Path(p).resolve(), (root, kernel, device_files, output))
    if os.name == 'nt' or root == Path('/') or (root / 'etc/e5/device-profile').read_text().strip() != 'k1':
        raise ValueError('Use a Linux offline K1 root')
    nv_store = root / 'opt/e5/nv'
    if nv_store.is_symlink() or (nv_store.exists() and
                                (not nv_store.is_dir() or any(nv_store.iterdir()))):
        raise ValueError('RAM trial requires a clean root without stored NV')
    firmware = json.loads((device_files / 'manifest.json').read_text())
    backup = Path(backup)
    stock = runpy.run_path(str(TOP / 'devices/common/stock-backup.py'))['verify'](backup, 'k1')
    if firmware['android_slot'] != stock['android_slot'] or firmware['cid_sha256'] != stock['files']['cid.txt']['sha256']:
        raise ValueError('Firmware and backup physical identity/slots differ')
    archive = device_files / 'device-files.tar'
    if firmware['profile'] != 'k1' or hashlib.sha256(archive.read_bytes()).hexdigest() != firmware['archive_sha256']:
        raise ValueError('Wrong or damaged K1-owned firmware')
    with tarfile.open(archive) as tar:
        tar.extractall(root, filter='tar')
    runpy.run_path(str(TOP / 'upstream/stage-root-modules.py'))['stage'](kernel, root)

    def put(name, text, mode=0o644):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')
        path.chmod(mode)

    # Lab-only controls: preserve the already-selected Android fallback and
    # prevent external mounts, outbound mobile data and host gateway changes.
    for link in (root / 'etc/rc.d').iterdir():
        if link.name.endswith(('e5-boot-ok', 'fstab', 'e5-apn-auto')):
            link.unlink()
    put('etc/config/fstab', "config global\n option anon_mount '0'\n option auto_mount '0'\n option auto_swap '0'\n")
    put('etc/config/network', "config interface 'loopback'\n option device 'lo'\n option proto 'static'\n option ipaddr '127.0.0.1'\n option netmask '255.0.0.0'\n\nconfig device\n option name 'br-lan'\n option type 'bridge'\n list ports 'usb0'\n option bridge_empty '1'\n\nconfig interface 'lan'\n option device 'br-lan'\n option proto 'static'\n option ipaddr '192.168.29.1'\n option netmask '255.255.255.0'\n")
    cid = (backup / 'cid.txt').read_text().strip()
    put('etc/config/dhcp', lab_dhcp(cid))
    put('etc/e5/configured.network', '')
    put('etc/e5/ram-probe', 'k1\n')
    put('etc/e5/image-version', 'k1-ram-bringup1\n')
    stage_ram_modules(root, kernel)
    # Let the first boot finish normally rather than stop in charger-only UI.
    charge = root / 'etc/config/e5-charge'
    charge.write_text(charge.read_text().replace("option power_on '0'", "option power_on '1'"))
    password = subprocess.check_output(['openssl', 'passwd', '-6', '-salt', 'K1RAMprobe', 'root'], text=True).strip()
    shadow = root / 'etc/shadow'
    lines = [line.split(':') for line in shadow.read_text().splitlines()]
    for line in lines:
        if line[0] == 'root':
            line[1] = password
    put('etc/shadow', '\n'.join(':'.join(line) for line in lines) + '\n', 0o600)
    subprocess.run(['aarch64-linux-gnu-gcc', '-static', '-Os', '-s', '-Wall', '-Wextra', '-Werror',
                    str(TOP / 'devices/common/probe-watchdog.c'), '-o', str(root / 'usr/libexec/e5-ram-watchdog')], check=True)
    persist = '''#!/bin/sh
persist() {
    [ "$(cat /sys/class/block/mmcblk0/device/cid)" = "CID" ] || return 1
    { echo 'K1-RAM-BEGIN'; cat /proc/uptime; uname -r; cat /proc/mounts;
      echo '--- services'; ps; echo '--- nv isolation'; cat /run/t551m-nv-source-sha256.json;
      echo '--- system'; logread | tail -c 1000000; echo '--- kernel'; dmesg | tail -c 1500000;
      echo 'K1-RAM-END'; } > /tmp/k1-persist.txt 2>&1
    head -c 65536 /dev/zero >> /tmp/k1-persist.txt
    dd if=/tmp/k1-persist.txt of=/dev/block/by-name/boot_a bs=4096 seek=14336 conv=notrunc 2>/dev/null
    sync
}
[ "${1:-}" != --once ] || { persist; exit $?; }
i=0
while [ "$i" -lt 21 ]; do sleep 20; persist; i=$((i + 1)); done
reboot -f
'''
    persist = persist.replace('mmcblk0', stock['disk']).replace('boot_a', 'boot_' + stock['linux_slot'])
    put('usr/libexec/e5-ram-probe', persist.replace('CID', cid), 0o755)
    put('etc/init.d/e5-ram-probe', '#!/bin/sh /etc/rc.common\nSTART=08\nUSE_PROCD=1\nstart_service() {\n procd_open_instance\n procd_set_param command /usr/libexec/e5-ram-probe\n procd_close_instance\n}\n', 0o755)
    service_link = root / 'etc/rc.d/S08e5-ram-probe'
    if not service_link.is_symlink():
        service_link.symlink_to('../init.d/e5-ram-probe')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, 'w:gz', compresslevel=1) as tar:
        for path in sorted(root.iterdir()):
            tar.add(path, arcname=path.name)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.sha256').write_text(digest + '  ram-root.tar.gz\n')
    print('K1 RAM root: ' + str(output.stat().st_size) + ' compressed bytes; SHA256 ' + digest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'kernel', 'device-files', 'out', 'backup'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    prepare(args.root, args.kernel, args.device_files, args.out, args.backup)
