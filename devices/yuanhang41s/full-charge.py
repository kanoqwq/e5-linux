"""Select this SGM board's hardware termination endpoint, not the donor policy.

The stock SQC charger reports REG08=CHARGE_DONE as STATUS_FULL. Stock CM
accepts a full indication only above its DT full-battery voltage, then adjusts
the coulomb-counter baseline. OpenWrt has no SQC daemon: consume the actual
charger endpoint, never its removed userspace status cache or a guessed SOC.
"""
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
PATCH = HERE / 'full-charge.patch'
MARKER = 'P720S11 hardware termination endpoint'


def restore(kernel):
    kernel = Path(kernel)
    args = ['git', 'apply', '--whitespace=nowarn', '--reverse']
    if subprocess.run(args + ['--check', str(PATCH)], cwd=kernel,
                      capture_output=True).returncode == 0:
        subprocess.run(args + [str(PATCH)], cwd=kernel, check=True)
    elif MARKER in (kernel / 'drivers/power/supply/charger-manager.c').read_text():
        raise ValueError('41S full-charge policy changed; cannot unstack safely')


def apply(kernel):
    args = ['git', 'apply', '--whitespace=nowarn']
    if subprocess.run(args + ['--reverse', '--check', str(PATCH)], cwd=kernel,
                      capture_output=True).returncode == 0:
        return
    subprocess.run(args + ['--check', str(PATCH)], cwd=kernel, check=True)
    subprocess.run(args + [str(PATCH)], cwd=kernel, check=True)
