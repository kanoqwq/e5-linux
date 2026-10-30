"""The installer is now a pinned submodule, never a generated source mirror."""
import argparse
from pathlib import Path
import subprocess


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository',type=Path)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    if not args.check:
        raise SystemExit('Source mirroring is retired. Commit in openwrt-tools, then update the installer submodule in each firmware repository.')
    expected=subprocess.check_output(['git','-C',str(Path(__file__).resolve().parent),'rev-parse','HEAD'],text=True).strip()
    actual=subprocess.check_output(['git','-C',str(args.repository/'installer'),'rev-parse','HEAD'],text=True).strip()
    if actual!=expected:raise SystemExit('Installer submodule revisions differ: '+actual+' != '+expected)
    print('Installer submodule revision:',actual)


if __name__=='__main__':main()
