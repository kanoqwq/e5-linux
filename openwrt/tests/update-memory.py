#!/usr/bin/env python3
"""Update memory budgets and disk staging; synthetic local files only."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOP=Path(__file__).resolve().parents[2]


class UpdateMemory(unittest.TestCase):
    def test_offline_preflight_uses_physical_ram_not_running_browser_ram(self):
        source=(TOP/'openwrt/device-stage-part-update.sh').read_text()
        start=source.index('payload=$(du')
        code=source[start:source.index('cid=',start)]
        for total,expected in ((1532172,True),(200000,False)):
            with self.subTest(total=total),tempfile.TemporaryDirectory() as d:
                root=Path(d);(root/'nv.tar').write_bytes(b'compressed NV fixture')
                info=root/'meminfo';info.write_text(f'MemTotal: {total} kB\nMemAvailable: 100000 kB\n')
                harness='''set -eu
du() { for p; do [ "$p" = -k ] || printf '60000 %s\n' "$p"; done; }
'''+f'stage="{root}"\n'+code.replace('/proc/meminfo',str(info))
                result=subprocess.run(['busybox','ash'],input=harness,text=True,capture_output=True)
                self.assertEqual(result.returncode==0,expected,result.stderr)

    def test_uploads_and_legacy_nv_spools_never_use_tmpfs(self):
        host=(TOP/'tools/e5-update.py').read_text()
        self.assertIn("stage = '/.e5-upload-'",host)
        self.assertIn('df -Pk / |',host);self.assertNotIn('df -Pk /tmp',host)
        for name in ('device-install.sh','device-install-image.sh'):
            text=(TOP/'openwrt'/name).read_text()
            self.assertRegex(text,r'mktemp /opt/e5-[^\n]*nv\.')
            self.assertNotRegex(text,r'mktemp /tmp/[^\n]*nv\.')

    def test_settings_nv_history_is_not_duplicated_when_archived(self):
        source=(TOP/'openwrt/device-stage-part-update.sh').read_text()
        code=source[source.index('settings='):source.index('tar -cf /.e5-update/settings.tar')]
        for working,policy,expected in ((True,'preserve',False),(False,'preserve',True),(True,'original',True)):
            with self.subTest(working=working,policy=policy),tempfile.TemporaryDirectory() as d:
                root=Path(d);(root/'etc/e5-nv').mkdir(parents=True)
                if working:(root/'opt/e5/nv').mkdir(parents=True)
                result=subprocess.run(['busybox','ash'],input=f'E5_NV_POLICY={policy}\n'+code+'\nprintf "%s" "$settings"\n',cwd=root,text=True,capture_output=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual('etc/e5-nv' in result.stdout.split(),expected)

    def test_cli_compressed_archive_roundtrips_on_busybox(self):
        spec=importlib.util.spec_from_file_location('nv_fixture',TOP/'openwrt/tests/nv-persistence.py')
        fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
        case=fixture.NVTests();case.setUp()
        try:
            (case.store/'calinv').write_bytes(bytes(4<<20))
            result=subprocess.run([sys.executable,str(fixture.SOURCE),'archive','--gzip','--root',str(case.root)],capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr);self.assertTrue(result.stdout.startswith(b'\x1f\x8b'))
            target=case.root/'archive.tar';target.write_bytes(result.stdout)
            checked=subprocess.run(['busybox','tar','-ztf',str(target)],capture_output=True,text=True)
            self.assertEqual(checked.returncode,0,checked.stderr)
            self.assertIn('opt/e5/nv/calinv',checked.stdout);self.assertLess(len(result.stdout),100000)
            new=case.restore(result.stdout);self.assertEqual((new/'opt/e5/nv/calinv').read_bytes(),bytes(4<<20))
        finally:case.doCleanups()


if __name__=='__main__':unittest.main()
