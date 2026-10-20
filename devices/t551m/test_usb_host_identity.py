"""Exercise configured gadget MAC discovery with real configfs-shaped links.

Run on a Linux host/device with ucode; no hardware or network settings change.
"""
from pathlib import Path
import json
import subprocess
import tempfile


def main():
    api = (Path(__file__).with_name('infoscreen') / 'base/api.uc').read_text()
    fragment = api[api.index('let usbHostMacs = null;'):api.index('function clients() {')]
    with tempfile.TemporaryDirectory(prefix='usb-identity-') as directory:
        root = Path(directory)
        config = root / 'phone/configs/c.1'
        config.mkdir(parents=True)
        functions = root / 'phone/functions'
        for name, mac in [('ncm.usb0', '02:51:3D:D0:7A:00\0\n'),
                          ('rndis.usb0', '02:50:00:00:e5:02\n')]:
            path = functions / name
            path.mkdir(parents=True)
            (path / 'host_addr').write_text(mac)
        link = config / 'f1'
        link.symlink_to(functions / 'ncm.usb0', target_is_directory=True)
        script = root / 'test.uc'
        script.write_text("import {glob, readfile} from 'fs';\n" +
                          fragment.replace('/sys/kernel/config/usb_gadget', str(root)) +
                          "print(sprintf('%J', usb_host_macs()));\n")

        def scan():
            return json.loads(subprocess.check_output(['ucode', str(script)]))

        assert scan() == {'02:51:3d:d0:7a:00': True}
        link.unlink()
        link.symlink_to(functions / 'rndis.usb0', target_is_directory=True)
        assert scan() == {'02:50:00:00:e5:02': True}
        (functions / 'rndis.usb0/host_addr').write_text('not a valid MAC\n')
        assert scan() == {}
        link.unlink()
        assert scan() == {}, 'Disconnected/unconfigured function retained a MAC'
    print('PASS: board MAC, configfs NUL, active mode only, mode change and disconnect')


if __name__ == '__main__':
    main()
