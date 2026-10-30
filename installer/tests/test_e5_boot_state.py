"""Exercise the compatible Android preflight without transports or device writes."""
from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from firmware_sources import firmware_root
SOURCE=firmware_root('e5')/'openwrt/bundle/flash.py'


@unittest.skipUnless(SOURCE.is_file(), 'E5 compatible CLI source is tested in the E5 repository')
class E5BootState(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('e5_boot_state_fixture',SOURCE)
        self.flash=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.flash)

    def check(self, profile, model, board, slot, reported_model=None, uid='0'):
        values={'id -u':uid, 'getprop ro.product.model':reported_model or model,
                'getprop ro.product.device':board, 'getprop ro.boot.slot_suffix':slot}
        def shell(command):
            if command.endswith(' -c id'):return 'uid=2000(shell)'
            # Any attempt to query the old BL gate is a test failure.
            self.assertIn(command,values,command)
            return values[command]
        with patch.multiple(self.flash,SERIAL='fixture',GUI=None), \
             patch.object(self.flash,'adb',return_value='List of devices attached\nfixture\tdevice\n'), \
             patch.object(self.flash,'sh',side_effect=shell), \
             patch.object(self.flash,'package_profile',return_value={'profile':profile,'android_models':[model]}), \
             patch.object(self.flash,'su',return_value='ok') as root, \
             patch.object(self.flash,'android_tools') as tools, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:self.flash.android_checks()
            except SystemExit:
                tools.assert_not_called();root.assert_not_called()
                raise
            self.assertEqual(self.flash.current_slots(),(slot[1:],'b' if slot=='_a' else 'a'))
            self.assertIn('boot_'+self.flash.LINUX_SLOT,root.call_args.args[0])
            self.assertIn('l_agdsp_'+self.flash.ANDROID_SLOT,root.call_args.args[0])
            tools.assert_called_once()

    def test_all_profiles_check_root_and_slots_without_a_bl_requirement(self):
        profiles=[('e5','E5','fixture'),('m8','M8','fixture'),('t551m','T551M','fixture'),
                  ('t512n','T512N','fixture'),('yuanhang41s','ZTE 7546N','P720S11'),('k1','angelcare K1','ums9157_2h10')]
        for profile,model,board in profiles:
            for slot in ('_a','_b'):
                with self.subTest(profile=profile,slot=slot):self.check(profile,model,board,slot)

    def test_missing_root_wrong_model_board_or_slot_still_stop_before_tool_upload(self):
        cases=[dict(uid='2000'),dict(reported_model='wrong-model'),dict(slot=''),dict(slot='_c')]
        for change in cases:
            args=dict(profile='e5',model='E5',board='fixture',slot='_a');args.update(change)
            with self.subTest(change=change),self.assertRaises(SystemExit):self.check(**args)
        for profile,model in [('k1','angelcare K1'),('yuanhang41s','ZTE 7546N')]:
            with self.subTest(profile=profile),self.assertRaises(SystemExit):self.check(profile,model,'wrong-board','_a')


if __name__=='__main__':unittest.main()
