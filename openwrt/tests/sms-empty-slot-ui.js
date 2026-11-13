// Render the shipped legacy inbox, without connecting to rpcd or a modem.
const fs = require('fs'), assert = require('assert/strict');
const source = fs.readFileSync(__dirname + '/../overlay/www/luci-static/resources/view/e5-sms/sms.js', 'utf8');
const E = (tag, attrs, children) => ({ tag, attrs, children });
const app = new Function('view', 'rpc', 'L', 'E', 'document', source)(
    { extend: v => v }, { declare: () => () => Promise.resolve({}) },
    { bind: (fn, self) => fn.bind(self) }, E, { createTextNode: value => value });
function text(node) {
    if (node == null) return '';
    if (Array.isArray(node)) return node.map(text).join(' ');
    return typeof node === 'object' ? text(node.children) : String(node);
}
const messages = [{id: 1, card: 0, number: '10086', text: 'saved message', direction: 'in'}];
const slots = {0: {ok: true, present: true}, 1: {ok: true, present: false, error: null}};
let rendered = app.renderList({messages, slots});
assert.match(text(rendered), /SIM2：未插卡/);
assert.match(text(rendered), /saved message/);
assert(!JSON.stringify(rendered).includes('e5-sms-error'));
slots[1] = {ok: false, error: '+CME ERROR: 13'};
assert.match(text(app.renderList({messages, slots})), /SIM2：\+CME ERROR: 13/);
slots[1] = {ok: true, present: true};
assert(!text(app.renderList({messages, slots})).includes('未插卡'));
console.log('PASS empty slot is a hint; real faults and stored messages stay visible; reinsertion clears hint');
