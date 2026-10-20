#!/usr/bin/env python3
"""Check digital/analog/BT transitions without activating CP voice."""
from pathlib import Path
import os
import runpy
import tempfile
import unittest

BOARD = Path(__file__).parent


class Base:
    bluetooth = False
    speaker_path = True

    def __init__(self):
        self.writes = []

    def controls(self):
        return ('S_VOICE_P_CODEC SWITCH',)

    def put(self, name, value):
        self.writes.append((name, value))

    def profiles(self):
        self.put('VBC_MUX_IIS1_PORT_DO_SEL', 'IIS_DO_VAL_DAC0')

    def output(self):
        self.put('S_VOICE_P_BT SWITCH', 'on')


class Routes(unittest.TestCase):
    def setUp(self):
        # Minimal recipient fixture; no proprietary tables or identifiers.
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.route = Path(self.temp.name) / 'route.xml'
        groups = {
            'vbc_iis_mux': {
                'only_smart_spk_p': {
                    'on': {'SYS_IIS3': 'vbc_iism0', 'VBC_IIS_MST_WIDTH_SET': 'WD_24BIT',
                           'VBC_MUX_IIS1_PORT_DO_SEL': 'IIS_DO_VAL_DAC1'},
                    'off': {'VBC_IIS_MASTER_ENALBE': 'disable'}},
                'only_codec_p': {'on': {'VBC_MUX_IIS0_PORT_DO_SEL': 'IIS_DO_VAL_DAC0'}}},
            'devices': {'handset': {
                'on': {'Earpiece Function': '1'}, 'off': {'Earpiece Function': '0'}}},
            'usecase_device': {'default': {'on': {'aw_dev_0_prof': 'Music'}}},
        }
        import xml.etree.ElementTree as ET
        root = ET.Element('root')
        for category, paths in groups.items():
            parent = ET.SubElement(root, category)
            for path, states in paths.items():
                node = ET.SubElement(parent, path)
                for state, values in states.items():
                    entry = ET.SubElement(node, state)
                    for name, value in values.items():
                        ET.SubElement(entry, 'ctl', name=name, val=value)
        ET.ElementTree(root).write(self.route)
        self.factory = runpy.run_path(str(BOARD / 'call-audio.py'))['hardware_class']
        self.hw = self.factory(Base, self.route)()

    def test_speaker_uses_digital_backend_and_retains_snapshot_controls(self):
        self.hw.output()
        writes = self.hw.writes
        self.assertLess(writes.index(('S_VOICE_P_HIFI SWITCH', 'on')),
                        writes.index(('S_VOICE_P_CODEC SWITCH', 'off')))
        self.assertNotIn(('Speaker Function', 'on'), writes)
        self.assertIn(('VBC_IIS_MST_WIDTH_SET', 'MST_WD_24BIT'), writes)
        self.assertTrue({name for name, _ in writes}.issubset(
            {*self.hw.controls(), 'Speaker Function'}))

    def test_receiver_makes_codec_before_breaking_digital(self):
        self.hw.speaker_path = False
        self.hw.output()
        self.assertLess(self.hw.writes.index(('S_VOICE_P_CODEC SWITCH', 'on')),
                        self.hw.writes.index(('S_VOICE_P_HIFI SWITCH', 'off')))
        self.assertEqual(dict(self.hw.writes)['Earpiece Function'], '1')

    def test_profile_does_not_overwrite_digital_wiring(self):
        self.hw.profiles()
        self.assertEqual(dict(self.hw.writes)['VBC_MUX_IIS1_PORT_DO_SEL'], 'IIS_DO_VAL_DAC1')

    def test_bt_uses_shared_route_before_disconnecting_pa(self):
        self.hw.bluetooth = True
        self.hw.output()
        self.assertEqual(self.hw.writes, [('S_VOICE_P_BT SWITCH', 'on'),
                                         ('S_VOICE_P_HIFI SWITCH', 'off')])

    def test_rejects_missing_or_mismatched_recipient_route(self):
        self.route.write_text('<root/>')
        with self.assertRaises(ValueError):
            self.factory(Base, self.route)

    @unittest.skipIf(os.name == 'nt', 'Offline root staging requires POSIX symlinks')
    def test_board_audio_staging_is_repeatable_and_keeps_shared_import(self):
        stage = runpy.run_path(str(BOARD / 'stage.py'))['stage_audio']
        root = Path(self.temp.name) / 'root'
        (root / 'etc/e5').mkdir(parents=True)
        (root / 'etc/e5/device-profile').write_text('yuanhang41s\n')
        (root / 'usr/share/alsa/ucm2/sprdphone-sc273').mkdir(parents=True)
        stage(root)
        files = ['opt/e5/e5-call-audio.py', 'opt/e5/e5-audio-dsp',
                 'usr/libexec/e5-volume', 'opt/e5/yuanhang41s_call_audio.py']
        before = {name: (root / name).read_bytes() for name in files}
        stage(root)
        self.assertEqual(before, {name: (root / name).read_bytes() for name in files})
        voice = before['opt/e5/e5-call-audio.py'].decode()
        self.assertLess(voice.index('VoiceHardware = hardware_class(VoiceHardware)'),
                        voice.index('def main():'))
        self.assertIn('SYS_IIS3 vbc_iism0', before['opt/e5/e5-audio-dsp'].decode())
        self.assertEqual(before['usr/libexec/e5-volume'].count(b'\nHW_GAIN='), 1)
        for name, content in before.items():
            self.assertNotIn(b'\r', content)
            if name.endswith('.py'):
                compile(content, name, 'exec')


if __name__ == '__main__':
    unittest.main()
