"""Port V4 AP BA rearm and nonblocking 120 ms admission to SC2355 sources.

No Android offsets, firmware replacement, periodic recovery, or bandwidth cap.
The normal work lifecycle owns pending attempts and association generations.
"""
from pathlib import Path
import hashlib
import re

HERE = Path(__file__).resolve().parent
BASE = Path('drivers/unisoc_platform/sprd_wlan_combo')
ORIGINAL = {
    'sc2355_tx_prepare_addba':'c166851f3cf556967a0847d9a1379c8c5bfeda29ccde2454a68febcd28c74fcc',
    'sc2355_tx_addba':'ba26e3f05054757ed48de585ddcc563237996db59f418c1e025387370eb96eb2',
    'sc2355_tx_ba_mgmt':'49076efda0f736e7dc1821a6f73e6fa53ab16b785e0a561d373651874b8c82f0',
    'sc2355_tx_send_addba':'03f37ef426e00ae917c36165cef56b1eea9d560715d4883ab4f279ce8c6928cb',
    'sc2355_event_sta_lut':'6e18c8e1d53f783ef6d8396fff5f5122764e586fab45332592c520901974b7a2',
}


def function(source, name):
    match = re.search(r'(?m)^(?:static\s+)?[^\n;]*\b' + re.escape(name) +
                      r'\s*\([^;]*?\)\s*\{', source)
    if not match:
        raise ValueError('Missing SC2355 BA function: ' + name)
    end = source.index('\n}', match.end()) + 2
    return match.start(), end


def apply(kernel):
    root = Path(kernel) / BASE
    edits = {}
    # All eight current profiles have these exact BA bodies. Refuse an
    # unreviewed vendor/upstream implementation instead of erasing its fixes.
    marker = root / 'sc2355/ba-recovery.h'
    if not marker.is_file():
        for name, expected in ORIGINAL.items():
            text = (root / 'sc2355' / ('sdio.c' if name == 'sc2355_event_sta_lut' else 'tx.c')).read_text()
            a, b = function(text, name)
            if hashlib.sha256(text[a:b].encode()).hexdigest() != expected:
                raise ValueError('Unreviewed SC2355 BA implementation: ' + name)

    def get(name):
        return edits.get(name, (root / name).read_text())

    def replace(name, old, new, count=1):
        source = get(name)
        if new in source:
            return
        if source.count(old) != count:
            raise ValueError('Unexpected SC2355 BA anchor: ' + name + ': ' + old[:65])
        edits[name] = source.replace(old, new, count)

    members = '''	/* SC2355 BA ownership: protected by ba_lock, never held over CP IO. */
	spinlock_t ba_lock;
	u64 ba_generation[MAX_LUT_NUM];
	unsigned long ba_pending[MAX_LUT_NUM];
	unsigned long ba_rearm[MAX_LUT_NUM];
	s64 ba_last_admit;
	bool ba_gate_valid;
'''
    replace('common/hif.h', '\ts64 time[6 + 1];', '\ts64 time[8];')
    replace('common/hif.h', '\tstruct sprd_peer_entry peer_entry[MAX_LUT_NUM];',
            members + '\n\tstruct sprd_peer_entry peer_entry[MAX_LUT_NUM];')
    replace('common/hif.h', '\treturn hif->ops->init(hif);',
            '\tspin_lock_init(&hif->ba_lock);\n'
            '\tmemset(hif->ba_generation, 0, sizeof(hif->ba_generation));\n'
            '\tmemset(hif->ba_pending, 0, sizeof(hif->ba_pending));\n'
            '\tmemset(hif->ba_rearm, 0, sizeof(hif->ba_rearm));\n'
            '\thif->ba_gate_valid = false;\n\thif->ba_last_admit = 0;\n'
            '\treturn hif->ops->init(hif);')

    # A cancelled/cleaned job must release its BA claim, including jobs that
    # never reach sc2355_do_delay_work. Other work types keep a NULL callback.
    replace('common/delay_work.h', '\tu8 data[];\n};',
            '\tvoid (*release)(struct sprd_work *work);\n\tu8 data[];\n};')
    for name in ('common/delay_work.c', 'common/cfg80211.c'):
        source = get(name)
        if 'sprd_work->release(sprd_work);' not in source:
            if 'kfree(sprd_work);' not in source:
                raise ValueError('Missing work disposal: ' + name)
            source = re.sub(r'(?m)^([\t ]*)kfree\(sprd_work\);',
                            r'\1if (sprd_work->release)\n\1\tsprd_work->release(sprd_work);\n\1kfree(sprd_work);', source)
            edits[name] = source
        if source.count('sprd_work->release(sprd_work);') != source.count('kfree(sprd_work);'):
            raise ValueError('Partially adapted work disposal: ' + name)

    for name, anchor in (('sc2355/tx.c', '#include "wcn_bus.h"'),
                         ('sc2355/sdio.c', '#include "defrag.h"'),
                         ('sc2355/reorder.c', '#include "rx.h"')):
        replace(name, anchor + '\n', anchor + '\n#include "ba-recovery.h"\n')

    fragment = (HERE / 'wifi-ba-tx.c').read_text()
    tx = get('sc2355/tx.c')
    for name in ('sc2355_tx_prepare_addba', 'sc2355_tx_addba',
                 'sc2355_tx_ba_mgmt', 'sc2355_tx_send_addba'):
        start, end = function(tx, name)
        a, b = function(fragment, name)
        tx = tx[:start] + fragment[a:b] + tx[end:]
    a, b = function(fragment, 'sc2355_ba_command')
    if 'static int sc2355_ba_command(' not in tx:
        start, _ = function(tx, 'sc2355_tx_ba_mgmt')
        tx = tx[:start] + fragment[a:b] + '\n\n' + tx[start:]
    else:
        start, end = function(tx, 'sc2355_ba_command')
        tx = tx[:start] + fragment[a:b] + tx[end:]
    # Keep legacy DHCP/hang cleanup semantics while invalidating old jobs.
    tx = tx.replace('hif->peer_entry[lut_index].ba_tx_done_map = 0;',
                    'sc2355_ba_reset(hif, lut_index);')
    tx = tx.replace('hif->peer_entry[i].ba_tx_done_map = 0;',
                    'sc2355_ba_reset(hif, i);')
    tx = tx.replace('clear_bit(qos_index_2_tid(ac_index), &peer_entry->ba_tx_done_map);',
                    'sc2355_ba_deltxba(hif, peer_entry->lut_index, qos_index_2_tid(ac_index));')
    edits['sc2355/tx.c'] = tx

    source = get('sc2355/sdio.c')
    a, b = function(source, 'sc2355_event_sta_lut')
    x, y = function(fragment, 'sc2355_event_sta_lut')
    edits['sc2355/sdio.c'] = source[:a] + fragment[x:y] + source[b:]

    source = get('sc2355/reorder.c')
    start = source.index('\tcase SPRD_DELTXBA_EVENT:')
    end = source.index('\n\t\tbreak;', start)
    source = source[:start] + '''\tcase SPRD_DELTXBA_EVENT:
		sc2355_ba_deltxba(hif, ba_event->sta_lut_index, ba_event->tid);''' + source[end:]
    edits['sc2355/reorder.c'] = source

    # Do not leave declarations used only by the replaced DELTXBA branch.
    a, b = function(source, 'sc2355_wlan_ba_session_event')
    body = source[a:b].replace('\tstruct sprd_peer_entry *peer_entry = NULL;\n', '').replace('\tu8 qos_index;\n', '')
    edits['sc2355/reorder.c'] = source[:a] + body + source[b:]
    for name, text in edits.items():
        path = root / name
        if path.read_text() != text:
            path.write_text(text)
    target = root / 'sc2355/ba-recovery.h'
    data = (HERE / 'wifi-ba.h').read_bytes()
    if not target.exists() or target.read_bytes() != data:
        target.write_bytes(data)
