"""Shared screen transport and charger wake must ship on every screen profile."""
from pathlib import Path
import runpy
import tempfile

repo = Path(__file__).resolve().parents[2]
screen = runpy.run_path(str(repo/'devices/t551m/infoscreen/stage.py'))['stage']
with tempfile.TemporaryDirectory() as tmp:
    for profile in ('t551m', 'e5', 'm8', 't512n'):
        root = Path(tmp)/profile
        (root/'etc/e5').mkdir(parents=True)
        (root/'etc/e5/device-profile').write_text(profile+'\n')
        (root/'etc/rc.d').mkdir()
        (root/'etc/uci-defaults').mkdir()
        screen(root)
        index = (root/'usr/share/e5-infoscreen/www/index.html').read_text()
        assert index.count('src="transport.js"') == 1
        assert index.index('transport.js') < index.index('calls.js')
        for name in ('e5_call_events.py', 'e5_screen_rpc.py', 'e5_power_events.py'):
            assert (root/'usr/lib/e5'/name).read_bytes() == (repo/'openwrt/overlay/usr/lib/e5'/name).read_bytes().replace(b'\r\n',b'\n')
        for name in ('calls.js', 'transport.js'):
            assert (root/'usr/share/e5-infoscreen/www'/name).read_bytes() == (repo/'devices/t551m/infoscreen'/name).read_bytes().replace(b'\r\n',b'\n')
        assert b'T551MTransport' in (root/'usr/share/e5-infoscreen/www/sdk/e5.js').read_bytes()
        assert b'T551MCalls.subscribe' in (root/'etc/e5-infoscreen/plugins/phone/phone.js').read_bytes()
        assert (root/'etc/e5/device-profile').read_text().strip() == profile
print('E5/T551M/M8/T512N shared event/read transport and charger wake PASS')
