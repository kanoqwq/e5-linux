#!/usr/bin/env python3
"""Exercise both updater collectors with a board's own firmware and symlinks."""
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile

TOP = Path(__file__).resolve().parents[2]
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
