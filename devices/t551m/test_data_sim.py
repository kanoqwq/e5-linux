"""Run asynchronous SIM jobs against fake system commands, never a real modem."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

TOP = Path(__file__).resolve().parents[2]
TOOL = TOP / 'openwrt/luci-app-t551m/root/usr/libexec/unisoc-modem/data-sim'


class DataSim(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'card').write_text('0')
        (self.root / 'saved').write_text('0')
        self.env = dict(os.environ, E5_DATA_SIM_RUN=str(self.root / 'run'),
                        E5_DATA_SIM_CARD=str(self.root / 'card'), E5_DATA_SIM_TOOL=str(self.root / 'switch'),
                        TEST_ROOT=str(self.root), PATH=str(self.root) + ':' + os.environ['PATH'])
        self.script('uci', 'cat "$TEST_ROOT/saved"')
        self.script('ubus', 'echo \'{"up":true}\'')
        self.script('switch', 'sleep 0.2\necho "$1" >> "$TEST_ROOT/invoked"\nprintf %s "$1" > "$TEST_ROOT/card"\nprintf %s "$1" > "$TEST_ROOT/saved"')

    def script(self, name, text):
        path = self.root / name
        path.write_text('#!/bin/sh\n' + text + '\n')
        path.chmod(0o755)

    def call(self, *args, payload=None):
        return json.loads(subprocess.check_output(['python3', str(TOOL), *args], env=self.env,
                                                 input=json.dumps(payload or {}), text=True, timeout=5))

    def wait(self, job):
        for _ in range(100):
            result = self.call('status', job['id'])
            if result['state'] != 'running':
                return result
            time.sleep(.03)
        self.fail('Worker did not finish')

    def request(self, **kwargs):
        return self.call('set', payload=dict(slot='1', current='0', confirm=True, **kwargs))

    def test_switch_has_async_progress_and_verified_persistent_result(self):
        before = self.call('get')
        self.assertEqual((before['data_sim'], before['saved_sim']), (0, 0))
        job = self.request()
        self.assertEqual(job['state'], 'running')
        self.assertEqual(self.wait(job)['state'], 'done')
        after = self.call('get')
        self.assertEqual((after['data_sim'], after['saved_sim']), (1, 1))
        self.assertEqual((self.root / 'invoked').read_text(), '1\n')

    def test_same_card_is_a_noop_and_invalid_requests_never_switch(self):
        self.assertTrue(self.call('set', payload=dict(slot='0', current='0', confirm=True))['unchanged'])
        for request in [dict(slot='2', current='0', confirm=True), dict(slot='1', current='1', confirm=True),
                        dict(slot='1', current='0'), dict(slot='1', current='0', confirm='true')]:
            self.assertFalse(self.call('set', payload=request)['ok'])
        self.assertFalse((self.root / 'invoked').exists())
        self.assertFalse(self.call('status', '../invalid')['ok'])

    def test_async_wwan_reload_does_not_report_missing_sysfs_as_failure(self):
        self.script('switch', '''
printf %s "$1" > "$TEST_ROOT/saved"
rm "$TEST_ROOT/card"
(sleep .4; printf %s "$1" > "$TEST_ROOT/card") >/dev/null 2>&1 &
''')
        result = self.wait(self.request())
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['state'], 'done')
        self.assertEqual(self.call('get')['data_sim'], 1)

    def test_duplicate_is_rejected_while_worker_owns_lock(self):
        job = self.request()
        other = self.request()
        self.assertFalse(other['ok'])
        self.assertIn('already running', other['error'])
        self.assertEqual(self.wait(job)['state'], 'done')

    def test_command_failure_and_false_success_are_visible(self):
        for source in ['echo "Modem is busy" >&2; exit 1', 'exit 0']:
            self.script('switch', source)
            result = self.wait(self.request())
            self.assertFalse(result['ok'])
            self.assertEqual(result['state'], 'error')
        self.assertEqual((self.root / 'card').read_text(), '0')

    def test_interrupted_worker_is_not_reported_as_still_running(self):
        run = self.root / 'run'
        run.mkdir()
        job_id = 'a' * 32
        (run / (job_id + '.json')).write_text(json.dumps(dict(id=job_id, state='running', ok=True)))
        self.assertIn('interrupted', self.call('status', job_id)['error'])

    def test_rpc_confirmation_is_a_boolean_prototype(self):
        rpc = TOOL.parent.parent / 'rpcd/mu300dash'
        schema = json.loads(subprocess.check_output(['sh', str(rpc), 'list'], input='', text=True))
        # rpcd derives the type from the JSON value, not a string type name.
        # "Boolean" declares a string and discards/rejects actual booleans.
        for method in ('sim_set', 'traffic_clear'):
            self.assertIs(schema[method]['confirm'], False)


if __name__ == '__main__':
    unittest.main()
