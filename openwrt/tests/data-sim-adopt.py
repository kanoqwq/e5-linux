#!/usr/bin/env python3
"""Adopt the current card after an empty saved card, without modem reloads."""
import json
import os
from pathlib import Path
import runpy
import shlex
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

TOP = Path(__file__).resolve().parents[2]
CLI = TOP / 'openwrt/overlay/usr/sbin/e5-sim'
API = TOP / 'openwrt/luci-app-t551m/root/usr/libexec/unisoc-modem/data-sim'


class Adoption(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = self.root / 'state.json'
        driver = self.root / 'driver'
        driver.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
p=Path(os.environ['ADOPT_STATE']); s=json.loads(p.read_text())
name=sys.argv[1]; args=sys.argv[2:]
s['calls'].append([name,*args])
def save(): p.write_text(json.dumps(s))
save()
if name=='uci':
 if args[:2]==['-q','get']: print(s['saved'])
 elif args[0]=='set': s['staged']=args[1].split('=')[1]; save()
 elif args[0]=='commit':
  if s.get('fail_commit'): sys.exit(1)
  s['saved']=s['staged']; save()
 else: sys.exit(99)
elif name=='ubus':
 if args[-1]=='status': print(json.dumps(s['wan']))
 elif args[-2]=='notify_proto':
  assert json.loads(args[-1])=={'action':5,'available':True}
  s['wan']['available']=True; save(); print('{}')
 else: sys.exit(99)
elif name=='ifup':
 assert args==['wan']
 s['wan']['autostart']=True
 s['wan']['up']=s['wan']['available']; save()
elif name=='jsonfilter':
 value=json.load(sys.stdin)[args[-1][2:]]
 print(str(value).lower())
else: sys.exit(99)
''')
        driver.chmod(0o755)
        source = CLI.read_text()
        body = source[source.index('switch() {'):source.index('case "${1:-}" in')]
        body = body.replace('/usr/libexec/unisoc-modem/radio-allowed', 'radio_allowed')
        for service in ('modemmanager', 'e5-sipc-wwan'):
            body = body.replace('/etc/init.d/' + service, 'unexpected')
        body = body.replace('/usr/libexec/e5-apn-auto', 'unexpected')
        functions = '\n'.join(name + '() { ' + shlex.quote(str(driver)) + ' ' + name + ' "$@"; }'
                              for name in ('uci', 'ubus', 'jsonfilter', 'ifup'))
        self.script = self.root / 'switch.sh'
        self.script.write_text(functions + '''
radio_allowed() { return 0; }
card_in_use() { printf '%s\\n' "$ADOPT_ACTUAL"; }
unexpected() { echo 'Unexpected modem/network mutation' >&2; exit 99; }
at_card() { unexpected; }
ifdown() { unexpected; }
log() { :; }
''' + body + '\nswitch "$1"\n')

    def exercise(self, target='1', saved='0', fail_commit=False, **wan):
        state = dict(saved=saved, calls=[], fail_commit=fail_commit, wan=dict(up=False, pending=False,
                                                   available=False, autostart=True))
        state['wan'].update(wan)
        self.db.write_text(json.dumps(state))
        shells = [['sh'], ['bash']]
        if shutil.which('busybox'):
            shells.append(['busybox', 'sh'])
        for shell in shells:
            with self.subTest(shell=shell):
                self.db.write_text(json.dumps(state))
                result = subprocess.run(shell + [str(self.script), target], text=True,
                                        capture_output=True, timeout=5,
                                        env=dict(os.environ, ADOPT_ACTUAL=target,
                                                 ADOPT_STATE=str(self.db)))
                self.assertEqual(result.returncode, 1 if fail_commit else 0, result.stderr)
                yield json.loads(self.db.read_text())

    def test_stale_saved_card_is_persisted_and_unavailable_wan_resumed_both_directions(self):
        for target in ('0', '1'):
            for state in self.exercise(target, str(1-int(target))):
                self.assertEqual(state['saved'], target)
                self.assertTrue(state['wan']['up'])
                calls = state['calls']
                commit = next(i for i,c in enumerate(calls) if c[:2] == ['uci','commit'])
                notify = next(i for i,c in enumerate(calls) if 'notify_proto' in c)
                self.assertLess(commit, notify)
                self.assertEqual(sum(c[0]=='ifup' for c in calls), 1)

    def test_manual_data_off_connected_and_pending_are_preserved(self):
        for wan in ({'autostart':False}, {'up':True}, {'pending':True}):
            for state in self.exercise(**wan):
                self.assertEqual(state['saved'], '1')
                self.assertFalse(any(c[0]=='ifup' or 'notify_proto' in c for c in state['calls']))

    def test_matching_saved_card_is_a_true_noop(self):
        for state in self.exercise(saved='1'):
            self.assertEqual(state['calls'], [['uci','-q','get','e5-sim.main.card']])

    def test_storage_failure_never_resumes_wan(self):
        for state in self.exercise(fail_commit=True):
            self.assertEqual(state['saved'], '0')
            self.assertFalse(any(c[0]=='ifup' or 'notify_proto' in c for c in state['calls']))

    def test_available_idle_wan_needs_no_availability_override(self):
        for state in self.exercise(available=True):
            self.assertTrue(state['wan']['up'])
            self.assertFalse(any('notify_proto' in c for c in state['calls']))

    def test_api_does_not_skip_persisting_an_already_current_card(self):
        card = self.root / 'card'
        card.write_text('1')
        with patch.dict(os.environ, E5_DATA_SIM_CARD=str(card),
                        E5_DATA_SIM_RUN=str(self.root / 'jobs')):
            api = runpy.run_path(str(API))
        for saved, unchanged in [('0', False), ('1', True)]:
            with self.subTest(saved=saved), \
                 patch.dict(api['start'].__globals__, saved=lambda: saved,
                            radio_policy=lambda: [True,True]), \
                 patch.object(api['subprocess'], 'Popen') as worker:
                result = api['start'](dict(slot='1', current='1', confirm=True))
                self.assertEqual(result.get('unchanged', False), unchanged)
                self.assertEqual(worker.call_count, 0 if unchanged else 1)


if __name__ == '__main__':
    unittest.main()
