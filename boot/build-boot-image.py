#!/usr/bin/env python3
"""Build an e5-linux boot image for the Rongyue E5 (Unisoc UMS9621, ums9158_1h10).

The E5 bootloader (LK) takes the *generic* ramdisk from boot.img and only falls
back to init_boot when boot.img's ramdisk_size is zero. Confirmed in the stock
bootloader log (uboot_log partition):

    bootimage: generic ramdisk size is 360840
    bootimage: generic ramdisk offset is 0x2c98000
    ...
    bootimage: generic ramdisk size is zero, maybe ramdisk in init_boot image side!
    init_bootimage: generic ramdisk size is 1907183

So the Linux kernel and the Linux initramfs both go into slot b's boot image and
init_boot_b / vendor_boot_b stay stock (LK also AVB-verifies init_boot).

The image reuses the stock boot image header, vbmeta blob and AVB footer byte for
byte; only kernel_size, ramdisk_size, the command line and the payload change.
AVB is not enforced on this unlocked device (the Magisk-patched boot_a boots with
a non-matching digest), but keeping the original descriptor avoids the
"invalid vbmeta header" failures seen when regenerating it.

Example:
  boot/build-boot-image.py \
    --stock-boot dumps/boot_b.img --misc-head dumps/misc-head.bin \
    --kernel out_linux/arch/arm64/boot/Image --modules out_linux-modules \
    --busybox work/busybox --out boot-linux-slotb.img
"""
import argparse
import hashlib
import json
import os
import stat
import struct
import subprocess
import sys
import zlib
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
PAGE = 4096
MISC_BC_OFFSET = 0x800

# e5-linux reserves the top of boot_b for a persistent init log written by
# boot/init (survives a cold power cycle, unlike pstore). 56 MiB of the 64 MiB
# partition; the image must end before it.
PERSIST_LOG_OFFSET = 56 << 20

BOOT_CMDLINE = (
    b'console=tty0 console=ttyGS0,115200 loglevel=7 e5.part_v=2 '
    b'selinux=0 androidboot.selinux=permissive enforcing=0 '
    # CONFIG_PANIC_ON_OOPS=y in this kernel, and the E5 has a pre-existing
    # vsprintf/uevent oops about 25 s into every boot, so panic=10 alone turns
    # that into a boot loop back into Android.  sysctl.kernel.panic_on_oops=0 is
    # handled by the sysctl code during early init and is what makes the boot
    # survive it; every image that has booted this device carried it.
    # softlockup_panic=1 was dropped at the same time: a hung task has to show up
    # in the log, not take the machine down.
    b'panic=10 sysctl.kernel.panic_on_oops=0 '
    # No 'fw_devlink=permissive' here on purpose.  The E5's USB controller
    # (64a00000.usb) lists the AW322xx charger (i2c:2-006a, which declares the
    # "vddvbus" regulator musb wants in host mode) among its devicetree
    # suppliers, and while 2-006a has no driver fw_devlink blocks musb before its
    # probe function is even entered:
    #
    #   platform 64a00000.usb: DEFERDBG: supplier 2-006a not ready
    #   probe of 64a00000.usb returned -517      (/sys/class/udc stays empty)
    #
    # Relaxing fw_devlink globally does unblock it, but it also lets every other
    # driver probe out of order and the kernel then dies before the initramfs
    # writes its first log line.  The charger driver exists in this tree
    # (drivers/power/supply/aw32257_charger.c, CONFIG_CHARGER_AW32257=m) and
    # registers a power supply named "aw322xx_charger" for compatible
    # "awinic,aw322xx_chg" -- loading it makes 2-006a probe, which is the actual
    # fix.  See boot/module-order.extra.
    # register ramoops from the command line so console/pmsg survive a boot that
    # dies before the DT-based device_initcall runs (artifacts_e5 diagnostics)
    b'ramoops.mem_address=0xfff80000 ramoops.mem_size=0x40000 '
    b'ramoops.record_size=0x8000 ramoops.console_size=0x8000 ramoops.pmsg_size=0x8000'
)


def cpio_record(name, data, mode, ino, rdev=(0, 0)):
    nb = name.encode() + b'\0'
    fields = [ino, mode, 0, 0, 1, 0, len(data), 0, 0, rdev[0], rdev[1], len(nb), 0]
    h = b'070701' + b''.join(('%08x' % v).encode() for v in fields)
    x = h + nb
    x += b'\0' * ((-len(x)) % 4)
    x += data
    x += b'\0' * ((-len(data)) % 4)
    return x


def slot_info(prio, tries, successful):
    return (prio & 0xf) | ((tries & 0x7) << 4) | ((successful & 1) << 7)


def initramfs_archive(files, directories):
    directories = set(directories)
    # The kernel's newc extractor does not create missing parent directories.
    # In particular, losing etc/e5/linux-slot would send persistent logs to
    # Android's boot partition when Android occupies slot B.
    for name in list(files) + list(directories):
        directories.update(str(p) for p in PurePosixPath(name).parents if str(p) != '.')
    cpio = bytearray()
    ino = 1
    for directory in sorted(directories, key=lambda x: (x.count('/'), x)):
        cpio += cpio_record(directory, b'', stat.S_IFDIR | 0o755, ino)
        ino += 1
    for name, (data, mode) in files.items():
        cpio += cpio_record(name, data, mode, ino)
        ino += 1
    cpio += cpio_record('dev/console', b'', stat.S_IFCHR | 0o600, ino, (5, 1))
    cpio += cpio_record('TRAILER!!!', b'', 0, ino + 1)
    return bytes(cpio)


def bootloader_control(misc_head, android_slot=None):
    """Return (slot_a_block, slot_b_trial_block) from the live bootloader_control.

    Same 32-byte AOSP structure as mu300-linux: slot_suffix[4], magic 'BCAB',
    version, nb_slot, recovery_tries, merge_status, slot_info[4], ..., crc32.
    slot_info bit layout: priority (4) | tries_remaining (3) | successful_boot (1).
    """
    bc = misc_head[MISC_BC_OFFSET:MISC_BC_OFFSET + 32]
    if bc[4:8] != b'BCAB':
        sys.exit('misc head has no bootloader_control magic at 0x800')
    if zlib.crc32(bc[:28]) != struct.unpack('<I', bc[28:])[0]:
        sys.exit('misc bootloader_control CRC mismatch')

    def with_slots(suffix, a, b):
        x = bytearray(bc)
        x[0:4] = suffix
        x[12] = a
        x[14] = b
        x[28:32] = struct.pack('<I', zlib.crc32(bytes(x[:28])))
        return bytes(x)

    if android_slot is None:
        android_slot = {b'_a': 'a', b'_b': 'b'}.get(bc[:2])
    if android_slot not in ('a', 'b'):
        raise ValueError('Android slot must be a or b')
    index = 0 if android_slot == 'a' else 1
    infos = [bc[12], bc[14]]
    priority = infos[index] & 0xf
    if priority < 2 or not infos[index] & 0x80:
        raise ValueError('Selected Android slot is not successful/bootable')
    infos[index] = slot_info(priority, 1, 1)
    # The other boot partition is replaced by Linux in either layout. Its old
    # Android success flag must not make an unconfirmed Linux boot bootable.
    infos[1 - index] = 0
    android = with_slots(b'_' + android_slot.encode() + b'\0\0', *infos)
    infos[index] = slot_info((priority - 1) & 0xf, 1, 1)
    infos[1 - index] = slot_info(0xf, 2, 0)
    linux_slot = 'b' if android_slot == 'a' else 'a'
    linux = with_slots(b'_' + linux_slot.encode() + b'\0\0', *infos)
    # The legacy filenames mean Android fallback/Linux trial; retaining them
    # preserves existing init/update protocols even when physical roles swap.
    return android, linux


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--stock-boot', required=True, type=Path,
                    help='stock boot image of the target slot (64 MiB, header v4, ramdisk_size 0)')
    ap.add_argument('--misc-head', required=True, type=Path, help='first 4 KiB of the misc partition')
    ap.add_argument('--android-slot', choices=('a', 'b'),
                    help='stock Android slot (default: derive from the stock misc suffix); Linux uses the opposite slot')
    ap.add_argument('--kernel', required=True, type=Path, help='arm64 Image built by kernel/build-linux.sh')
    ap.add_argument('--modules', type=Path, help='flat directory with the built .ko files')
    ap.add_argument('--module-order', type=Path, default=HERE / 'module-order.txt')
    ap.add_argument('--init', type=Path, default=HERE / 'init')
    ap.add_argument('--busybox', required=True, type=Path, help='static arm64 busybox')
    ap.add_argument('--overlay', type=Path,
                    help='rootfs overlay tree to embed in the initramfs as e5-overlay/ '
                         '(firmware, factory data, systemd units); boot/init copies it into '
                         'the initramfs root before the module pass and into the real root '
                         'before switch_root')
    ap.add_argument('--cmdline', default=BOOT_CMDLINE.decode())
    ap.add_argument('--f2fs-tools', type=Path,
                    default=Path(os.environ.get('E5_F2FS_TOOLS', str(HERE.parent / 'out/boot/f2fs-tools'))),
                    help='aarch64/musl bundle made by boot/prepare-f2fs-tools.py')
    ap.add_argument('--out', required=True, type=Path)
    a = ap.parse_args()

    base = a.stock_boot.read_bytes()
    if base[:8] != b'ANDROID!' or struct.unpack_from('<I', base, 40)[0] != 4:
        sys.exit('stock boot is not an Android boot image header v4')
    ks0, rs0 = struct.unpack_from('<II', base, 8)
    print('base: kernel_size=0x%x ramdisk_size=0x%x' % (ks0, rs0))
    slot_a_bc, slot_b_bc = bootloader_control(a.misc_head.read_bytes(), a.android_slot)
    print('misc: slot_a=%s slot_b_trial=%s' % (slot_a_bc.hex(), slot_b_bc.hex()))

    if 'e5.part_v=2' not in a.cmdline.split():
        a.cmdline = (a.cmdline + ' e5.part_v=2').strip()
    cmdline = a.cmdline.encode()
    if len(cmdline) >= 1536:
        sys.exit('command line too long for a boot header v4 (max 1535 bytes)')

    files = {
        'init': (a.init.read_bytes(), stat.S_IFREG | 0o755),
        'bin/busybox': (a.busybox.read_bytes(), stat.S_IFREG | 0o755),
        'bin/sh': (b'busybox', stat.S_IFLNK | 0o777),
        'etc/misc-bc-slot-a.bin': (slot_a_bc, stat.S_IFREG | 0o644),
        'etc/misc-bc-slot-b-trial.bin': (slot_b_bc, stat.S_IFREG | 0o644),
        'etc/e5/linux-slot': (slot_b_bc[1:2] + b'\n', stat.S_IFREG | 0o644),
        'etc/sd-registry.sh': ((HERE.parent / 'rootfs/overlay/opt/e5/e5-sd-registry').read_bytes(), stat.S_IFREG | 0o644),
        'etc/f2fs-repair.sh': ((HERE / 'f2fs-repair.sh').read_bytes(), stat.S_IFREG | 0o644),
    }
    dirs = {'bin', 'sbin', 'etc', 'proc', 'sys', 'dev', 'run', 'tmp', 'root', 'config', 'linux-modules'}
    if not (a.f2fs_tools / 'manifest.json').is_file():
        sys.exit('Missing early F2FS tools; run boot/prepare-f2fs-tools.py --root ARM64_OPENWRT_ROOT')
    repair_manifest = json.loads((a.f2fs_tools / 'manifest.json').read_text())
    repair_names = ('sbin/fsck.f2fs', 'sbin/resize.f2fs', 'lib/ld-musl-aarch64.so.1',
                    'lib/libf2fs.so.10', 'lib/libuuid.so.1', 'lib/libgcc_s.so.1',
                    'usr/bin/ucode', 'lib/libucode.so.20230711', 'lib/libjson-c.so.5',
                    'usr/lib/ucode/fs.so',
                    'usr/sbin/resize2fs', 'lib/libe2p.so.2', 'lib/libext2fs.so.2', 'lib/libcom_err.so.0')
    for name in repair_names:
        payload = (a.f2fs_tools / name).read_bytes()
        if hashlib.sha256(payload).hexdigest() != repair_manifest['sha256'].get(name):
            sys.exit('F2FS tool checksum mismatch: '+name)
        files['repair/'+name] = (payload, stat.S_IFREG | 0o755)
    files['repair/lib/libc.so'] = (b'ld-musl-aarch64.so.1', stat.S_IFLNK | 0o777)
    files['repair/manifest.json'] = ((a.f2fs_tools / 'manifest.json').read_bytes(), stat.S_IFREG | 0o644)
    dirs.update({'repair', 'repair/sbin', 'repair/lib', 'repair/usr', 'repair/usr/bin', 'repair/usr/lib',
                 'repair/usr/lib/ucode', 'repair/usr/sbin'})
    # The internal-partition installer (boot/install-part.sh) runs the tools
    # the way a normal system does, from the standard paths: ucode resolves
    # its modules at the compiled-in /usr/lib/ucode, and the loader finds
    # libucode/libc beside it, so no private library path is involved.
    for name in repair_names:
        files[name] = ((a.f2fs_tools / name).read_bytes(), stat.S_IFREG | 0o755)
    files['etc/install-part.sh'] = ((HERE / 'install-part.sh').read_bytes(), stat.S_IFREG | 0o644)
    files['etc/part-progress.sh'] = ((HERE / 'part-progress.sh').read_bytes(), stat.S_IFREG | 0o644)
    files['etc/boot-args.sh'] = ((HERE / 'boot-args.sh').read_bytes(), stat.S_IFREG | 0o644)
    files['etc/part-prepare.sh'] = ((HERE / 'part-prepare.sh').read_bytes(), stat.S_IFREG | 0o644)
    files['etc/part-update.sh'] = ((HERE / 'part-update.sh').read_bytes(), stat.S_IFREG | 0o644)
    files['usr/share/e5-gpt'] = ((HERE.parent / 'openwrt/overlay/usr/libexec/e5-gpt').read_bytes(),
                                 stat.S_IFREG | 0o755)
    dirs.update({'usr', 'usr/bin', 'usr/lib', 'usr/lib/ucode', 'usr/share', 'usr/sbin'})

    order = []
    if a.module_order.exists():
        order = a.module_order.read_text().split()
        files['etc/module-order'] = (a.module_order.read_bytes(), stat.S_IFREG | 0o644)
    for name in order:
        ko = a.modules / name if a.modules else None
        if ko is None or not ko.exists():
            sys.exit('missing module %s (looked in %s)' % (name, a.modules))
        files['linux-modules/' + name] = (ko.read_bytes(), stat.S_IFREG | 0o644)

    overlay_files = 0
    if a.overlay and a.overlay.is_dir():
        dirs.add('e5-overlay')
        for path in sorted(a.overlay.rglob('*')):
            if any(part.startswith('.') for part in path.relative_to(a.overlay).parts):
                continue
            name = 'e5-overlay/' + str(path.relative_to(a.overlay))
            if path.is_dir():
                dirs.add(name)
            elif path.is_file():
                # Never ship a file the session's user cannot read: the build
                # host's umask has nothing to do with what the device needs, and
                # a 0600 /etc/phosh/phoc.ini silently cost the e5 user its whole
                # session (phoc is started with an explicit -C and exits when it
                # cannot read the file).  Executables keep their bits; everything
                # else is at least a+r.
                mode = path.stat().st_mode & 0o777
                mode |= 0o055 if mode & 0o100 else 0o044
                files[name] = (path.read_bytes(), stat.S_IFREG | mode)
                overlay_files += 1
    print('overlay: %d files from %s' % (overlay_files, a.overlay))

    cpio = initramfs_archive(files, dirs)

    # LK decodes the generic ramdisk as an LZ4 *legacy* frame (magic 02 21 4c 18);
    # that is what the stock and Magisk boot images on this device use.
    ram = subprocess.run(['lz4', '-l', '-12', '-c'], input=bytes(cpio), capture_output=True, check=True).stdout
    assert ram[:4] == bytes.fromhex('02214c18'), 'not a legacy lz4 frame'
    print('initramfs: cpio %d bytes -> lz4 legacy %d bytes, %d modules'
          % (len(cpio), len(ram), len(order)))

    kern = a.kernel.read_bytes()
    hdr = bytearray(base[:PAGE])
    struct.pack_into('<I', hdr, 8, len(kern))
    struct.pack_into('<I', hdr, 12, len(ram))
    hdr[44:44 + 1536] = cmdline + b'\0' * (1536 - len(cmdline))
    struct.pack_into('<I', hdr, 1580, 0)  # signature_size
    body = bytes(hdr) + kern + b'\0' * ((-len(kern)) % PAGE) + ram
    body += b'\0' * ((-len(body)) % PAGE)
    original_size = len(body)

    avb_off, avb_size = struct.unpack_from('>QQ', base, len(base) - 44)
    if avb_off + avb_size > len(base) - 64:
        sys.exit('base image has no usable vbmeta blob (offset 0x%x size 0x%x)' % (avb_off, avb_size))
    body += base[avb_off:avb_off + avb_size]
    if len(body) > PERSIST_LOG_OFFSET:
        sys.exit('image data (%d bytes) overlaps the persistent log area at %d'
                 % (len(body), PERSIST_LOG_OFFSET))
    body += b'\0' * (len(base) - 64 - len(body))
    footer = bytearray(base[-64:])
    struct.pack_into('>QQQ', footer, 12, original_size, original_size, avb_size)
    image = body + bytes(footer)
    assert len(image) == len(base)

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_bytes(image)
    a.out.with_suffix('.misc-slot-b-trial.bin').write_bytes(slot_b_bc)
    manifest = {
        'image': a.out.name,
        'slot_roles_version': 1,
        'android_slot': slot_a_bc[1:2].decode(),
        'linux_slot': slot_b_bc[1:2].decode(),
        'sha256': hashlib.sha256(image).hexdigest(),
        'sha256_head56m': hashlib.sha256(image[:PERSIST_LOG_OFFSET]).hexdigest(),
        'kernel_sha256': hashlib.sha256(kern).hexdigest(),
        'kernel_size': len(kern),
        'ramdisk_size': len(ram),
        'modules': len(order),
        'cmdline': a.cmdline,
        'misc_slot_a_hex': slot_a_bc.hex(),
        'misc_slot_b_trial_hex': slot_b_bc.hex(),
        'overlay_files': overlay_files,
        'persist_log_offset': PERSIST_LOG_OFFSET,
        'f2fs_repair': {'policy': 'userdata-mount-failure-only', 'tf_boot': 'skip',
                        'tools': repair_manifest['sha256'],
                        'script_sha256': hashlib.sha256((HERE / 'f2fs-repair.sh').read_bytes()).hexdigest()},
        'part_install': {'trigger': 'e5linux/part-install.conf on userdata (flash.py --part)',
                         'script_sha256': hashlib.sha256((HERE / 'install-part.sh').read_bytes()).hexdigest()},
        'tf_update': {'version': 1, 'layout': 'single-root; PC installer; no GPT changes'},
        'part_update': {'version': 1, 'trigger': '/.e5-update/request on internal e5root or TF root; offline initramfs',
                        'script_sha256': hashlib.sha256((HERE / 'part-update.sh').read_bytes()).hexdigest()},
    }
    a.out.with_suffix('.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
