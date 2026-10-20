"""Verified bring-up boot backups using the installer's staged binary transport."""
from contextlib import ExitStack
import gzip
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import time
import zlib

TOP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOP / 'installer'))
from backend.binary_io import pull_binary
from backend.state import DeviceLock, app_directory, atomic_json, sha

# Explicit recovery-only allowlist; never include modem NV or calibration.
PARTITIONS = tuple(name + '_' + slot for name in (
    'boot', 'vendor_boot', 'init_boot', 'dtb', 'dtbo', 'vbmeta',
    'sml', 'teecfg', 'trustos', 'uboot') for slot in ('a', 'b'))


def collect(adb, destination, profile, lock_base=None):
    if not adb.serial:
        raise ValueError('Select one explicit ADB device')
    if adb.root_status() != 'ready':
        raise ValueError('Allow shell root access before collecting')
    properties = {}
    for name in ('ro.product.model', 'ro.product.device', 'ro.boot.slot_suffix',
                 'ro.boot.verifiedbootstate', 'ro.build.display.id', 'ro.build.fingerprint'):
        properties[name] = adb.shell('getprop ' + name).strip()
    if (properties['ro.product.model'] not in profile['android_models'] or
            properties['ro.product.device'] != profile['android_device']):
        raise ValueError('Device model/board differs from the requested profile')
    slot = properties['ro.boot.slot_suffix']
    if slot not in ('_a', '_b'):
        raise ValueError('Current Android slot is unavailable')
    disk = adb.shell('readlink -f /dev/block/by-name/boot_' + slot[1:], root=True).strip()
    match = re.fullmatch(r'/dev/block/(mmcblk\d+)p\d+', disk)
    if not match:
        raise ValueError('Cannot establish physical eMMC identity')
    cid = adb.shell('cat /sys/class/block/' + match[1] + '/device/cid', root=True).strip()
    if not re.fullmatch('[a-f0-9]{32}', cid):
        raise ValueError('Invalid eMMC identity')
    destination = Path(destination).resolve()
    with ExitStack() as locks:
        for identity in sorted({adb.serial, cid}):
            locks.enter_context(DeviceLock(lock_base or app_directory(), identity))
        destination.mkdir(parents=True, exist_ok=False, mode=0o700)
        manifest = {'version': 1, 'profile': profile['profile'], 'android_slot': slot[1:],
                    'linux_slot': 'b' if slot == '_a' else 'a', 'complete': False,
                    'disk': match[1],
                    'device_tag': hashlib.sha256(cid.encode()).hexdigest()[:12], 'files': {}}

        def record(name):
            path = destination / name
            manifest['files'][name] = {'bytes': path.stat().st_size, 'sha256': sha(path)}
            atomic_json(destination / 'manifest.json', manifest)

        def save(name, data):
            (destination / name).write_bytes(data)
            record(name)

        save('properties.txt', ''.join('[' + name + ']: [' + value + ']\n'
                                      for name, value in properties.items()).encode())
        save('cid.txt', (cid + '\n').encode())
        for name, command in (('live.dtb', 'cat /sys/firmware/fdt'),
                              ('config.gz', 'cat /proc/config.gz'),
                              ('misc-head.bin', 'dd if=/dev/block/by-name/misc bs=4096 count=1 2>/dev/null')):
            data = adb.read(command)
            if name == 'live.dtb' and (len(data) < 40 or data[:4] != b'\xd0\x0d\xfe\xed' or
                                       struct.unpack_from('>I', data, 4)[0] != len(data)):
                raise ValueError('Invalid live device tree')
            if name == 'config.gz':
                gzip.decompress(data)  # Validate the full CRC, not just the magic.
            if name == 'misc-head.bin' and len(data) != 4096:
                raise ValueError('Incomplete misc recovery header')
            if name == 'misc-head.bin':
                control = data[2048:2080]
                if (control[4:8] != b'BCAB' or control[8] != 1 or control[9] & 7 != 2 or
                        zlib.crc32(control[:28]) != struct.unpack_from('<I', control, 28)[0]):
                    raise ValueError('Invalid Android boot control')
            save(name, data)
        for part in PARTITIONS:
            source = '/dev/block/by-name/' + part
            exists = adb.shell('test -b ' + source + '; echo $?', root=True).strip() == '0'
            if not exists:
                if part.startswith('boot_'):
                    raise ValueError('Required boot partition missing: ' + part)
                continue
            size = int(adb.shell('blockdev --getsize64 ' + source, root=True).strip())
            if not 0 < size <= 256 << 20:
                raise ValueError('Unexpected boot recovery partition size: ' + part)
            started = time.monotonic()
            print('Backing up ' + part + ': ' + str(size) + ' bytes', flush=True)
            pull_binary(adb.call, adb.shell, 'dd if=' + source + ' bs=4M 2>/dev/null',
                        destination / (part + '.img'), expected_size=size)
            record(part + '.img')
            print('Verified ' + part + ' (' + str(round(time.monotonic() - started, 1)) + ' s)', flush=True)
        # A reconnect or slot change cannot silently bind this backup to a new unit.
        if (adb.shell('getprop ro.boot.slot_suffix').strip() != slot or
                adb.shell('cat /sys/class/block/' + match[1] + '/device/cid', root=True).strip() != cid):
            raise ValueError('Device identity/slot changed during backup')
        manifest['complete'] = True
        atomic_json(destination / 'manifest.json', manifest)
        return manifest


def verify(directory, profile):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('version') != 1 or manifest.get('profile') != profile or not manifest.get('complete'):
        raise ValueError('Incomplete or incompatible stock backup')
    android = manifest.get('android_slot')
    if android not in ('a', 'b') or manifest.get('linux_slot') != ('b' if android == 'a' else 'a'):
        raise ValueError('Invalid backup slot roles')
    if not re.fullmatch(r'mmcblk\d+', manifest.get('disk', '')):
        raise ValueError('Missing physical eMMC name in backup')
    required = {'boot_a.img', 'boot_b.img', 'misc-head.bin', 'cid.txt', 'properties.txt', 'live.dtb', 'config.gz'}
    if not required <= manifest.get('files', {}).keys():
        raise ValueError('Required recovery files are missing')
    for name, info in manifest['files'].items():
        if ('/' in name or '\\' in name or name in ('.', '..') or
                not (directory / name).is_file() or (directory / name).is_symlink()):
            raise ValueError('Unsafe or missing backup file')
        path = directory / name
        if path.stat().st_size != info['bytes'] or sha(path) != info['sha256']:
            raise ValueError('Stock backup changed: ' + name)
    return manifest
