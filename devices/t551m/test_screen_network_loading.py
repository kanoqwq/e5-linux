"""Exercise the staged screen's first network visit against asynchronous cache reads."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import runpy
import tempfile
import threading
import time
import unittest

from playwright.sync_api import sync_playwright

TOP = Path(__file__).resolve().parents[2]


class NetworkLoading(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='e5-network-screen-')
        root = Path(cls.temp.name)
        for name, text in runpy.run_path(str(TOP / 'devices/common/infoscreen/stage.py'))['sources']().items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding='utf-8')

        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(root / 'www')))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(
            channel=os.environ.get('MU300_BROWSER_CHANNEL', 'msedge'), headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def network(self):
        return {'items': [
            {'id': 'lock_state', 'type': 'info', 'label': 'Lock state', 'value': 'SIM 2', 'pending': not self.ready},
            {'id': 'lock_refresh', 'type': 'action', 'label': 'Refresh lock state', 'reload': True},
            {'id': 'net_mode', 'type': 'choice' if self.ready else 'info', 'value': 'auto' if self.ready else '--',
             'slot': '1', 'label': 'Mode', 'options': [{'value': 'auto', 'label': 'Auto'}]},
            {'id': 'nr_bands', 'type': 'multi' if self.ready else 'info', 'value': [41] if self.ready else None,
             'slot': '1', 'label': 'NR bands', 'options': [{'value': 41, 'label': 'n41'}, {'value': 78, 'label': 'n78'}]}
        ]}

    def setUp(self):
        self.ready, self.hold, self.fail = False, False, False
        self.reads, self.posts, self.held, self.errors = [], [], [], []
        self.page = self.browser.new_page(viewport={'width': 320, 'height': 480}, has_touch=True)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.status = dict(sims=[], time=int(time.time()), tz_offset=0, clock='',
                           modem={'sim_card': 0}, wan={}, traffic={'rx_rate': 0, 'tx_rate': 0, 'rx_total': 0, 'tx_total': 0},
                           wifi={}, clients=[], battery={}, usb={}, system={}, sms={'unread': []}, notifications=[],
                           screen={'idle': 0, 'awake': True, 'touch': True, 'lang': 'zh',
                                   'lock_enabled': False, 'ui_theme': 'dark'})

        def route(request):
            path = request.request.url.split('/api/', 1)[1].split('?', 1)[0]
            if request.request.method == 'POST':
                self.posts.append((path, request.request.post_data_json))
                if path == 'settings/network':
                    self.ready = True
                    data = {'ok': True, 'item': self.network()['items'][1]}
                else:
                    data = {'ok': True}
            elif path == 'status':
                data = self.status
            elif path == 'settings':
                data = {'categories': [{'id': 'network', 'label': 'Network'}]}
            elif path == 'settings/network':
                self.reads.append(path)
                if self.fail:
                    request.abort('failed')
                    return
                if self.hold:
                    self.held.append(request)
                    return
                data = self.network()
            elif path == 'plugins':
                data = {'plugins': []}
            else:
                data = {}
            request.fulfill(json=data)

        self.page.route('**/api/**', route)
        self.page.goto(f'http://127.0.0.1:{self.server.server_port}/')
        self.page.wait_for_function('last !== null && pollRequest === null')
        self.page.wait_for_timeout(700)
        self.page.evaluate('clearTimeout(pollTimer); showPage(P.settings)')
        self.page.locator('[data-st="cat:0"]').wait_for()
        self.posts.clear()

    def tearDown(self):
        self.page.close()
        self.assertFalse(self.errors, self.errors)

    def enter(self):
        self.page.locator('[data-st="cat:0"]').click()
        self.page.wait_for_function("stTop()?.view === 'cat' && stTop().items !== null && !stTop().loading")

    def tick(self):
        self.page.evaluate('readStatus()')
        self.page.wait_for_function('!stTop()?.loading')

    def test_cold_cache_becomes_editable_on_the_first_visit(self):
        self.enter()
        self.assertTrue(self.page.evaluate('stTop().networkPending'))
        self.assertIn('正在读取', self.page.locator('#st-view').inner_text())
        self.assertEqual(self.page.locator('[data-st="item:nr_bands"]').evaluate('el => el.tagName'), 'DIV')
        self.ready = True
        self.tick()
        self.assertEqual(len(self.reads), 2)
        self.assertFalse(self.page.evaluate('stTop().networkPending'))
        self.assertEqual(self.page.locator('[data-st="item:nr_bands"]').evaluate('el => el.tagName'), 'BUTTON')
        self.page.locator('[data-st="item:nr_bands"]').click()
        self.page.wait_for_function("stTop().view === 'edit'")
        self.assertTrue(self.page.evaluate("stTop().item.slot === '1' && stTop().draft.join(',') === '41'"))
        self.assertFalse(self.posts)

    def test_warm_cache_and_completed_reads_do_not_keep_refreshing(self):
        self.ready = True
        self.enter()
        for _ in range(4):
            self.tick()
        self.assertEqual(len(self.reads), 1)
        self.assertFalse(self.page.evaluate('loadState(P.settings).pending'))
        self.assertFalse(self.posts)

    def test_slow_read_is_coalesced_across_status_ticks(self):
        self.enter()
        self.hold = True
        self.page.evaluate('readStatus()')
        self.page.wait_for_function('stTop().loading')
        for _ in range(3):
            self.page.evaluate('readStatus()')
        self.assertEqual(len(self.reads), 2)
        self.ready = True
        self.held.pop().fulfill(json=self.network())
        self.page.wait_for_function('!stTop().loading && !stTop().networkPending')
        self.tick()
        self.assertEqual(len(self.reads), 2)

    def test_hidden_sleep_locked_editor_and_navigation_stop_followups(self):
        self.enter()
        for enter, leave in [
            ('showPage(P.overview)', 'showPage(P.settings)'),
            ('blank=true', 'blank=false'),
            ('window.wasLocked=T551M.isLocked; T551M.isLocked=()=>true', 'T551M.isLocked=wasLocked'),
            ("st.push({view:'edit',item:{type:'choice',options:[]}})", 'st.pop()'),
        ]:
            if enter == 'blank=true':
                self.status['screen']['awake'] = False
            self.page.evaluate(enter)
            self.tick()
            self.assertEqual(len(self.reads), 1, enter)
            self.page.evaluate(leave)
            self.status['screen']['awake'] = True
        self.hold = True
        self.page.evaluate('readStatus()')
        self.page.wait_for_function('stTop().loading')
        self.page.evaluate("st=[{view:'menu'}];stRender()")
        self.ready = True
        self.held.pop().fulfill(json=self.network())
        self.page.wait_for_timeout(50)
        self.assertEqual(self.page.evaluate('stTop().view'), 'menu')
        self.assertFalse(self.page.evaluate('loadState(P.settings).pending'))

    def test_deadline_stops_reads_and_manual_refresh_reloads_controls(self):
        self.enter()
        self.page.evaluate('stTop().networkDeadline=performance.now()-1')
        self.tick()
        self.assertEqual(len(self.reads), 1)
        self.assertFalse(self.page.evaluate('stTop().networkPending'))
        self.assertIn('读取超时', self.page.locator('#st-view').inner_text())
        self.page.evaluate("lang='en';stRender()")
        self.assertIn('Read timed out', self.page.locator('#st-view').inner_text())
        self.page.locator('[data-st="item:lock_refresh"]').click()
        self.page.wait_for_function("!stTop().loading && !stTop().saving && !stTop().networkTimeout")
        self.assertEqual(self.posts, [('settings/network', {'id': 'lock_refresh'})])
        self.assertEqual(self.page.locator('[data-st="item:nr_bands"]').evaluate('el => el.tagName'), 'BUTTON')

    def test_transport_failure_stops_loading_and_can_be_retried(self):
        self.enter()
        self.fail = True
        self.tick()
        self.assertFalse(self.page.evaluate('stTop().networkPending || loadState(P.settings).pending'))
        count = len(self.reads)
        self.tick()
        self.assertEqual(len(self.reads), count)
        self.fail = False
        self.page.locator('[data-st="item:lock_refresh"]').click()
        self.page.wait_for_function("!stTop().loading && !stTop().saving && stTop().items.find(i=>i.id==='nr_bands').type==='multi'")

    def test_refresh_does_not_reuse_an_inflight_old_snapshot(self):
        self.enter()
        self.hold = True
        self.page.evaluate('readStatus()')
        self.page.wait_for_function('stTop().loading')
        old = self.network()
        self.page.locator('[data-st="item:lock_refresh"]').click()
        self.page.wait_for_function('!stTop().saving')
        self.assertEqual(len(self.reads), 2)
        self.hold = False
        self.held.pop().fulfill(json=old)
        self.page.wait_for_function("!stTop().loading && !stTop().networkPending")
        self.assertEqual(len(self.reads), 3)
        self.assertEqual(self.page.locator('[data-st="item:nr_bands"]').evaluate('el => el.tagName'), 'BUTTON')


if __name__ == '__main__':
    unittest.main(verbosity=2)
