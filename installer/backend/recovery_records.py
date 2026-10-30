"""Publish a portable recovery record without changing partition-backup bytes."""
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import time

from .state import atomic_json


def path_relocator(original, target):
    target = Path(target)
    parser = PureWindowsPath if PureWindowsPath(original).drive else PurePosixPath
    root = parser(original)
    roots = [root]
    # macOS /var and /private/var (and imported directory aliases) can name
    # the same existing record. Portable paths from another OS stay lexical.
    if parser is PurePosixPath and os.name == 'posix' and Path(original).exists():
        resolved = parser(str(Path(original).resolve()))
        if resolved != root: roots.append(resolved)

    def relocate(value):
        if isinstance(value, dict):
            return {key: relocate(item) for key, item in value.items()}
        if isinstance(value, list):
            return [relocate(item) for item in value]
        if not isinstance(value, str):
            return value
        for candidate in roots:
            try:
                relative = parser(value).relative_to(candidate)
                break
            except ValueError:
                continue
        else:
            return value
        if '..' in relative.parts:
            raise ValueError('Recovery path escapes its record / 恢复路径超出记录目录')
        return str(target.joinpath(*relative.parts))

    return relocate


def import_record(source, target, record):
    source, target = Path(source).resolve(), Path(target)
    if target.exists() or target.is_symlink():
        raise ValueError('Transaction already exists / 此安装记录已存在')
    target = target.resolve()
    relocate = path_relocator(record.get('data_directory') or str(source), target)
    restored = relocate(record)
    restored['data_directory'] = str(target)
    legacy = restored.get('legacy')
    relative = None
    data = None
    if legacy:
        # Only the recorded JSON metadata is rewritten. Images, NV and backup
        # archives are copied byte-for-byte and still undergo resume checks.
        relative = Path(legacy).resolve().relative_to(target)
        path = (source/relative).resolve()
        if not path.is_relative_to(source) or not path.is_file():
            raise ValueError('Recovery legacy record is missing / 恢复备份缺少旧安装记录')
        data = relocate(json.loads(path.read_text(encoding='utf-8')))

    staging = target.with_name(target.name+'.importing')
    if staging.resolve().is_relative_to(source) or source.is_relative_to(staging.resolve()):
        raise ValueError('Recovery source overlaps its destination / 恢复源与目标记录目录重叠')
    owner = staging/'.record-import-owner.json'
    identity = dict(component='recovery-record-import', transaction=record['id'])
    if staging.exists():
        if staging.is_symlink() or staging.resolve().parent != target.parent or not owner.is_file() or json.loads(owner.read_text()) != identity:
            raise ValueError('Unrecognized interrupted record import / 无法识别中断的记录导入目录')
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    atomic_json(owner, identity)
    shutil.copytree(source, staging, dirs_exist_ok=True, ignore=shutil.ignore_patterns('.record-import-owner.json'))
    if relative is not None:
        atomic_json(staging/relative, data)
    restored['revision'] = restored.get('revision', 0)+1
    restored['updated'] = time.time()
    atomic_json(staging/'transaction.json', restored)
    os.replace(staging, target)
    return restored
