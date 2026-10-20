"""Keep SIM-scoped dashboard commands off MM without splitting SMS ownership."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'rootfs/overlay/opt/e5/e5-at'


class Routing(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / 'profile'
        self.profile.write_text('t551m\n')
        for name, body in {
            'fast': 'printf "FAST:%s:%s:%s:%s\\n" "$T551M_AT_CHANNEL" "$1" "$2" "$3"',
            'systemctl': 'exit 0',
            'mmcli': 'printf "response: \'MM-REPLY\'\\n"',
        }.items():
            p = self.root / name
            p.write_text('#!/bin/sh\n' + body + '\n')
            p.chmod(0o755)
        self.script = self.root / 'e5-at'
        self.script.write_text(SOURCE.read_text().replace(
            '/etc/e5/device-profile', str(self.profile)).replace(
            '/opt/e5/t551m-at', str(self.root / 'fast')))
        self.env = dict(os.environ, PATH=str(self.root) + ':' + os.environ['PATH'],
                        T551M_AT_CHANNEL='7')

    def call(self, command):
        return subprocess.check_output(['sh', str(self.script), '-t', '5', command],
                                       env=self.env, text=True).strip()

    def test_scoped_queries_and_controls_use_selected_owner(self):
        for profile in ('t551m', 'e5', 'm8', 't512n', 'yuanhang41s', 'k1'):
            self.profile.write_text(profile + '\n')
            for slot in (0, 1):
                for suffix in ('+CFUN?', '+CFUN=4', '+CSCS?;+COPS?',
                               '+SPLBAND=0', '+SPENGMD=0,0,6',
                               '+CGDCONT=1,"IP","MixedCaseAPN"'):
                    command = f'AT+SPACTCARD={slot};{suffix}'
                    self.assertEqual(self.call(command), 'FAST:7:-t:5:' + command)
            command = 'at+spactcard=1;+cesq'
            self.assertEqual(self.call(command), 'FAST:7:-t:5:' + command)

    def test_scoped_sms_keeps_mm_charset_storage_and_submit_owner(self):
        for profile in ('t551m', 'e5', 'm8', 't512n', 'yuanhang41s', 'k1'):
            self.profile.write_text(profile + '\n')
            for suffix in ('+CMGL=4', '+CMGR=1', '+CMGD=1', '+CMGS=12',
                           '+CMGF=0', '+CPMS?', '+CSCS="UCS2"', '+CNMI?',
                           '+CSCS?;+CMGR=1'):
                self.assertEqual(self.call('AT+SPACTCARD=1;' + suffix), 'MM-REPLY\nOK')
            self.assertEqual(self.call('AT+E5SMS=?'), 'MM-REPLY\nOK')

    def test_legacy_images_and_plain_commands_preserve_routing(self):
        self.assertEqual(self.call('AT+CESQ'), 'FAST:7:-t:5:AT+CESQ')
        self.profile.write_text('m8\n')
        self.assertEqual(self.call('AT'), 'FAST:7:-t:5:AT')
        self.assertEqual(self.call('AT+E5SMS=?'), 'MM-REPLY\nOK')
        self.profile.write_text('legacy\n')
        self.assertEqual(self.call('AT+SPACTCARD=1;+CESQ'), 'MM-REPLY\nOK')


if __name__ == '__main__':
    unittest.main()
