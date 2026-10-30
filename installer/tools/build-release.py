#!/usr/bin/env python3
"""Run a native installer build with the shared workspace lock and durable logs."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
    temp.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
    temp.replace(path)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='action',required=True)
    runner=commands.add_parser('run')
    runner.add_argument('--workspace',type=Path,required=True)
    runner.add_argument('--label',required=True)
    runner.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    if not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}',args.label):raise ValueError('Invalid task label')
    command=args.command
    if command[:1]==['--']:command=command[1:]
    if not command:raise ValueError('Build command is required')
    if os.name!='posix':raise ValueError('Use the existing WSL workspace runner for Windows native builds')
    if not args.workspace.is_dir():raise ValueError('Existing build workspace is missing; do not create a duplicate workspace')
    import fcntl
    locks=args.workspace/'locks';locks.mkdir(exist_ok=True)
    fd=os.open(locks/'openwrt-release.lock',os.O_RDONLY|os.O_CREAT,0o644)
    with os.fdopen(fd,'rb') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:raise ValueError('Another build owns the shared workspace') from error
        task=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+args.label+'-'+str(os.getpid())
        directory=ROOT/'out/build-logs'/task;directory.mkdir(parents=True)
        state={'task':task,'status':'running','command':command,'log':str(directory/'command.log')}
        destination=ROOT/'out/build-state/latest.json';atomic(destination,state)
        print('command: '+state['log'],flush=True)
        try:
            with (directory/'command.log').open('wb') as output:
                result=subprocess.run(command,cwd=ROOT,stdout=output,stderr=subprocess.STDOUT,check=False)
            state.update(status='complete' if result.returncode==0 else 'failed',exit_code=result.returncode)
            return result.returncode
        except BaseException as error:
            state.update(status='failed',error=type(error).__name__+': '+str(error))
            raise
        finally:
            atomic(destination,state);atomic(directory/'status.json',state)
            fcntl.flock(lock,fcntl.LOCK_UN)


if __name__=='__main__':sys.exit(main())
