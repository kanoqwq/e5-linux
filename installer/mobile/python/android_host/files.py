"""Bounded package imports; no shared-storage path assumptions or shell unzip."""
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import shutil
import stat
import time
import zipfile


def locate(root,kind):
    if kind=='package':
        from backend.package import locate_package
        return locate_package(root)
    candidates=[root] if (root/'transaction.json').is_file() else [p for p in root.iterdir() if p.is_dir() and (p/'transaction.json').is_file()]
    if len(candidates)!=1:raise ValueError('Select one complete recovery ZIP / 请选择一份完整恢复备份')
    return candidates[0]


def extract_archive(archive,base,kind,emit=lambda *_:None):
    digest=hashlib.sha256()
    with archive.open('rb') as stream:
        for block in iter(lambda:stream.read(1<<20),b''):digest.update(block)
    base.mkdir(parents=True,exist_ok=True);dest=base/digest.hexdigest()
    marker=dest/'.mobile-import.json'
    if marker.is_file():
        return locate(dest,kind)
    staging=base/(digest.hexdigest()+'.importing')
    # Only a directory owned by this exact archive import may be removed.
    if staging.exists():
        owner=staging/'.mobile-import-owner'
        if not owner.is_file() or owner.read_text()!=digest.hexdigest() or staging.is_symlink():
            raise ValueError('Unrecognized interrupted import directory')
        shutil.rmtree(staging)
    with zipfile.ZipFile(archive) as z:
        rows=z.infolist();seen=set();total=sum(p.file_size for p in rows)
        if len(rows)>100000 or total>64<<30:raise ValueError('Package exceeds import limits')
        if shutil.disk_usage(base).free<total+(256<<20):raise ValueError('Not enough phone storage to extract this release / 手机空间不足，无法解压此安装包')
        for row in rows:
            path=PurePosixPath(row.filename)
            if (path.is_absolute() or '\\' in row.filename or '..' in path.parts or not path.parts or
                    row.filename in seen or stat.S_ISLNK(row.external_attr>>16)):
                raise ValueError('Unsafe package member / 安装包路径不安全: '+row.filename)
            seen.add(row.filename)
        staging.mkdir();(staging/'.mobile-import-owner').write_text(digest.hexdigest())
        done=0;last=0
        try:
            for row in rows:
                path=staging/row.filename
                if row.is_dir():path.mkdir(parents=True,exist_ok=True);continue
                path.parent.mkdir(parents=True,exist_ok=True)
                with z.open(row) as src,path.open('xb') as out:
                    for block in iter(lambda:src.read(1<<20),b''):
                        out.write(block);done+=len(block)
                now=time.monotonic()
                if now-last>=.2 or done==total:
                    emit('import_progress',{'done':done,'total':total,'path':row.filename});last=now
            locate(staging,kind)
            (staging/'.mobile-import-owner').unlink()
            (staging/'.mobile-import.json').write_text(json.dumps({'sha256':digest.hexdigest(),'bytes':total}))
            os.replace(staging,dest)
        except BaseException:
            # Keep the owned partial import for diagnosis/retry, never publish it.
            raise
    return locate(dest,kind)


def extract_package(archive,base,emit=lambda *_:None):
    return extract_archive(archive,base,'package',emit)


def extract_recovery(archive,base,emit=lambda *_:None):
    root=extract_archive(archive,base,'recovery',emit)
    path=root/'transaction.json';record=json.loads(path.read_text())
    old=record.get('data_directory')
    if not isinstance(old,str) or not old:raise ValueError('Recovery record lacks its original data directory')
    from backend.recovery_records import path_relocator
    relocate=path_relocator(old,root)
    legacy=record.get('legacy')
    if legacy:
        rewritten=Path(relocate(legacy)).resolve()
        if not rewritten.is_relative_to(root.resolve()) or not rewritten.is_file():raise ValueError('Recovery legacy record is missing')
        data=json.loads(rewritten.read_text())
        backup=rewritten.with_name(rewritten.name+'.mobile-original')
        if not backup.exists():backup.write_bytes(rewritten.read_bytes())
        rewritten.write_text(json.dumps(relocate(data),ensure_ascii=False,indent=2)+'\n')
    backup=root/'transaction.json.mobile-original'
    if not backup.exists():backup.write_bytes(path.read_bytes())
    path.write_text(json.dumps(relocate(record),ensure_ascii=False,indent=2)+'\n')
    return root
