#!/usr/bin/env python3
"""Export a portable Diff Studio baseline without extracting a filesystem."""
import argparse
import base64
import collections
from concurrent.futures import CancelledError, FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import ExitStack
import ctypes
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tarfile
import threading
import time
from types import SimpleNamespace
import zipfile

if not getattr(sys, 'frozen', False):
    sys.path.insert(0,str(Path(__file__).resolve().parent))
from backend.ota_package import Release
from backend.ota_engine import digest, package_versions, safe_name
from backend.release_index import snapshot, write_index, SUFFIX

# Version-1 readers already include the target boot when this does not match.
# Zero is an explicit "not provided" marker, never a claimed image checksum.
UNKNOWN_BOOT = '0' * 64


def complete_directory(path):
    return (path/'files/device-profile.json').is_file() or (path/'desktop/manifest.json').is_file()


def directory_inputs(path):
    """Batch a collection, treating each unpacked complete release as one input."""
    inputs = []
    for current, directories, files in os.walk(path, followlinks=False):
        current = Path(current)
        if complete_directory(current):
            inputs.append(current)
            directories[:] = []
            continue
        directories[:] = sorted(n for n in directories if not (current/n).is_symlink())
        for name in sorted(files):
            lower = name.lower()
            if lower in ('boot.img', 'boot-linux.img'):
                continue
            if lower.endswith(('.zip', '.tar', '.tar.gz', '.tgz', '.ext4', '.ext4.gz', '.img', '.img.gz')):
                inputs.append(current/name)
    return sorted(inputs, key=lambda p: str(p).casefold())


class CachedGzip:
    """Provide random filesystem reads with a bounded 16 MiB gzip cache."""
    def __init__(self, stream):
        self.stream = stream
        self.position = 0
        self.blocks = collections.OrderedDict()
        self.chunk = 128 * 1024

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        if whence == 0:
            self.position = offset
        elif whence == 1:
            self.position += offset
        elif whence == 2:
            self.position = self.stream.seek(offset, 2)
        else:
            raise ValueError('Invalid seek origin')
        if self.position < 0:
            raise ValueError('Negative filesystem offset')
        return self.position

    def read(self, size):
        if size < 0:
            raise ValueError('Filesystem reader requires bounded reads')
        out = []
        while size:
            key, inner = divmod(self.position, self.chunk)
            if key not in self.blocks:
                self.stream.seek(key * self.chunk)
                self.blocks[key] = self.stream.read(self.chunk)
                if len(self.blocks) > 128:
                    self.blocks.popitem(last=False)
            block = self.blocks[key]
            self.blocks.move_to_end(key)
            part = block[inner:inner + size]
            if not part:
                break
            out.append(part)
            self.position += len(part)
            size -= len(part)
        return b''.join(out)


def root_index(stream, progress=lambda *_: None):
    """Same descriptors as Diff Studio, including links, APK metadata and xattrs."""
    stream.seek(0)
    zipped = stream.read(2) == b'\x1f\x8b'
    stream.seek(0)
    source = gzip.GzipFile(fileobj=stream) if zipped else stream
    try:
        source.seek(1080)
        ext4 = source.read(2) == b'\x53\xef'
        source.seek(0)
        entries, packages, metadata = {}, {}, {}
        keep = {'etc/e5/release.json', 'etc/mu300/release.json', 'lib/apk/db/installed'}

        def add(name, desc, content=None):
            nonlocal packages
            safe_name(name)
            if name in entries:
                raise ValueError('Duplicate rootfs path: ' + name)
            if len(entries) >= 250000:
                raise ValueError('Rootfs exceeds the release index file limit')
            if desc['kind'] == 'file':
                if name in keep:
                    raw = content.read((32 << 20) + 1)
                    if len(raw) != desc['size'] or len(raw) > 32 << 20:
                        raise ValueError('Invalid or excessive rootfs metadata: ' + name)
                    desc['sha256'] = hashlib.sha256(raw).hexdigest()
                    if name == 'lib/apk/db/installed':
                        packages = package_versions(raw)
                    else:
                        metadata[name] = json.loads(raw)
                else:
                    desc['sha256'] = digest(content)
            entries[name] = desc
            progress(len(entries), name)

        if ext4:
            from dissect.extfs import ExtFS
            fs = ExtFS(CachedGzip(source) if zipped else source)
            seen = set()

            def walk(node, prefix=''):
                if node.inum in seen:
                    raise ValueError('Cyclic ext4 directory')
                seen.add(node.inum)
                for child in node.iterdir():
                    if child.filename in ('.', '..'):
                        continue
                    name = safe_name(prefix + child.filename)
                    d = dict(mode=int(stat.S_IMODE(child.inode.i_mode)),
                             uid=int(child.inode.i_uid), gid=int(child.inode.i_gid),
                             xattrs={a.name: base64.b64encode(a.value).decode() for a in child.xattr})
                    if child.filetype == stat.S_IFDIR:
                        d.update(kind='dir')
                    elif child.filetype == stat.S_IFLNK:
                        d.update(kind='symlink', target=child.link)
                    elif child.filetype == stat.S_IFREG:
                        d.update(kind='file', size=child.size)
                    else:
                        continue
                    if d['kind'] == 'file':
                        with child.open() as content:
                            add(name, d, content)
                    else:
                        add(name, d)
                    if d['kind'] == 'dir':
                        walk(child, name + '/')
                seen.remove(node.inum)
            walk(fs.root)
        else:
            with tarfile.open(fileobj=source, mode='r|') as tar:
                for node in tar:
                    name = node.name.removeprefix('./').rstrip('/')
                    if name in ('', '.'):
                        continue
                    safe_name(name)
                    d = dict(mode=node.mode, uid=node.uid, gid=node.gid, xattrs={})
                    for key, value in node.pax_headers.items():
                        if key.startswith('SCHILY.xattr.'):
                            d['xattrs'][key[13:]] = base64.b64encode(value.encode('utf-8', 'surrogateescape')).decode()
                    if node.isdir():
                        d.update(kind='dir')
                    elif node.issym():
                        d.update(kind='symlink', target=node.linkname)
                    elif node.isfile():
                        d.update(kind='file', size=node.size)
                    elif node.islnk():
                        if len(entries) >= 250000:
                            raise ValueError('Rootfs exceeds the release index file limit')
                        target = node.linkname.removeprefix('./')
                        if target not in entries or entries[target]['kind'] != 'file':
                            raise ValueError('Unsupported forward tar hardlink: ' + name)
                        d = dict(entries[target])
                        if name in entries:
                            raise ValueError('Duplicate rootfs path: ' + name)
                        entries[name] = d
                        progress(len(entries), name)
                        continue
                    else:
                        continue
                    if node.isfile():
                        with tar.extractfile(node) as content:
                            add(name, d, content)
                    else:
                        add(name, d)
        return entries, packages, metadata
    finally:
        if zipped:
            source.close()


def summarize(path, boot=None, progress=lambda *_: None):
    """Full package hashes are checked; raw roots obtain identity from their JSON."""
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(str(path))
    with ExitStack() as stack:
        release = Release(path)
        if path.is_dir() or zipfile.is_zipfile(path):
            release.files = None
            release.prefix = ''
            if not path.is_dir():
                release.files = stack.enter_context(zipfile.ZipFile(path))
                names = release.files.namelist()
                if len(names) != len(set(names)):
                    raise ValueError('Duplicate files in full package')
                matches = [n[:-len(marker)] for n in names for marker in
                           ('files/device-profile.json', 'desktop/manifest.json') if n.endswith(marker)]
                if len(matches) != 1:
                    raise ValueError('Select a single complete release ZIP')
                release.prefix = matches[0]
            if release.exists('desktop/manifest.json'):
                family = 'mu300'
                meta = release.json('desktop/release.json')
                package = release.json('desktop/manifest.json')
                layout = package.get('magisk_layout', {})
                root_name = layout.get('mu300-openwrt.tar.gz', 'mu300-openwrt.tar.gz')
                boot_name = layout.get('boot-linux.img', 'boot-linux.img')
                checks = {**package['magisk'], **package['desktop']}
                if 'desktop/release.json' in checks:
                    release.check('desktop/release.json', checks['desktop/release.json'])
            else:
                family = 'e5'
                meta = release.json('files/RELEASE.json')
                root_name, boot_name = 'files/openwrt.ext4.gz', 'files/boot.img'
                checks = {}
                with release.open('files/SHA256SUMS') as lines:
                    for line in lines.read().decode().splitlines():
                        value, name = line.split(None, 1)
                        checks[name.strip().removeprefix('*')] = value
                for name in ('files/RELEASE.json', 'files/device-profile.json'):
                    if name in checks:
                        release.check(name, checks[name])
            release.check(root_name, checks[root_name])
            release.check(boot_name, checks[boot_name])
            with release.open(boot_name) as source:
                boot_digest = digest(source)
            with release.open(root_name) as source:
                entries, packages, _ = root_index(source, progress)
        else:
            with path.open('rb') as source:
                entries, packages, identities = root_index(source, progress)
            found = [(family, identities[name]) for family, name in
                     (('e5', 'etc/e5/release.json'), ('mu300', 'etc/mu300/release.json')) if name in identities]
            if len(found) != 1:
                raise ValueError('Rootfs must contain exactly one versioned etc/e5/release.json or etc/mu300/release.json')
            family, meta = found[0]
            boot_digest = UNKNOWN_BOOT
            if boot is None:
                siblings = [p for p in (path.parent/'boot.img', path.parent/'boot-linux.img') if p.is_file()]
                if len(siblings) == 1:
                    boot = siblings[0]
            if boot is not None:
                with Path(boot).open('rb') as source:
                    boot_digest = digest(source)
        if not isinstance(meta, dict):
            raise ValueError('Invalid release identity / 版本身份信息不是 JSON 对象')
        return snapshot(SimpleNamespace(family=family, meta=meta, entries=entries,
                                        packages=packages, boot_digest=boot_digest))


def filename(data):
    meta = data['release']['meta']
    version = meta['image_version'].removeprefix(meta['profile'] + '-')
    kernel = re.match(r'\d+(?:\.\d+){1,2}', meta['kernel_release'])
    name = f"OpenWrt-index-{meta['profile']}-{meta['openwrt']}-k{kernel[0] if kernel else meta['kernel_release']}-{version}"
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).rstrip('. ')[:190] + SUFFIX


def own_console():
    if sys.platform != 'win32' or not sys.stdin.isatty():
        return False
    array = (ctypes.c_uint32 * 8)()
    return ctypes.windll.kernel32.GetConsoleProcessList(array, 8) == 1


def positive_jobs(value):
    value = int(value)
    if value < 1:
        raise argparse.ArgumentTypeError('并发数至少为 1 / Workers must be at least 1')
    return value


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Export a Diff Studio JSON baseline / 导出 Diff Studio 版本摘要')
    parser.add_argument('input', type=Path, help='Release ZIP, rootfs, unpacked release or batch folder / 成品包、系统镜像、解压包或批量目录')
    parser.add_argument('-o', '--output', type=Path, help='Output JSON; batch: output folder / 单文件输出 JSON；批量输出目录')
    parser.add_argument('--boot', type=Path, help='Optional boot image for a bare rootfs / 裸 rootfs 配套启动镜像')
    parser.add_argument('-j', '--jobs', type=positive_jobs, default=2, help='Parallel inputs (default: 2); 1 for serial / 同时处理的包数，默认 2，设为 1 则串行')
    parser.add_argument('--no-pause', action='store_true', help='Do not pause a drag-and-drop console / 自动化时不暂停')
    args = parser.parse_args(argv)
    start = time.monotonic()
    log_lock, commit_lock = threading.Lock(), threading.Lock()
    cancelled = threading.Event()
    executor, futures = None, []
    status = 0

    def emit(message, error=False):
        with log_lock:
            print(message, file=sys.stderr if error else sys.stdout, flush=True)

    def process(number, source):
        last = 0
        label = f'[{number}/{len(inputs)}] {source.name}'
        def progress(count, name):
            nonlocal last
            if cancelled.is_set():
                raise CancelledError()
            if time.monotonic() - last >= 1:
                emit(f'{label} · 正在分析 / Indexing: {count} files · {name[:90]}')
                last = time.monotonic()
        try:
            if cancelled.is_set():
                raise CancelledError()
            emit(f'[{number}/{len(inputs)}] 正在识别并检查 / Detecting and checking: {source}')
            data = summarize(source, args.boot, progress)
            output = ((args.output or source.resolve().parent) / filename(data)) if batch else (args.output or source.resolve().parent / filename(data))
            # Different installer ZIPs may describe one firmware and one output
            # name. Serialize the check+commit, not the expensive indexing.
            with commit_lock:
                if cancelled.is_set():
                    raise CancelledError()
                existed = output.exists()
                write_index(output, data, reuse=True)
                size = output.stat().st_size
            meta = data['release']['meta']
            emit(f"{label} · {'复用 / REUSED' if existed else '成功 / SUCCESS'}: {output}\n"
                 f"{meta['profile']} · OpenWrt {meta['openwrt']} · {meta['image_version']}\n"
                 f"{len(data['release']['entries'])} files · {size / 1024:.1f} KiB")
            if data['release']['boot_digest'] == UNKNOWN_BOOT:
                emit(f'{label} · 未提供旧版 boot：差异包将包含新版完整 boot。/ No old boot supplied: the patch will include the complete new boot.')
            return True
        except (OSError, ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile) as error:
            emit(f'失败 / FAILED: {source}\n{error}', error=True)
            return False

    try:
        batch = args.input.is_dir() and not complete_directory(args.input)
        if batch and args.boot:
            raise ValueError('--boot 仅用于单个输入 / --boot is only supported for a single input')
        if batch and args.output and (args.output.is_file() or not args.output.is_dir() and args.output.suffix.lower() == '.json'):
            raise ValueError('批量输出需指定目录，而不是单个 JSON / Batch output must be a folder, not one JSON file')
        inputs = directory_inputs(args.input) if batch else [args.input]
        if not inputs:
            raise ValueError('目录中未找到成品 ZIP、rootfs 或完整解压包 / No release ZIP, rootfs or unpacked release found')
        workers = min(args.jobs, len(inputs))
        if batch:
            emit(f'批量模式 / Batch: {len(inputs)} inputs · {workers} workers')
        executor = ThreadPoolExecutor(max_workers=workers)
        futures = [executor.submit(process, number, source) for number, source in enumerate(inputs, 1)]
        pending = set(futures)
        while pending:
            # A short timed wait also lets Windows deliver Ctrl+C promptly.
            done, pending = wait(pending, timeout=.2, return_when=FIRST_COMPLETED)
            for future in done:
                future.result()
    except (OSError, ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile) as error:
        emit('失败 / FAILED: ' + str(error), error=True)
        status = 1
    except KeyboardInterrupt:
        cancelled.set()
        for future in futures:
            future.cancel()
        status = 130
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=cancelled.is_set())
    if futures:
        results = [f.result() for f in futures if not f.cancelled() and f.exception() is None]
        successes, failures = results.count(True), results.count(False)
        if status == 130:
            emit(f'已中止，已完成的摘要会保留 / Cancelled; completed indexes are kept: {successes}', error=True)
        else:
            status = 1 if failures else status
            emit(f'处理完成 / Finished: {successes} succeeded, {failures} failed · {time.monotonic()-start:.1f}s')
        if successes:
            emit('Diff Studio：旧版选此 JSON，新版选完整 ZIP。/ Select this JSON as the old release and a full ZIP as the new release.')
    if not args.no_pause and own_console():
        input('按回车退出 / Press Enter to exit…')
    return status


if __name__ == '__main__':
    raise SystemExit(main())
