'use strict';
const fs = require('fs'), path = require('path'), assert = require('assert');
const plugin = path.resolve(__dirname, '../../openwrt/luci-app-t551m');
const read = p => fs.readFileSync(path.join(plugin, p), 'utf8');
const source = read('htdocs/luci-static/resources/view/mu300/locks.js');
let lang = 'en', slot = '0', confirm, finish, fail;
const requests = [], notes = [], elements = new Map();
const voices = ['volte', 'vonr'].map(voice => ({
    dataset: { voice }, disabled: true, setAttribute(k, v) { this[k] = v; }
}));
const root = {
    querySelectorAll(selector) {
        if (selector === '[data-voice]') return voices;
        return voices.filter(b => selector === '[data-voice="'+b.dataset.voice+'"]');
    },
    querySelector(selector) {
        if (!elements.has(selector)) elements.set(selector, {
            querySelectorAll: () => [], addEventListener() {}, children: [], classList: {toggle() {}}
        });
        return elements.get(selector);
    }
};
const common = new Function('rpc', 'baseclass', 'L', 'sessionStorage', read('htdocs/luci-static/resources/mu300/common.js'))(
    {declare: () => () => Promise.resolve({})}, {extend: x => x},
    {env: {get lang() { return lang; }}}, {getItem: () => slot});
let live = {volte: {enabled: true}, vonr: {enabled: false}};
const M = {
    translate: common.translate, esc: s => s, simSlot: () => slot,
    confirmBox: (title, text, options) => {
        assert.equal(options.danger, false);
        return new Promise(resolve => { confirm = resolve; });
    },
    busy: (button, on) => { button.busy = on; },
    callLockSet: (kind, val) => {
        requests.push({kind, val, slot});
        return Promise.resolve({ok: 1, id: 'job', kind});
    },
    callLockGet: () => Promise.resolve(live),
    waitLockJob: () => new Promise((resolve, reject) => { finish = resolve; fail = reject; })
};
// LuCI normally injects this separate settings widget. This suite exercises
// voice switches; keep the unrelated widget inert, as with the poll service.
const autoMode = {attach: () => ({update() {}, reload() {}})};
const page = new Function('view', 'poll', 'M', 'L', 'AutoMode', source)(
    {extend: x => x}, {add() {}}, M, {resolveDefault: x => Promise.resolve(x)}, autoMode);
const flush = async () => { for (let i=0;i<25;i++) await Promise.resolve(); };
async function main() {
    assert(source.includes('role="switch"'));
    assert(!source.includes('data-enabled='));
    assert(!source.includes('IMS ·'));
    const messages = ['状态暂不可读，请刷新后重试', '查看卡槽已改变，请重新确认操作',
        '关闭后只停止下次开机回放，已保存的网络、VoLTE 和 VoNR 设置不会被删除。',
        'VoLTE 用于 4G 通话，VoNR 用于 5G SA 通话。设置按当前查看的 SIM 保存，并跟随“开机自动应用”。',
        '关闭 VoLTE 或 VoNR 不等于屏蔽全部来电和短信，传统网络业务仍可能到达。'];
    for (lang of ['en', 'tr']) for (const message of messages)
        assert(!/[\u3400-\u9fff]/.test(common.translate(message)), lang+': '+message);
    page.note = (text, kind) => notes.push({text, kind});
    page.refresh = () => Promise.resolve();
    page.loadServing = page.loadServingMeta = () => Promise.resolve();
    page.paint = () => page.paintVoiceSwitches(page.lastLock || {});
    page.paintServing = page.paintNeighbors = () => {};
    page.wire(root);
    page.lastLock = live;
    page.paint();
    for (const [kind, val] of [['volte','off'], ['volte','on'], ['vonr','on'], ['vonr','off']]) {
        const button = voices.find(b => b.dataset.voice === kind);
        const before = requests.length;
        button.onclick();confirm(false);await flush();
        assert.equal(requests.length, before);
        button.onclick();confirm(true);await flush();
        assert.deepEqual(requests.at(-1), {kind, val, slot: '0'});
        assert.equal(button.busy, true);
        const count = requests.length;
        button.onclick();await flush();assert.equal(requests.length, count);
        live = {...live, [kind]: {enabled: val === 'on'}};
        finish();await flush();
        assert.equal(button.busy, false);
        assert.equal(button.disabled, false);
        assert.equal(button['aria-checked'], String(val === 'on'));
    }
    voices[0].onclick();slot='1';confirm(true);await flush();
    assert.match(notes.at(-1).text, /查看卡槽已改变/);
    assert.equal(requests.length, 4);
    slot='0';voices[0].onclick();confirm(true);await flush();
    fail(Error('readback mismatch'));await flush();
    assert.equal(page._applying, false);
    assert.equal(voices[0].busy, false);
    page.lastLock={volte:{enabled:null},vonr:{enabled:false}};page.paint();
    assert.equal(voices[0].disabled,true);
    const count=requests.length;voices[0].onclick();await flush();
    assert.equal(requests.length,count);
    console.log('VoLTE/VoNR switches: both directions, confirmation, busy, failed query, SIM scope and i18n passed');
}
main().catch(error => { console.error(error); process.exitCode=1; });
