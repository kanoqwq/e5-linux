"""Real OTA and full-image migration of the staged screen's network loading fix."""
import hashlib
import importlib
import io
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

TOP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOP / 'installer'))
from backend.ota_engine import Engine, Patch, canonical, descriptor
from backend.ota_package import compare

BOOT = b'unchanged screen-only fixture boot'
CODE = 'usr/share/e5-infoscreen'
source = runpy.run_path(str(TOP / 'devices/common/infoscreen/stage.py'))['sources']


def put(root, name, content):
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    target.chmod(0o644)


def release(root, version):
    return SimpleNamespace(
        family='e5', source=str(root), packages={},
        meta=dict(profile='e5', image_version=version, kernel_release='6.18.54-screen-test', openwrt='25.12.5'),
        entries={p.relative_to(root).as_posix(): descriptor(p) for p in root.rglob('*')},
        boot_name='boot.img', boot_digest=hashlib.sha256(BOOT).hexdigest(),
        open=lambda name: io.BytesIO(BOOT))


def delta(before, after, version, target_version, destination, folder):
    runtime = importlib.import_module(compare.__module__.rsplit('.', 1)[0] + '.ota_runtime')
    # No kernel/runtime changes: exercise the real screen comparison/transaction only.
    with patch.object(runtime, 'build', return_value=({}, None, {})):
        plan, _ = compare(release(before, version), release(after, target_version))
    assert plan['can_export'], plan['blockers']
    paths = {op['path'] for op in plan['manifest']['operations']}
    assert CODE + '/www/app.js' in paths
    archive = folder / (target_version + '.ota')
    with zipfile.ZipFile(archive, 'w') as stream:
        stream.writestr('manifest.json', canonical(plan['manifest']))
        added = set()
        for op in plan['manifest']['operations']:
            desc = op['after']
            if desc and desc['kind'] == 'file' and desc['sha256'] not in added:
                stream.write(after / op['path'], 'blobs/' + desc['sha256'])
                added.add(desc['sha256'])
    with Patch(archive) as package:
        assert Engine(destination).apply(package)['status'] == 'complete'
    return paths


with tempfile.TemporaryDirectory(prefix='e5-network-delivery-') as tmp:
    root = Path(tmp)
    old, new, live, next_root = (root / name for name in ('old', 'new', 'live', 'next'))
    previous = {}
    for name in ('app.js', 'settings.uc'):
        path = TOP / 'devices/common/infoscreen/base' / name
        previous[path] = subprocess.check_output(
            ['git', '-C', str(TOP), 'show', 'HEAD:' + path.relative_to(TOP).as_posix()]).decode()
    read_text = Path.read_text

    def baseline(path, *args, **kwargs):
        return previous[path] if path in previous else read_text(path, *args, **kwargs)

    with patch.object(Path, 'read_text', baseline):
        old_sources = source()
    for target, sources, version in ((old, old_sources, 'e5-screen-old'), (new, source(), 'e5-screen-fixed')):
        for name, content in sources.items():
            put(target, CODE + '/' + name, content.encode())
        put(target, 'etc/config/e5-infoscreen', b'user screen config\n')
        put(target, 'etc/e5-infoscreen/wallpapers/desktop.png', b'user wallpaper')
        put(target, 'etc/e5-infoscreen/plugins/custom/main.js', b'user plugin')
        put(target, 'etc/config/wireless', b'user AP credentials\n')
        put(target, 'etc/e5/release.json', json.dumps(release(target, version).meta).encode())
    shutil.copytree(old, live)
    paths = delta(old, new, 'e5-screen-old', 'e5-screen-fixed', live, root)
    assert CODE + '/settings.uc' in paths
    assert (live / CODE / 'www/app.js').read_bytes() == (new / CODE / 'www/app.js').read_bytes()
    assert (live / CODE / 'settings.uc').read_bytes() == (new / CODE / 'settings.uc').read_bytes()

    shutil.copytree(new, next_root)
    js = next_root / CODE / 'www/app.js'
    js.write_bytes(js.read_bytes() + b'\n/* subsequent screen update fixture */\n')
    put(next_root, 'etc/e5/release.json', json.dumps(release(next_root, 'e5-screen-next').meta).encode())
    delta(new, next_root, 'e5-screen-fixed', 'e5-screen-next', live, root)
    assert (live / CODE / 'www/app.js').read_bytes() == js.read_bytes()

    # The ordinary image path preserves user settings, never old /usr screen code.
    script = (TOP / 'openwrt/device-install-image.sh').read_text()
    start = script.index('keep_config() {')
    keep = script[start:script.index('\n}\n', start) + 3]
    keep = keep.replace('/tmp/e5-img.', str(root / 'migration.'))
    put(old, 'etc/config/e5-infoscreen', b'changed user screen config\n')
    subprocess.run(['sh', '-ec', keep + '\nkeep_config "$1" "$2"', 'test', str(old), str(next_root)], check=True)
    assert (next_root / CODE / 'www/app.js').read_bytes() == js.read_bytes()
    assert (next_root / 'etc/config/e5-infoscreen').read_bytes() == b'changed user screen config\n'
    for name in ('etc/config/wireless', 'etc/e5-infoscreen/wallpapers/desktop.png',
                 'etc/e5-infoscreen/plugins/custom/main.js'):
        assert (next_root / name).read_bytes() == (old / name).read_bytes()
        assert (live / name).read_bytes() == (old / name).read_bytes()

print('PASS: shared staging, actual first/second OTA apply, ordinary-image migration, user configuration/wallpaper/plugins preserved')
