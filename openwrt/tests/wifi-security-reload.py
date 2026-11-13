#!/usr/bin/env python3
"""Run the shipped reload decision in ucode, without radio/network access."""
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

TOP = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
patch = runpy.run_path(str(TOP/'openwrt/patch-hostapd-sae.py'))['patch_reload']
with tempfile.TemporaryDirectory(dir=TOP/'work', prefix='wifi-security-') as directory:
    root = Path(directory)
    source = Path('/home/kano/openwrt-build/base/e5/usr/share/hostap/hostapd.uc')
    target = root/'hostapd.uc'
    target.write_bytes(source.read_bytes())
    patch(root)
    expected = target.read_bytes()
    patch(root)
    assert target.read_bytes() == expected, 'not idempotent'
    text = target.read_text()
    start = text.index('\t/* sc2355-security-reload:')
    end = text.index('\n\tif (hostapd.data.pending_config[name])', start)
    decision = text[start:end]
    script = root/'check.uc'
    script.write_text('''let driver = '/sys/module/sprd_wlan_combo';
function realpath(path) { return driver; }
function is_equal(a,b) { return sprintf('%J',a) == sprintf('%J',b); }
function reload(config,old_config) {
 let phy = 'phy0';
 if (is_equal(old_config.bss,config.bss)) return true;
''' + decision + '''
 return true;
}
function bss(key,hash,extra) {
 return {bss:[{data:['wpa=2','wpa_passphrase='+key,'max_num_sta='+extra],
 hash:{wpa_psk_file:hash}}]};
}
function check(value,message) { if (!value) die(message+'\\n'); }
let old=bss('password-old','a',10);
check(!reload(bss('password-new','a',10),old),'inline password change');
check(!reload(bss('password-old','b',10),old),'PSK file change');
check(reload(old,old),'unchanged config');
check(reload(bss('password-old','a',20),old),'unrelated BSS change');
driver='/sys/module/mt76';
check(reload(bss('password-new','b',10),old),'unrelated driver');
print('PASS: password/file changes restart SC2355 BSS; unchanged and unrelated configurations stay incremental\\n');
''')
    relative = script.relative_to(TOP).as_posix()
    subprocess.run(['docker','run','--rm','--network','none','--entrypoint','ucode',
                    '-v',str(TOP)+':/src:ro','u30pro-rootfs:25.12.5',
                    '/src/'+relative],check=True)
