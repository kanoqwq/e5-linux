#!/usr/bin/env python3
"""Stage shared UMS9620 userspace with K1 identity and no M8 calibration."""
from pathlib import Path
import json
import runpy


def stage(root):
    shared = Path(__file__).resolve().parents[1] / 'm8/stage.py'
    runpy.run_path(str(shared))['stage'](root, profile='k1')
    # AngelCare.K1.YUAN.YD.T1.0 devices/handset uses AO mixers and the
    # Earpiece endpoint, unlike the donor's EAR_HPL route or M8's loudspeaker.
    wiring = json.loads(Path(__file__).with_name('profile.json').read_text())['audio_receiver_wiring']
    if wiring != 'aol':
        raise ValueError('Unexpected K1 receiver wiring')
    (Path(root) / 'etc/e5/receiver-wiring').write_text(wiring + '\n')
