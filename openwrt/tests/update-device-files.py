#!/usr/bin/env python3
"""Exercise both updater collectors with a board's own firmware and symlinks."""
from pathlib import Path
import re
import os
import subprocess
import tarfile
import tempfile

TOP = Path(__file__).resolve().parents[2]

# Exercise the real settings-archive command with both host and target tar.
# The resulting archive must be safe even for a pre-fix initramfs restorer.
stage_source = (TOP/'openwrt/device-stage-part-update.sh').read_text()
start = stage_source.index('# BEGIN image-owned-settings-filter')
archive_command = stage_source[start:stage_source.index('# END image-owned-settings-filter', start)]
with tempfile.TemporaryDirectory(prefix='e5-settings-filter-') as folder:
    root = Path(folder)
    preserved = {'etc/config/wireless': b'user password',
                 'etc/e5/install.conf': b'own device settings',
                 'etc/e5-infoscreen/wallpapers/desktop.png': b'user wallpaper',
                 'etc/e5-infoscreen/plugins/user-app/main.js': b'user app'}
    excluded = {'etc/e5-infoscreen/plugins/phone/phone.js': b'old phone',
                'etc/e5-infoscreen/plugins/esim/esim.css': b'old eSIM'}
    excluded.update({'etc/e5/'+name: b'old version' for name in
                     ('image-version','image-form','build-time','release.json',
                      'build.json','feature-port.json','device-profile')})
    for name, data in {**preserved, **excluded}.items():
        path = root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    runners = [['sh','-c']]
    if os.environ.get('E5_SETTINGS_TARGET_TAR')=='1':
        runners.append(['docker','run','--rm','--pull=never','--network','none',
            '--platform','linux/arm64','-v',str(root)+':'+str(root),'-w',str(root),
            'mu300-openwrt-base:25.12.5','/bin/sh','-c'])
    for runner in runners:
        output = root/'settings.tar'
        code = archive_command.replace('/.e5-update/settings.tar', str(output))
        harness = 'set -eu\nsettings="etc/config etc/e5 etc/e5-infoscreen"\n'+code
        subprocess.run([*runner,harness],cwd=root,check=True)
        with tarfile.open(output) as stream:
            members = {m.name.removeprefix('./') for m in stream}
            for name,data in preserved.items():
                assert name in members and stream.extractfile(name).read()==data,(runner,name)
            assert not members.intersection(excluded),(runner,members.intersection(excluded))
files = {
    'lib/firmware/wcnmodem.bin': b'own CP',
    'lib/firmware/aw883xx_acf.bin': b'own amp calibration',
    'lib/firmware/codec.xml': b'<codec>own analog call gains</codec>',
    'lib/firmware/awinic_params.bin': b'own amp params',
    'lib/firmware/sitronix_firmware_huaying.dump': b'own touch firmware',
    'lib/firmware/yuanhang41s-HiFi.conf': b'own audio routing',
    'lib/firmware/yuanhang41s-media-max': b'25\n',
    'lib/firmware/android-firmware-slot': b'b\n',
    'lib/firmware/sprd/pskey.bin': b'own bluetooth',
    'opt/e5/android/vendor/bin/modem_control': b'own loader',
}
with tempfile.TemporaryDirectory(prefix='e5-update-collect-') as folder:
    base = Path(folder)
    for name, content in files.items():
        p = base / name; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(content)
    touch = base / 'lib/firmware/sitronix/st7123.bin'
    touch.parent.mkdir(); touch.symlink_to('../sitronix_firmware_huaying.dump')
    (base / 'lib/firmware/not-device-owned.bin').write_bytes(b'do not capture unrelated files')
    for source, variable in [('device-install-image.sh','f'),('device-stage-part-update.sh','name')]:
        text = (TOP / 'openwrt' / source).read_text()
        loop = re.search(r'for ' + variable + r' in lib/firmware/wcnmodem\.bin .*?; do\n.*?\bdone', text, re.S)
        assert loop, source
        code = 'cd "$1"\nfirmware=\n' + loop[0]
        if variable == 'name':
            code += '\nprintf "%s\\n" $firmware\n'
        result = subprocess.run(['sh','-ec',code,'test',str(base)],capture_output=True,text=True,check=True)
        names = result.stdout.splitlines()
        archive = base / (source + '.tar')
        subprocess.run(['tar','-cf',str(archive),*names],cwd=base,check=True)
        with tarfile.open(archive) as stream:
            members = {m.name.removeprefix('./'): m for m in stream}
            for name, content in files.items():
                assert stream.extractfile(members[name]).read() == content, (source,name)
            assert members['lib/firmware/sitronix/st7123.bin'].issym(), source
            assert members['lib/firmware/sitronix/st7123.bin'].linkname == '../sitronix_firmware_huaying.dump'
            assert 'lib/firmware/not-device-owned.bin' not in members, source
        print('PASS:', source, 'preserves own modem, audio, touch and Bluetooth files')
