"""Bind one shared boot payload to this handset's actual Android/Linux slots.

The appended newc segment overrides only role metadata, not the kernel, module
cohort or firmware. No package payload is duplicated and no device is accessed
by this module.
"""
import hashlib
import stat
import struct
import zlib

PAGE = 4096
PERSIST = 56 << 20


def validate_control(data, android_slot=None):
    if (len(data) != 32 or data[4:8] != b'BCAB' or data[8] != 1 or data[9] & 7 != 2
            or struct.unpack_from('<I', data, 28)[0] != zlib.crc32(data[:28])):
        raise ValueError('invalid BCAB format/version/CRC')
    if android_slot is None:
        android_slot = {b'_a': 'a', b'_b': 'b'}.get(data[:2])
    if android_slot not in ('a', 'b'):
        raise ValueError('unknown Android slot')
    index = 12 if android_slot == 'a' else 14
    other = 14 if android_slot == 'a' else 12
    if not data[index] & 0x80 or data[index] & 15 < 2 or data[13] & 1 or data[15] & 1:
        raise ValueError('selected Android slot is not successful/bootable or has a verity error')
    if (data[other] & 15) > (data[index] & 15):
        raise ValueError('other slot has higher priority: finish or cancel pending Android OTA first')
    return android_slot


def role_files(data, android_slot):
    validate_control(data, android_slot)
    linux = 'b' if android_slot == 'a' else 'a'
    index, other = (12, 14) if android_slot == 'a' else (14, 12)

    def block(slot, android, target):
        result = bytearray(data)
        result[:4] = ('_' + slot).encode() + b'\0\0'
        result[index], result[other] = android, target
        struct.pack_into('<I', result, 28, zlib.crc32(result[:28]))
        return bytes(result)

    priority = data[index] & 15
    fallback = block(android_slot, 0x90 | priority, 0)
    trial = block(linux, 0x90 | (priority - 1), 0x2f)
    return {
        'etc/e5/android-slot': (android_slot + '\n').encode(),
        'etc/e5/linux-slot': (linux + '\n').encode(),
        'etc/e5/modem-slot': (android_slot + '\n').encode(),
        'lib/firmware/android-firmware-slot': (android_slot + '\n').encode(),
        # Historical names identify roles; suffix bytes identify physical slots.
        'etc/e5linux/misc-bc-slot-a.bin': fallback,
        'etc/e5linux/misc-bc-slot-b-trial.bin': trial,
    }


def cpio_record(name, data, mode, ino):
    name = name.encode() + b'\0'
    fields = [ino, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(name), 0]
    result = b'070701' + b''.join(('%08x' % n).encode() for n in fields) + name
    result += b'\0' * (-len(result) % 4)
    result += data
    return result + b'\0' * (-len(result) % 4)


def role_ramdisk(files):
    # Every parent precedes its child: the kernel does not create parents.
    dirs = {'etc', 'etc/e5', 'etc/e5linux'}
    result = b''.join(cpio_record(name, b'', stat.S_IFDIR | 0o755, i)
                      for i, name in enumerate(sorted(dirs, key=lambda n: (n.count('/'), n)), 1))
    for i, (name, data) in enumerate(files.items(), len(dirs) + 1):
        result += cpio_record(name, data, stat.S_IFREG | 0o644, i)
    result += cpio_record('TRAILER!!!', b'', 0, len(dirs) + len(files) + 1)
    # One literal-only LZ4 block: portable, tiny metadata, no host executable
    # or Python extension required by the standalone installer.
    length = len(result)
    block = bytes([min(length, 15) << 4])
    if length >= 15:
        extra = length - 15
        block += b'\xff' * (extra // 255) + bytes([extra % 255])
    block += result
    return bytes.fromhex('02214c18') + struct.pack('<I', len(block)) + block


def bind_boot(image, control, android_slot):
    files = role_files(control, android_slot)
    # Rootfs metadata goes into the firmware archive. Initramfs needs only the
    # role and two controls, not a modem-slot override in the early overlay.
    early = {name.replace('etc/e5linux/', 'etc/'): value for name, value in files.items()
             if name in ('etc/e5/linux-slot', 'etc/e5linux/misc-bc-slot-a.bin',
                         'etc/e5linux/misc-bc-slot-b-trial.bin')}
    return append_ramdisk(image, early)


def append_ramdisk(image, files):
    """Append a small initramfs overlay while retaining kernel, AVB and log area."""
    if (len(image) < PAGE or image[:8] != b'ANDROID!' or image[-64:-60] != b'AVBf'
            or struct.unpack_from('<I', image, 40)[0] != 4):
        raise ValueError('expected boot v4 with original AVB footer')
    kernel, size = struct.unpack_from('<II', image, 8)
    offset = PAGE + ((kernel + PAGE - 1) & -PAGE)
    avb_offset, avb_size = struct.unpack_from('>QQ', image, len(image) - 44)
    if not size or offset + size > avb_offset or avb_offset + avb_size > len(image) - 64:
        raise ValueError('invalid boot payload bounds')
    ramdisk = image[offset:offset + size] + role_ramdisk(files)
    header = bytearray(image[:PAGE])
    struct.pack_into('<I', header, 12, len(ramdisk))
    body = bytes(header) + image[PAGE:offset] + ramdisk
    body = body.ljust((len(body) + PAGE - 1) & -PAGE, b'\0')
    payload_end = len(body)
    body += image[avb_offset:avb_offset + avb_size]
    if len(body) > min(PERSIST, len(image) - 64):
        raise ValueError('slot metadata would overlap persistent log; nothing written')
    footer = bytearray(image[-64:])
    struct.pack_into('>QQQ', footer, 12, payload_end, payload_end, avb_size)
    result = body.ljust(len(image) - 64, b'\0') + bytes(footer)
    assert len(result) == len(image)
    return result


if __name__ == '__main__':
    import argparse, hashlib, json
    from pathlib import Path
    parser=argparse.ArgumentParser(description='Bind the shared boot payload to this Android slot')
    parser.add_argument('image',type=Path);parser.add_argument('manifest',type=Path)
    parser.add_argument('control',type=Path);parser.add_argument('android',choices=('a','b'))
    parser.add_argument('output',type=Path);parser.add_argument('root',type=Path)
    args=parser.parse_args();data=args.image.read_bytes();manifest=json.loads(args.manifest.read_text())
    if hashlib.sha256(data).hexdigest()!=manifest['sha256']:
        raise ValueError('Package boot checksum mismatch')
    if args.android=='b' and manifest.get('slot_roles_version',0)<1:
        raise ValueError('Package initramfs lacks dynamic-slot support')
    control=args.control.read_bytes()
    args.output.write_bytes(bind_boot(data,control,args.android))
    for name,value in role_files(control,args.android).items():
        path=args.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(value)
