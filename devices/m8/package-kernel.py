#!/usr/bin/env python3
"""Package a completed M8 kernel and its matching modules for staging."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

TOP = Path(__file__).resolve().parents[2]


def check_output_names(out, names):
    folded = {}
    collisions = []
    for name in names:
        prior = folded.setdefault(name.casefold(), name)
        if prior != name:
            collisions.append(prior + '/' + name)
    if not collisions:
        return
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.module-case-', dir=out) as directory:
        probe = Path(directory) / 'CaseSensitive'
        probe.write_bytes(b'')
        if (probe.parent / 'casesensitive').exists():
            raise ValueError('Kernel modules require a case-sensitive output filesystem: ' +
                             ', '.join(collisions) + '; use a Linux-native output directory')


def package(build, out, *, profile='m8'):
    if profile not in ('m8', 'k1'):
        raise ValueError('Unsupported UMS9620 kernel profile')
    release = (build / 'include/config/kernel.release').read_text().strip()
    if '-' + profile + '-' not in release:
        raise ValueError('Wrong kernel profile: ' + release)
    files = [str(Path(name).with_suffix('.ko')) for name in (build / 'modules.order').read_text().splitlines()]
    if len({Path(p).name for p in files}) != len(files):
        raise ValueError('Duplicate module names cannot be flattened')
    check_output_names(out, [Path(p).name for p in files])
    modules = out / 'modules'
    modules.mkdir(parents=True, exist_ok=True)
    expected = {Path(p).name for p in files}
    if any(p.name not in expected for p in modules.glob('*.ko')):
        raise ValueError('Stale modules in output; use a clean output directory')
    for rel in files:
        source = build / rel
        vermagic = subprocess.check_output(['modinfo', '-F', 'vermagic', str(source)], text=True).split()[0]
        if vermagic != release:
            raise ValueError('Module release mismatch: ' + rel)
        target = modules / source.name
        shutil.copyfile(source, target)
        subprocess.run(['llvm-strip', '--strip-debug', str(target)], check=True)
    for source, dest in [('arch/arm64/boot/Image', 'Image'), ('.config', 'config'),
                         ('System.map', 'System.map'), ('modules.builtin', 'modules.builtin'),
                         ('modules.builtin.modinfo', 'modules.builtin.modinfo'),
                         ('include/config/kernel.release', 'kernel.release')]:
        shutil.copyfile(build / source, out / dest)
    with tarfile.open(out / 'modules.tar', 'w') as archive:
        for rel in files:
            archive.add(modules / Path(rel).name,
                        arcname='lib/modules/' + release + '/kernel/' + rel)
        archive.add(build / 'modules.order', arcname='lib/modules/' + release + '/modules.order')
        for name in ('modules.builtin', 'modules.builtin.modinfo'):
            archive.add(out / name, arcname='lib/modules/' + release + '/' + name)
    # Preserve the canonical archive for the shared build verifier. Create
    # OpenWrt/audio aliases only when installing into the offline root.
    (out / 'module-links.sh').write_text('#!/bin/sh\nset -eu\n'
        'cd "${1:?offline root required}/lib/modules/' + release + '"\nmkdir -p audio\n' +
        ''.join('ln -sfn kernel/' + rel + ' ' + Path(rel).name + '\n'
                'ln -sfn ../kernel/' + rel + ' audio/' + Path(rel).name + '\n' for rel in files))
    subprocess.run(['python3', str(TOP / 'upstream/wrap-image.py'), str(out / 'Image'), str(out / 'Image.lk')], check=True)
    order = (TOP / 'upstream/module-order.txt').read_text().replace('aw32257_charger.ko', 'bq2560x-charger.ko')
    # Load CPUFreq from the real root at S13, after validating the saved
    # voltage profile and arming crash recovery. Early zero-offset loading
    # consumes the firmware's one-shot DVFS_INIT before the profile is read.
    (out / 'module-order.txt').write_text(order)
    for name in (out / 'module-order.txt').read_text().split():
        if name not in expected:
            raise ValueError('Missing early module: ' + name)
    (out / 'SHA256SUMS').write_text(''.join(hashlib.sha256((out / n).read_bytes()).hexdigest() + '  ' + n + '\n'
                                         for n in ['Image', 'Image.lk', 'config']))
    subprocess.run(['python3', str(TOP / 'upstream/check-container-support.py'), str(out), '--record'], check=True)
    print('Packaged', release, 'with', len(files), 'matching modules')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    package(args.build, args.out)
