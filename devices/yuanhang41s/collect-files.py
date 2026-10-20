#!/usr/bin/env python3
"""Collect this board's runtime/firmware, never NV or another model's data."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import runpy

TOP = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    adb = ['adb', '-s', args.serial]

    def read(command):
        return subprocess.check_output(adb + ['exec-out', 'su', '-c',
            'stty -onlcr 2>/dev/null; ' + command], timeout=180)

    if read('getprop ro.product.device').strip() != b'P720S11':
        raise ValueError('Expected rooted P720S11 / ZTE 7546N')
    slot = read('getprop ro.boot.slot_suffix').decode().strip()
    if slot not in ('_a', '_b'):
        raise ValueError('Unknown Android firmware slot')
    args.out.mkdir(parents=True, exist_ok=False)
    raw = args.out / 'raw'
    fw = args.out / 'lib/firmware'
    raw.mkdir()
    (fw / 'sprd').mkdir(parents=True)
    sources = [
        '/odm/firmware/wcnmodem.bin', '/odm/firmware/gnssmodem.bin',
        '/odm/firmware/bt_configure_pskey.ini', '/odm/firmware/bt_configure_rf.ini',
        '/mnt/vendor/btmac.txt', '/vendor/firmware/aw883xx_acf.bin',
        '/vendor/firmware/awinic_params.bin',
        '/vendor/firmware/sitronix_firmware_huaying.dump',
        '/odm/etc/audio_route.xml', '/odm/etc/audio_route_ab.xml',
        '/odm/etc/audio_pcm.xml', '/odm/etc/audio_config.xml',
    ]
    sources += ['/odm/firmware/' + name for name in
                read('ls /odm/firmware').decode().split()
                if name.startswith('wifi_board_config') and name.endswith('.ini')]
    sources += ['/odm/etc/audio_params/sprd/' + name for name in
                ('audio_structure.xml', 'codec.xml', 'dsp_vbc.xml', 'cvs.xml', 'audio_pga.xml')]
    manifest = {'android_slot': slot[1:], 'files': {}}
    for source in sources:
        content = read('cat ' + source)
        if not content:
            raise ValueError('Empty firmware: ' + source)
        name = Path(source).name
        (raw / name).write_bytes(content)
        manifest['files'][source] = hashlib.sha256(content).hexdigest()
        if name not in ('btmac.txt', 'audio_structure.xml', 'dsp_vbc.xml', 'cvs.xml'):
            (fw / name).write_bytes(content)
    # Existing audio userspace uses a canonical filename ending in _a. The
    # content must come from the running Android slot, which is B on this unit.
    source = '/dev/block/by-name/l_agdsp' + slot
    dsp = read('dd if=' + source + ' bs=1M count=6 2>/dev/null')
    if len(dsp) != 6291456 or not dsp.startswith(b'SharkL5_AUD'):
        raise ValueError('Unexpected active-slot AGDSP image')
    (fw / 'l_agdsp_a.img').write_bytes(dsp)
    manifest['files'][source] = hashlib.sha256(dsp).hexdigest()
    subprocess.run([sys.executable, str(TOP / 'tools/sprd-bt-config.py'),
        str(raw / 'bt_configure_pskey.ini'), str(raw / 'bt_configure_rf.ini'),
        str(raw / 'btmac.txt'), str(fw / 'sprd')], check=True)
    subprocess.run([sys.executable, str(TOP / 'tools/vbc-profile/vbc-profile.py'),
        str(raw / 'dsp_vbc.xml'), str(raw / 'audio_structure.xml'),
        str(raw / 'cvs.xml'), '-o', str(fw)], check=True)
    # Preserve a trace of the actual slot independently of the canonical DSP
    # filename. The 41S staging path consumes this before starting vendor CP.
    (fw / 'android-firmware-slot').write_text(slot[1:] + '\n')
    ucm, gain = runpy.run_path(str(Path(__file__).with_name('audio-routes.py')))['generate'](
        raw / 'audio_route.xml', raw / 'audio_pga.xml')
    (fw / 'yuanhang41s-HiFi.conf').write_text(ucm, encoding='utf-8', newline='\n')
    (fw / 'yuanhang41s-media-max').write_text(str(gain) + '\n', newline='\n')
    archive = args.out / 'firmware.tar'
    with tarfile.open(archive, 'w', format=tarfile.GNU_FORMAT) as stream:
        stream.add(args.out / 'lib', arcname='lib')
        for name, target in (
                ('lib/firmware/sitronix/st7123.bin', '../sitronix_firmware_huaying.dump'),):
            item = tarfile.TarInfo(name)
            item.type = tarfile.SYMTYPE
            item.linkname = target
            item.mode = 0o777
            stream.addfile(item)
    remote = '/data/local/tmp/yuanhang41s-files'
    read('mkdir -p ' + remote + '; chown 2000:2000 ' + remote +
         '; chmod 700 ' + remote + '; restorecon -R ' + remote)
    for source, name in [(archive, 'firmware.tar'),
            (TOP / 'openwrt/bundle/collect-device-files.sh', 'collect.sh')]:
        subprocess.run(adb + ['push', str(source), remote + '/' + name], check=True)
        digest = read('sha256sum ' + remote + '/' + name).decode().split()[0]
        if digest != hashlib.sha256(source.read_bytes()).hexdigest():
            raise ValueError('Upload verification failed')
    result = read('E5_COLLECT_WORK=' + remote + '/work sh ' + remote +
        '/collect.sh ' + remote + '/device-files.tar ' + remote + '/firmware.tar')
    print(result.decode(errors='replace'))
    if b'E5-COLLECT-OK' not in result:
        raise ValueError('Android runtime collection failed')
    data = read('cat ' + remote + '/device-files.tar')
    if hashlib.sha256(data).hexdigest() != read(
            'sha256sum ' + remote + '/device-files.tar').decode().split()[0]:
        raise ValueError('Runtime archive verification failed')
    (args.out / 'device-files.tar').write_bytes(data)
    manifest['device_files_sha256'] = hashlib.sha256(data).hexdigest()
    (args.out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('Collected this unit\'s files; active Android slot:', slot[1:])


if __name__ == '__main__':
    main()
