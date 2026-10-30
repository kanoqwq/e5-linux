"""Content identity permits moved directories and compatible repair releases."""
import json
from pathlib import Path
import re

from .state import sha


def safe_file(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('Missing or unsafe package file / 安装包文件缺失或路径不安全: ' + name)
    return path


def locate_package(directory):
    """Normalize a user-selected root, APP or payload folder, never search CWD."""
    selected = Path(directory).expanduser().resolve()
    if not selected.is_dir():
        raise ValueError('Select a fully extracted package folder, not a ZIP / 请选择完整解压的安装包目录，不能选择 ZIP: ' + str(selected))

    def has_layout(root):
        return (root/'desktop/manifest.json').is_file() or (root/'files/device-profile.json').is_file()

    # An incomplete root is still this package: report its missing file rather
    # than choosing another release nested inside it.
    def is_package(root):
        return has_layout(root) or (root/'OpenWrt-Installer.py').is_file()

    if is_package(selected):
        return selected
    app = next((p for p in (selected, *selected.parents) if p.suffix.lower() == '.app'), None)
    if app:
        selected = app.parent
    elif selected.name in ('files', 'desktop'):
        selected = selected.parent
    if is_package(selected):
        return selected
    candidates = [p for p in selected.iterdir() if p.is_dir() and has_layout(p)]
    if len(candidates) == 1:
        return candidates[0].resolve()
    if candidates:
        raise ValueError('Multiple OpenWrt packages found; select the exact release folder / 此目录包含多个安装包，请明确选择其中一个安装包目录')
    raise ValueError('No extracted OpenWrt package found; select the folder containing OpenWrt-Installer.py and files or desktop / 未找到解压后的安装包，请选择包含 OpenWrt-Installer.py 和 files 或 desktop 的目录: ' + str(selected))


def e5_profile(root):
    data = json.loads(safe_file(Path(root),'files/device-profile.json').read_text(encoding='utf-8'))
    profile, models = data.get('profile'), data.get('android_models',[])
    if (not isinstance(profile,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',profile) or
            not isinstance(models,list) or not all(isinstance(model,str) and model.strip() for model in models)):
        raise ValueError('Invalid package profile metadata / 安装包机型元数据不完整或格式错误')
    data.setdefault('android_models',[])
    return data


def inspect_package(directory):
    root = locate_package(directory)
    mu = root / 'desktop/manifest.json'
    if mu.is_file() and (root/'files/device-profile.json').is_file():
        raise ValueError('Ambiguous E5/MU300 package layouts / 安装包同时包含两种机型系列，无法确定安装流程')
    if mu.is_file():
        manifest = json.loads(mu.read_text(encoding='utf-8'))
        checks = {**manifest['magisk'], **manifest['desktop']}
        adapter, profile, layout, kernel = 'mu300', 'mu300-family', 'mu300root-container', manifest['kernel']
        mapping = manifest.get('magisk_layout', {})
        names = [mapping.get(n, n) for n in ('boot-linux.img', 'mu300-openwrt.tar.gz', 'busybox',
                                             'rootfs-critical.sha256', 'rootfs.requirements')]
        names += [n for n in checks if n.startswith('desktop/files/boot/repair/')]
    else:
        profile_data = e5_profile(root)
        adapter, profile, layout, kernel = 'e5', profile_data['profile'], 'e5root-ext4', 'e5-boot-v2'
        checks = {}
        for line in safe_file(root, 'files/SHA256SUMS').read_text().splitlines():
            digest, name = line.split(None, 1)
            checks[name.strip().removeprefix('*')] = digest
        names = ['files/boot.img', 'files/openwrt.ext4.gz', 'files/ext4-tools.tar']
    for name, expected in checks.items():
        if not re.fullmatch('[a-f0-9]{64}', expected) or sha(safe_file(root, name)) != expected:
            raise ValueError('Package checksum mismatch / 安装包校验失败: ' + name)
    payloads = {name: sha(safe_file(root, name)) for name in names}
    if not (root/'OpenWrt-Installer.py').is_file():
        raise ValueError('Missing compatible Python entry / 缺少兼容安装入口')
    package = {'protocol': 1, 'adapter': adapter, 'profile': profile, 'layout': layout, 'kernel': kernel,
               'payloads': payloads, 'path': str(root), 'name': root.name}
    release_file = root/('desktop/release.json' if adapter == 'mu300' else 'files/RELEASE.json')
    if release_file.is_file():
        release_data = json.loads(release_file.read_text(encoding='utf-8'))
        package['release_version'] = release_data['image_version']
    return package
