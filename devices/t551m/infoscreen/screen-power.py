#!/usr/bin/python3
"""Serialize backlight/touch transitions and reconcile out-of-band changes.

Input-core inhibition releases held contacts and drops all subsequent events.
Never unbind/reset/reflash the touch controller. Physical keys stay available.
"""
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import time

SYS = Path('/sys')
LOCK = Path('/run/t551m-screen-power.lock')
CONFIG = (Path('/etc/config/e5-infoscreen'), Path('/tmp/.uci/e5-infoscreen'))


class TouchSetting:
    """Keep libuci semantics, including unsaved deltas, without forking per tick."""
    def __init__(self):
        self.signature = None
        self.allowed = True

    def get(self):
        signature = []
        for path in CONFIG:
            try:
                st = path.stat()
                signature.append((st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size))
            except FileNotFoundError:
                signature.append(None)
        if signature != self.signature:
            self.allowed = touch_allowed()
            self.signature = signature
        return self.allowed


def touch_allowed():
    result = subprocess.run(['uci', '-q', 'get', 'e5-infoscreen.main.touch'],
                            capture_output=True, text=True, timeout=3)
    return result.stdout.strip() != '0'


def devices():
    backlights = list((SYS / 'class/backlight').glob('*/brightness'))
    touches = []
    for path in (SYS / 'class/input').glob('input*'):
        try:
            # INPUT_PROP_DIRECT identifies the panel independently of driver name.
            if int((path / 'properties').read_text().strip(), 16) & 2 and (path / 'inhibited').exists():
                touches.append(path / 'inhibited')
        except (OSError, ValueError):
            continue
    if not backlights or not touches:
        raise RuntimeError('Backlight or touch device missing')
    return backlights, touches


def inhibit(touches, disabled):
    value = '1' if disabled else '0'
    for path in touches:
        if path.read_text().strip() != value:
            path.write_text(value + '\n')
            if path.read_text().strip() != value:
                raise RuntimeError('Touch inhibition did not take effect')


def reconcile(level=None, allowed=None, paths=None):
    with LOCK.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        backlights, touches = paths if paths is not None else devices()
        if allowed is None:
            allowed = touch_allowed()
        if level is not None:
            level = max(0, min(255, int(level)))
            # Disable before darkening; restore before lighting. JS keeps the
            # lock and its wake guard engaged throughout either transition.
            inhibit(touches, not (level > 0 and allowed))
            for path in backlights:
                path.write_text(str(level) + '\n')
        on = any(int(p.read_text()) > 0 for p in backlights)
        inhibit(touches, not (on and allowed))


def watch():
    setting, paths, last_error = TouchSetting(), None, None
    while True:
        try:
            if paths is None:
                paths = devices()
            reconcile(allowed=setting.get(), paths=paths)
            last_error = None
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            paths = None  # rediscover if a driver/input device has disappeared
            if str(error) != last_error:
                print('t551m-screen-power: ' + str(error), flush=True)
                last_error = str(error)
        time.sleep(.1)


if __name__ == '__main__':
    os.umask(0o077)
    if len(sys.argv) == 2 and sys.argv[1] == 'watch':
        watch()
    elif len(sys.argv) == 2 and sys.argv[1] == 'sync':
        reconcile()
    elif len(sys.argv) == 3 and sys.argv[1] == 'set':
        reconcile(int(sys.argv[2]))
    else:
        raise SystemExit('usage: t551m-screen-power watch|sync|set LEVEL')
