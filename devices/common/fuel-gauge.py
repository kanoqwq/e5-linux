#!/usr/bin/env python3
"""Restore vendor charging policy and retain verified SOC across shutdown."""
from pathlib import Path
import subprocess
import tempfile

PATCH = Path(__file__).with_name('fuel-gauge-state.patch')
STOCK_POLICY = Path(__file__).with_name('charger-stock-policy.patch')
SAVE_DIAGNOSTICS = Path(__file__).with_name('fuel-gauge-save-diagnostics.patch')
STOCK_LATCHES = Path(__file__).with_name('fuel-gauge-stock-latches.patch')
CHARGER_LIFETIME = Path(__file__).with_name('charger-lifetime.patch')
CAPACITY_ARITHMETIC = Path(__file__).with_name('charger-capacity-arithmetic.patch')
JEITA_STATE = Path(__file__).with_name('charger-jeita-state.patch')
SET_LATCH = Path(__file__).with_name('fuel-gauge-set-latch.patch')
SAVE_READY = Path(__file__).with_name('fuel-gauge-save-ready.patch')


def apply(kernel):
    kernel = Path(kernel).resolve()
    patches = (PATCH, STOCK_POLICY, SAVE_DIAGNOSTICS, STOCK_LATCHES,
               CHARGER_LIFETIME, CAPACITY_ARITHMETIC, JEITA_STATE, SET_LATCH, SAVE_READY)
    paths = set(line[6:] for patch in patches for line in patch.read_text().splitlines()
                if line.startswith('+++ b/'))
    with tempfile.TemporaryDirectory(prefix='e5-fgu-') as directory:
        stage = Path(directory)
        for name in paths:
            target = stage/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((kernel/name).read_bytes())
        args = ['git', 'apply', '--whitespace=nowarn']
        # Unstack already applied patches in reverse order, then validate and
        # apply the complete stack in staging before changing the build tree.
        for patch in reversed(patches):
            if subprocess.run(args + ['--reverse', '--check', str(patch)], cwd=stage,
                              capture_output=True).returncode == 0:
                subprocess.run(args + ['--reverse', str(patch)], cwd=stage, check=True)
        for patch in patches:
            result = subprocess.run(args + ['--check', str(patch)], cwd=stage,
                                    capture_output=True, text=True)
            if result.returncode:
                raise ValueError('Fuel-gauge source differs from supported revision: ' + result.stderr.strip())
            subprocess.run(args + [str(patch)], cwd=stage, check=True)
        for name in paths:
            target = kernel/name
            content = (stage/name).read_bytes()
            if target.read_bytes() != content:
                target.write_bytes(content)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kernel', type=Path)
    apply(parser.parse_args().kernel)
