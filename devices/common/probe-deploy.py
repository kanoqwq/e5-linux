"""Read-only probe planning and explicit, journalled inactive-slot deployment."""
from contextlib import ExitStack
import json
from pathlib import Path
import re
import runpy
import secrets
import shlex
import sys

TOP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOP / 'installer'))
from backend.state import DeviceLock, app_directory, atomic_json, sha


def preflight(adb, backup, image, profile, previous_image=None):
    backup, image = Path(backup), Path(image)
    stock = runpy.run_path(str(TOP / 'devices/common/stock-backup.py'))['verify'](backup, profile['profile'])
    info = json.loads(image.with_suffix('.json').read_text(encoding='utf-8'))
    if (info.get('profile') != profile['profile'] or info.get('probe') is not True or
            info.get('android_slot') != stock['android_slot'] or info.get('linux_slot') != stock['linux_slot'] or
            info.get('cid_sha256') != stock['files']['cid.txt']['sha256']):
        raise ValueError('Probe and stock backup identity/slots differ')
    if sha(image) != info['sha256'] or sha(image.with_suffix('.init')) != info['init_sha256']:
        raise ValueError('Probe image/init changed')
    previous = None
    if previous_image:
        previous_image = Path(previous_image)
        previous = json.loads(previous_image.with_suffix('.json').read_text())
        if (previous.get('probe') is not True or
                any(previous.get(key) != info.get(key) for key in ('profile', 'cid_sha256', 'android_slot', 'linux_slot')) or
                sha(previous_image) != previous['sha256'] or
                sha(previous_image.with_suffix('.init')) != previous['init_sha256'] or
                not re.fullmatch('[a-f0-9]{64}', previous.get('sha256_head56m', ''))):
            raise ValueError('Previous probe identity or integrity differs')
    if adb.root_status() != 'ready' or not adb.serial:
        raise ValueError('One selected, rooted Android device is required')
    for key, allowed in (('ro.product.model', profile['android_models']),
                         ('ro.product.device', [profile['android_device']]),
                         ('ro.boot.slot_suffix', ['_' + stock['android_slot']]),
                         ('ro.boot.verifiedbootstate', ['orange'])):
        if adb.shell('getprop ' + key).strip() not in allowed:
            raise ValueError('Device precondition differs: ' + key)
    cid = (backup / 'cid.txt').read_text().strip()
    if adb.shell('cat /sys/class/block/' + stock['disk'] + '/device/cid', root=True).strip() != cid:
        raise ValueError('Different physical device')
    power = {key: adb.shell('cat /sys/class/power_supply/battery/' + key, root=True).strip()
             for key in ('capacity', 'voltage_now', 'status')}
    if int(power['capacity']) < 15 or int(power['voltage_now']) < 3500000 or power['status'] not in ('Charging', 'Full'):
        raise ValueError('Keep external power connected and charge before the probe')
    android, linux = stock['android_slot'], stock['linux_slot']
    boot_paths = {}
    for slot in (android, linux):
        path = adb.shell('readlink -f /dev/block/by-name/boot_' + slot, root=True).strip()
        if not re.fullmatch('/dev/block/' + stock['disk'] + r'p\d+', path):
            raise ValueError('Boot alias is outside the verified eMMC')
        label = adb.shell('cat /sys/class/block/' + Path(path).name + '/uevent', root=True)
        if 'PARTNAME=boot_' + slot not in label.splitlines():
            raise ValueError('Boot alias points to another partition')
        boot_paths[slot] = path
    if boot_paths[android] == boot_paths[linux]:
        raise ValueError('Android and Linux boot aliases overlap')
    current = {}
    for part in ['boot_' + android, 'boot_' + linux] + [name + '_' + slot
            for name in ('uboot', 'sml', 'teecfg', 'trustos', 'vbmeta') for slot in ('a', 'b')]:
        if part + '.img' not in stock['files']:
            raise ValueError('Missing boot-chain recovery image: ' + part)
        command = 'sha256sum /dev/block/by-name/' + part
        allowed = {stock['files'][part + '.img']['sha256']}
        if previous and part == 'boot_' + linux:
            command = 'dd if=/dev/block/by-name/' + part + ' bs=1M count=56 2>/dev/null | sha256sum'
            allowed = {previous['sha256_head56m']}
        elif previous and part.endswith('_' + linux):
            allowed.add(stock['files'][part[:-1] + android + '.img']['sha256'])
        value = adb.shell(command, root=True, timeout=120).split()[0]
        if not re.fullmatch('[a-f0-9]{64}', value) or value not in allowed:
            raise ValueError('Stock partition changed; preserve and review: ' + part)
        current[part] = value
    target_size = int(adb.shell('blockdev --getsize64 /dev/block/by-name/boot_' + linux, root=True).strip())
    if target_size != image.stat().st_size:
        raise ValueError('Probe/target boot sizes differ')
    original = (backup / 'misc-head.bin').read_bytes()
    live = adb.read('dd if=/dev/block/by-name/misc bs=4096 count=1 2>/dev/null')
    expected_control = bytes.fromhex(previous['misc_slot_a_hex']) if previous else original[2048:2080]
    if live[2048:2080] != expected_control:
        raise ValueError('Live boot control changed since backup')
    boot = runpy.run_path(str(TOP / 'boot/build-boot-image.py'))
    fallback, trial = boot['bootloader_control'](original, android)
    if fallback.hex() != info['misc_slot_a_hex'] or trial.hex() != info['misc_slot_b_trial_hex']:
        raise ValueError('Probe boot controls differ from the verified stock record')
    chain = [name for name in ('uboot', 'sml', 'teecfg', 'trustos', 'vbmeta')
             if current[name + '_' + android] != current[name + '_' + linux]]
    return {'profile': profile['profile'], 'android_slot': android, 'linux_slot': linux,
            'chain': chain, 'boot': 'boot_' + linux, 'boot_sha256': info['sha256'],
            'original_hex': live[2048:2080].hex(), 'trial_hex': trial.hex(),
            'fallback_hex': fallback.hex(), 'cid': cid, 'backup': str(backup.resolve()),
            'disk': stock['disk'], 'boot_path': boot_paths[linux],
            'image': str(image.resolve()), 'status': 'checked'}


def execute(adb, backup, image, busybox, profile, reboot=False, lock_base=None, previous_image=None):
    image, busybox = Path(image), Path(busybox)
    record_file = image.with_suffix('.deployment.json')
    cid = (Path(backup) / 'cid.txt').read_text().strip()
    with ExitStack() as locks:
        for identity in sorted({adb.serial, cid}):
            locks.enter_context(DeviceLock(lock_base or app_directory(), identity))
        if record_file.exists():
            raise ValueError('A deployment record already exists; review it before any retry: ' + str(record_file))
        plan = preflight(adb, backup, image, profile, previous_image=previous_image)
        info = json.loads(image.with_suffix('.json').read_text())
        if sha(busybox) != info['busybox_sha256']:
            raise ValueError('Use the exact verified static BusyBox from the probe build')
        atomic_json(record_file, plan)

        def checkpoint(stage):
            plan['status'] = stage
            atomic_json(record_file, plan)

        remote = '/data/local/tmp/openwrt-probe-' + secrets.token_hex(16)
        plan['staging'] = remote
        atomic_json(record_file, plan)
        adb.shell('umask 077; mkdir ' + remote)
        trial = image.with_suffix('.probe-trial.bin')
        original = image.with_suffix('.probe-original.bin')
        trial.write_bytes(bytes.fromhex(plan['trial_hex']))
        original.write_bytes(bytes.fromhex(plan['original_hex']))
        sources = [(image, 'boot.img'), (trial, 'trial.bin'), (original, 'original.bin'),
                   (busybox, 'busybox'), (TOP / 'tools/android-bootchain.sh', 'bootchain.sh')]
        for local, name in sources:
            # Android scripts must keep LF even from a CRLF Windows checkout.
            if name == 'bootchain.sh':
                local = image.with_suffix('.bootchain.sh')
                local.write_bytes((TOP / 'tools/android-bootchain.sh').read_bytes().replace(b'\r\n', b'\n'))
            adb.call('push', str(local), remote + '/' + name, timeout=120)
            actual = adb.shell('sha256sum ' + remote + '/' + name, root=True).split()[0]
            if actual != sha(local):
                raise ValueError('Upload checksum differs: ' + name)
        adb.shell('chown -R 0:0 ' + remote + '; chmod 700 ' + remote + ' ' + remote + '/busybox', root=True)
        shell = 'ASH_STANDALONE=1 ' + remote + '/busybox sh '
        checked = adb.shell(shell + remote + '/bootchain.sh check', root=True, timeout=120)
        expected = 'BOOTCHAIN-PLAN ' + plan['android_slot'] + ' ' + plan['linux_slot']
        lines = [line for line in checked.splitlines() if line.split()[:3] == expected.split()]
        if len(lines) != 1 or lines[0].split()[3:] != plan['chain']:
            raise ValueError('Boot chain changed after preflight')
        def ensure_binding():
            if (adb.shell('getprop ro.boot.slot_suffix').strip() != '_' + plan['android_slot'] or
                    adb.shell('cat /sys/class/block/' + plan['disk'] + '/device/cid', root=True).strip() != cid or
                    adb.shell('readlink -f /dev/block/by-name/' + plan['boot'], root=True).strip() != plan['boot_path']):
                raise ValueError('Device, Android slot or boot mapping changed')
        ensure_binding()
        checkpoint('syncing_chain')
        output = adb.shell(shell + remote + '/bootchain.sh sync', root=True, timeout=600)
        if not any(line.startswith('BOOTCHAIN-OK ' + plan['android_slot'] + ' ' + plan['linux_slot'])
                   for line in output.splitlines()):
            raise ValueError('Shared boot-chain synchronization did not complete; keep its backups')
        checkpoint('chain_verified')
        ensure_binding()
        checkpoint('writing_boot')
        target = '/dev/block/by-name/' + plan['boot']
        adb.shell(shell + '-c ' + shlex.quote(
            'set -e; dd if=' + remote + '/boot.img of=' + target + ' bs=4M conv=fsync,notrunc; sync'), root=True, timeout=120)
        if adb.shell('sha256sum ' + target, root=True, timeout=120).split()[0] != plan['boot_sha256']:
            raise ValueError('Inactive boot readback failed; Android is still selected')
        ensure_binding()
        checkpoint('arming_trial')
        def write_control(name):
            adb.shell(shell + '-c ' + shlex.quote(
                'set -e; dd if=' + remote + '/' + name + ' of=/dev/block/by-name/misc bs=1 seek=2048 conv=fsync,notrunc; sync'),
                root=True, timeout=60)
        write_control('trial.bin')
        def read_control():
            return adb.read('dd if=/dev/block/by-name/misc bs=4096 count=1 2>/dev/null')[2048:2080].hex()
        if read_control() != plan['trial_hex']:
            write_control('original.bin')
            if read_control() != plan['original_hex']:
                raise ValueError('Boot control restore failed; do not reboot, retain all backups')
            checkpoint('trial_rejected_android_restored')
            raise ValueError('Trial readback failed; original Android boot control restored')
        checkpoint('armed')
        if reboot:
            checkpoint('reboot_requested')
            try:
                adb.call('reboot', timeout=20)
            except ConnectionError:
                # Disconnection after the explicit reboot is expected.
                pass
        return plan
