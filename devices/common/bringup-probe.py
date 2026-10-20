"""Build a bounded RAM-only hardware probe through the shared boot packer."""
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import runpy
import struct
import subprocess
import sys

TOP = Path(__file__).resolve().parents[2]


def module_order(directory, requested, release):
    available = {}
    for path in Path(directory).glob('*.ko'):
        key = path.stem.replace('-', '_')
        if key in available:
            raise ValueError('Duplicate module: ' + key)
        available[key] = path
    resolved, loaded, visiting = [], set(), set()

    def resolve(name):
        key = Path(name).stem.replace('-', '_')
        if key in loaded:
            return
        if key in visiting or key not in available:
            raise ValueError('Missing or cyclic module: ' + name)
        path = available[key]
        data = path.read_bytes()
        versions = re.findall(rb'\x00vermagic=([^\x00]*)', data)
        depends = re.findall(rb'\x00depends=([^\x00]*)', data)
        if (len(versions) != 1 or not versions[0].split() or
                versions[0].split()[0].decode() != release or len(depends) != 1):
            raise ValueError('Module cohort/metadata mismatch: ' + name)
        visiting.add(key)
        for dependency in depends[0].decode().split(','):
            if dependency:
                resolve(dependency)
        visiting.remove(key)
        loaded.add(key)
        resolved.append(path.name)

    for name in requested:
        resolve(name)
    return resolved


def probe_init(cid, disk, android, linux, stay, label, address):
    if not re.fullmatch('[a-f0-9]{32}', cid) or not re.fullmatch(r'mmcblk\d+', disk):
        raise ValueError('Invalid probe eMMC identity')
    if android not in ('a', 'b') or linux != ('b' if android == 'a' else 'a'):
        raise ValueError('Invalid probe slots')
    if not 15 <= stay <= 300 or not re.fullmatch('[A-Z0-9]{1,12}', label):
        raise ValueError('Invalid bounded probe settings')
    network = ipaddress.IPv4Interface(address + '/24')
    if not network.ip.is_private or network.ip.packed[-1] != 1:
        raise ValueError('Probe address must be a private /24 host ending in .1')
    text = (TOP / 'upstream/init-bringup').read_text(encoding='utf-8')
    text = text.replace('PARTNAME=boot_b', 'PARTNAME=boot_' + linux)
    text = text.replace('/sys/class/block/mmcblk0p*', '/sys/class/block/' + disk + 'p*')
    text = text.replace('persist() {', 'persist() {\n    [ "${probe_verified:-0}" = 1 ] || return')
    prep = '''
# No Android mounts, partitioning, modem startup or NV access in this probe.
probe_verified=0
i=0
while [ ! -r /sys/class/block/DISK/device/cid ] && [ "$i" -lt 15 ]; do
    sleep 1; i=$((i + 1))
done
if [ "$(cat /sys/class/block/DISK/device/cid 2>/dev/null)" != "CID" ]; then
    log "Probe device identity mismatch; no partition writes"
    reboot -f; exit 1
fi
misc=
for u in /sys/class/block/DISKp*/uevent; do
    grep -qx PARTNAME=misc "$u" && misc=/dev/$(basename "$(dirname "$u")")
done
[ -b "$misc" ] || { log "Missing misc; aborting"; reboot -f; exit 1; }
dd if=/etc/misc-bc-slot-a.bin of="$misc" bs=1 seek=2048 conv=notrunc &&
    dd if="$misc" of=/tmp/probe-bc.bin bs=1 skip=2048 count=32 &&
    cmp /tmp/probe-bc.bin /etc/misc-bc-slot-a.bin || {
        log "Android fallback readback failed"; reboot -f; exit 1;
    }
sync
probe_verified=1
log "Android slot ANDROID fallback restored and verified; Linux slot LINUX probe"
(sleep WATCHDOG; persist; sync; reboot -f) &
echo 1 > /proc/sys/kernel/panic_on_oops
echo 1 > /proc/sys/kernel/softlockup_panic
while read -r module; do
    [ -n "$module" ] || continue
    log "loading $module"
    insmod "/linux-modules/$module"
    log "loaded $module rc=$?"
done < /etc/module-order
for p in /sys/class/power_supply/*; do
    log "power ${p##*/}: $(tr '\\n' ' ' < "$p/uevent")"
done
log "input: $(cat /proc/bus/input/devices)"
log "drm: $(ls /sys/class/drm /sys/class/backlight 2>&1)"
[ ! -x /e5-overlay/bin/display-test ] || /e5-overlay/bin/display-test > /tmp/display-test.log 2>&1 &
persist
'''
    for key, value in {'DISK': disk, 'CID': cid, 'ANDROID': android,
                       'LINUX': linux, 'WATCHDOG': str(stay + 90)}.items():
        prep = prep.replace(key, value)
    anchor = 'log "userspace up: $(uname -a)"'
    if text.count(anchor) != 1:
        raise ValueError('Shared probe init changed')
    text = text.replace(anchor, anchor + prep)
    text = text.replace('stay=${E5_STAY:-180}', 'stay=' + str(stay))
    tag = hashlib.sha256(cid.encode()).hexdigest()
    text = text.replace('E5LINUX', label + 'PROBE' + tag[:8])
    text = text.replace('E5 Linux USB Ethernet (mainline bring-up)', label + ' hardware probe')
    mac = ':'.join(tag[i:i + 2] for i in range(0, 8, 2))
    text = text.replace('02:50:00:00:e5:01', '02:50:' + mac).replace('02:50:00:00:e5:02', '02:51:' + mac)
    text = text.replace('192.168.9.1', str(network.ip)).replace('192.168.9.2', str(network.ip + 1))
    return text


def make(backup, kernel, busybox, out, profile, requested, stay=90, display_test=None, ram_handoff=False):
    backup, kernel, busybox, out = map(Path, (backup, kernel, busybox, out))
    stock = runpy.run_path(str(TOP / 'devices/common/stock-backup.py'))['verify'](backup, profile)
    release = (kernel / 'kernel.release').read_text().strip()
    if '-' + profile + '-' not in release:
        raise ValueError('Kernel belongs to another profile: ' + release)
    executable = busybox.read_bytes()
    if len(executable) < 64 or executable[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', executable, 18)[0] != 183:
        raise ValueError('Expected static AArch64 BusyBox')
    offset = struct.unpack_from('<Q', executable, 32)[0]
    size, count = struct.unpack_from('<HH', executable, 54)
    if size < 56 or not count or offset + size * count > len(executable):
        raise ValueError('Invalid BusyBox ELF headers')
    if any(struct.unpack_from('<I', executable, offset + i * size)[0] == 3 for i in range(count)):
        raise ValueError('Probe BusyBox must be static')
    android, linux = stock['android_slot'], stock['linux_slot']
    cid = (backup / 'cid.txt').read_text().strip()
    text = probe_init(cid, stock['disk'], android, linux, stay, profile.upper(), '192.168.29.1')
    if ram_handoff:
        helper = (TOP / 'devices/common/probe-ram-root.sh').read_text().replace('PROBE_PROFILE', profile)
        text = text.replace('    while [ "$t" -lt "$stay" ]', helper + '\n    while [ "$t" -lt "$stay" ]')
        text = text.replace('        sleep 30; t=$((t + 30))',
                            '        ram_handoff || :\n        sleep 2; t=$((t + 2))\n        [ $((t % 30)) -eq 0 ] || continue')
    modules = module_order(kernel / 'modules', requested, release)
    out.parent.mkdir(parents=True, exist_ok=True)
    init, order = out.with_suffix('.init'), out.with_suffix('.modules')
    init.write_text(text, encoding='utf-8', newline='\n')
    order.write_text(''.join(name + '\n' for name in modules))
    command = [sys.executable, str(TOP / 'boot/build-boot-image.py'),
               '--stock-boot', str(backup / ('boot_' + android + '.img')),
               '--misc-head', str(backup / 'misc-head.bin'), '--android-slot', android,
               '--kernel', str(kernel / 'Image.lk'), '--modules', str(kernel / 'modules'),
               '--module-order', str(order), '--init', str(init), '--busybox', str(busybox),
               '--cmdline', 'panic=10 panic_on_oops=1', '--out', str(out)]
    if display_test:
        target = out.with_suffix('.overlay') / 'bin/display-test'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(display_test).read_bytes())
        target.chmod(0o755)
        command += ['--overlay', str(target.parents[1])]
    subprocess.run(command, check=True)
    manifest = json.loads(out.with_suffix('.json').read_text())
    manifest.update(profile=profile, probe=True, stay_seconds=stay, ram_handoff=ram_handoff,
                    android_slot=android, linux_slot=linux, address='192.168.29.1',
                    cid_sha256=stock['files']['cid.txt']['sha256'],
                    stock_boot_sha256=stock['files']['boot_' + linux + '.img']['sha256'],
                    kernel_release=release, init_sha256=hashlib.sha256(init.read_bytes()).hexdigest(),
                    busybox_sha256=hashlib.sha256(executable).hexdigest())
    out.with_suffix('.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
