#!/usr/bin/env python3
"""Issue #21: run the real switch worker around an asynchronous module reload."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

TOP = Path(__file__).resolve().parents[2]
TOOL = TOP / 'openwrt/luci-app-t551m/root/usr/libexec/unisoc-modem/data-sim'


class ReloadTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.card = root / 'card'
        self.card.write_text('0')
        with patch.dict(os.environ, E5_DATA_SIM_CARD=str(self.card), E5_DATA_SIM_RUN=str(root / 'jobs')):
            self.scope = runpy.run_path(str(TOOL))
        self.g = self.scope['worker'].__globals__
        self.scope['RUN'].mkdir()
        self.job = {'id': 'a'*32, 'state': 'running', 'kind': 'switch', 'previous': '0', 'target': '1'}
        self.scope['write'](self.job)
        self.clock = 0
        self.sleeps = []

    def run_worker(self, final='1', saved='1', elapsed=2, returncode=0):
        def switch(argv, **kwargs):
            self.assertEqual(argv, [self.scope['SWITCH'], '1'])
            self.clock += elapsed
            self.card.unlink()
            return subprocess.CompletedProcess(argv, returncode, '', 'switch rejected' if returncode else '')
        def sleep(seconds):
            self.sleeps.append(seconds)
            self.clock += seconds
            if final is not None:
                self.card.write_text(final)
        with patch.object(self.scope['subprocess'], 'run', side_effect=switch), \
             patch.object(self.scope['time'], 'monotonic', side_effect=lambda: self.clock), \
             patch.object(self.scope['time'], 'sleep', side_effect=sleep), \
             patch.dict(self.g, saved=lambda: saved):
            self.scope['worker'](self.job['id'])
        return json.loads((self.scope['RUN'] / (self.job['id']+'.json')).read_text())

    def test_switch_succeeds_when_card_parameter_returns_after_command(self):
        result = self.run_worker()
        self.assertTrue(result['ok'])
        self.assertEqual(result['state'], 'done')
        self.assertEqual(len(self.sleeps), 1)

    def test_missing_parameter_has_only_remaining_operation_budget(self):
        result = self.run_worker(final=None, elapsed=179)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'WWAN interface did not return after SIM switch')
        self.assertGreaterEqual(self.clock, 180)
        self.assertLess(self.clock, 180.11)

    def test_ready_wrong_card_invalid_value_or_wrong_saved_card_remain_errors(self):
        for final, saved in [('0', '1'), ('bad', '1'), ('1', '0')]:
            self.card.write_text('0')
            result = self.run_worker(final=final, saved=saved)
            self.assertFalse(result['ok'])
            self.assertEqual(result['state'], 'error')

    def test_switch_failure_does_not_enter_reload_wait(self):
        result = self.run_worker(returncode=1)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'switch rejected')
        self.assertEqual(self.sleeps, [])

    def test_current_read_outside_worker_still_fails_immediately(self):
        self.card.unlink()
        with patch.object(self.scope['time'], 'sleep') as sleep:
            with self.assertRaises(FileNotFoundError):
                self.scope['current']()
        sleep.assert_not_called()

    def test_permission_failure_is_not_retried_as_missing_module(self):
        with patch.dict(self.g, current=Mock(
                side_effect=['0', PermissionError('permission denied')])), \
             patch.object(self.scope['subprocess'], 'run', return_value=subprocess.CompletedProcess([], 0)), \
             patch.object(self.scope['time'], 'sleep') as sleep:
            self.scope['worker'](self.job['id'])
        result = json.loads((self.scope['RUN'] / (self.job['id']+'.json')).read_text())
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'permission denied')
        sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
