#!/usr/bin/env python3
"""Build a bounded, RAM-only 41S probe for the slot opposite stock Android."""
import argparse,hashlib,json,re,struct,subprocess,sys,zlib
from pathlib import Path
TOP=Path(__file__).resolve().parents[2]
def control(original,android,trial=False):
 b=bytearray(original)
 if len(b)!=32 or b[4:8]!=b'BCAB' or zlib.crc32(b[:28])!=struct.unpack('<I',b[28:])[0]:raise ValueError('Invalid original boot control')
 other=1-android;b[:4]=b'_a\0\0' if (other if trial else android)==0 else b'_b\0\0'
 b[12+android*2]=(14 if trial else 15)|(7<<4)|128
 # This LK demotes a trial with tries=1 before boot selection; use two.
 b[12+other*2]=15|(2<<4) if trial else 0
 b[28:]=struct.pack('<I',zlib.crc32(b[:28]));return bytes(b)
def main():
 p=argparse.ArgumentParser(description=__doc__)
 for n in ['backup','kernel','busybox','out']:p.add_argument('--'+n,type=Path,required=True)
 p.add_argument('--firmware-dir',type=Path)
 p.add_argument('--display-test',type=Path)
 p.add_argument('--ram-handoff',action='store_true',help='Enable verified RAM-only root transfer for laboratory bring-up')
 p.add_argument('--stay',type=int,default=90)
 a=p.parse_args();bb=a.busybox.read_bytes()
 if not 15<=a.stay<=600:raise ValueError('Bounded stay must be 15..600 seconds')
 if bb[:5]!=b'\x7fELF\x02':raise ValueError('Expected ELF64 BusyBox')
 phoff=struct.unpack_from('<Q',bb,32)[0];phsize,phnum=struct.unpack_from('<HH',bb,54)
 if any(struct.unpack_from('<I',bb,phoff+i*phsize)[0]==3 for i in range(phnum)):raise ValueError('This minimal probe needs the existing complete static BusyBox')
 meta=json.loads((a.backup/'manifest.json').read_text(encoding="utf-8"));props=(a.backup/'properties.txt').read_text(encoding="utf-8")
 if '[ro.product.model]: [ZTE 7546N]' not in props or '[ro.product.device]: [P720S11]' not in props:raise ValueError('Wrong stock backup')
 suffix=re.search(r'\[ro.boot.slot_suffix\]: \[_(a|b)\]',props)[1];android=ord(suffix)-ord('a');target='b' if suffix=='a' else 'a'
 for n in ['boot_'+target+'.img','misc-head.bin','cid.txt']:
  if hashlib.sha256((a.backup/n).read_bytes()).hexdigest()!=meta[n]['sha256']:raise ValueError('Backup changed: '+n)
 cid=(a.backup/'cid.txt').read_text(encoding="utf-8").strip();bc=(a.backup/'misc-head.bin').read_bytes()[2048:2080]
 a.out.parent.mkdir(parents=True,exist_ok=True);overlay=a.out.with_suffix('.overlay');(overlay/'etc').mkdir(parents=True,exist_ok=True)
 (overlay/'etc/probe-android-bc').write_bytes(control(bc,android))
 if a.firmware_dir:
  firmware_meta=json.loads((a.firmware_dir.parent/'manifest.json').read_text(encoding='utf-8'))
  if firmware_meta['android_slot']!=suffix:raise ValueError('Firmware collected from a different Android slot')
  for name,dest in [('sitronix_firmware_huaying.dump','sitronix/st7123.bin'),
                    ('aw883xx_acf.bin','aw883xx_acf.bin')]:
   content=(a.firmware_dir/name).read_bytes()
   if hashlib.sha256(content).hexdigest()!=firmware_meta['files']['/vendor/firmware/'+name]:raise ValueError('Collected firmware changed: '+name)
   dest_path=overlay/'lib/firmware'/dest;dest_path.parent.mkdir(parents=True,exist_ok=True)
   dest_path.write_bytes(content)
 if a.display_test:
  dest_path=overlay/'bin/display-test';dest_path.parent.mkdir(parents=True,exist_ok=True)
  dest_path.write_bytes(a.display_test.read_bytes());dest_path.chmod(0o755)
 trial=a.out.with_suffix('.trial.bin');trial.write_bytes(control(bc,android,True))
 text=(TOP/'upstream/init-bringup').read_text(encoding="utf-8").replace('PARTNAME=boot_b','PARTNAME=boot_'+target)
 anchor='log "userspace up: $(uname -a)"'
 prep='''
# Never mount or format userdata. Restore this handset's Android slot first.
i=0
while [ ! -r /sys/block/mmcblk0/device/cid ] && [ "$i" -lt 15 ]; do
    sleep 1; i=$((i + 1))
done
[ "$(cat /sys/block/mmcblk0/device/cid)" = "CID" ] || { reboot -f; exit 1; }
misc=
for u in /sys/class/block/mmcblk0p*/uevent; do
    grep -qx PARTNAME=misc "$u" && misc=/dev/$(basename "$(dirname "$u")")
done
[ -b "$misc" ] || { reboot -f; exit 1; }
dd if=/e5-overlay/etc/probe-android-bc of="$misc" bs=1 seek=2048 conv=notrunc &&
    dd if="$misc" of=/tmp/actual-bc bs=1 skip=2048 count=32 &&
    cmp /tmp/actual-bc /e5-overlay/etc/probe-android-bc || { reboot -f; exit 1; }
sync
log "Stock Android fallback restored and verified"
(sleep 150; persist; sync; reboot -f) &
[ ! -d /e5-overlay/lib/firmware ] || { mkdir -p /lib; ln -s /e5-overlay/lib/firmware /lib/firmware; }
while read -r module; do
    [ -n "$module" ] || continue
    insmod "/linux-modules/$module"
    log "module $module rc=$?"
done < /etc/module-order
log "drm: $(ls /sys/class/drm /sys/class/backlight 2>&1)"
log "power: $(ls /sys/class/power_supply 2>&1)"
log "input: $(cat /proc/bus/input/devices)"
[ ! -x /e5-overlay/bin/display-test ] || /e5-overlay/bin/display-test > /tmp/display-test.log 2>&1 &
persist
'''.replace('CID',cid)
 prep=prep.replace('sleep 150;',f'sleep {a.stay+60};')
 text=text.replace(anchor,anchor+prep).replace('stay=${E5_STAY:-180}',f'stay={a.stay}')
 if a.ram_handoff:
  helper=(TOP/'devices/yuanhang41s/probe-ram-root.sh').read_text()
  text=text.replace('    while [ "$t" -lt "$stay" ]',helper+'\n    while [ "$t" -lt "$stay" ]')
  text=text.replace('        sleep 30; t=$((t + 30))','        ram_handoff || :\n        sleep 2; t=$((t + 2))\n        [ $((t % 30)) -eq 0 ] || continue')
 text=text.replace('E5LINUX','YH41SPROBE'+cid[-8:]).replace('E5 Linux USB Ethernet (mainline bring-up)','Yuanhang 41S hardware probe')
 # Device-specific MACs prevent colliding with another connected E5-family board.
 mac=':'.join(cid[-8:][i:i+2] for i in range(0,8,2))
 text=text.replace('02:50:00:00:e5:01','02:50:'+mac).replace('02:50:00:00:e5:02','02:51:'+mac)
 init=a.out.with_suffix('.init');init.write_text(text)
 order=a.out.with_suffix('.modules')
 # The charger requires a registered FGU; FGU in turn imports CM helpers.
 # Retain their stock dependency chain instead of bypassing the readiness gate.
 needed=['sprd_battery_info.ko','sprd-charger-manager.ko',
         'ump96xx_fuel_gauge.ko','bq2560x-charger.ko',
         'sprd_vpu_pw_domain.ko','unisoc-iommu.ko','ocp2131.ko',
         'drm_kms_helper.ko','drm_dma_helper.ko','drm_client_lib.ko','sprd-drm.ko']
 if a.firmware_dir:needed+=['spi-sprd.ko','st7123.ko','snd-soc-aw883xx.ko']
 if a.ram_handoff:
  # Runtime providers have no ELF symbol dependency. Create the legacy SIPC
  # bus before its PM/core consumers, as in the working UMS9620 boot chain.
  # Loading the bus only after audio has pulled sipc-core in leaves loader
  # resource locks unavailable and CP memory writes fail with EBUSY/EACCES.
  needed=['sprd-sipc-virt-bus.ko']+needed+[
   'sprd_power_manager.ko','sipc-core.ko','spipe.ko',
   'sbuf_bridge.ko','sblock_bridge.ko','sprd_modem_loader.ko',
   'sprd_cp_dump.ko','sprd_time_sync.ko','sprd_time_sync_cp.ko',
   'panfrost.ko','sipa-sys.ko','sipa-core.ko','sipa-dele.ko',
   'sipa_eth.ko','sipa_usb.ko']
 available={p.stem.replace('-','_'):p for p in (a.kernel/'modules').rglob('*.ko')}
 loaded=set();visiting=set();resolved=[]
 def resolve(name):
  path=available.get(Path(name).stem.replace('-','_'))
  if not path:raise ValueError('Probe module missing: '+name)
  key=path.stem.replace('-','_')
  if key in loaded:return
  if key in visiting:raise ValueError('Module dependency cycle: '+key)
  visiting.add(key)
  dependencies=re.findall(rb'\x00depends=([^\x00]*)',path.read_bytes())
  if len(dependencies)!=1:raise ValueError('Invalid module dependency metadata: '+name)
  for dependency in dependencies[0].decode().split(','):
   if dependency:resolve(dependency)
  visiting.remove(key);loaded.add(key);resolved.append(path.name)
 for name in needed:resolve(name)
 order.write_text(''.join(n+'\n' for n in resolved))
 subprocess.run([sys.executable,str(TOP/'boot/build-boot-image.py'),'--stock-boot',str(a.backup/('boot_'+suffix+'.img')),'--misc-head',str(a.backup/'misc-head.bin'),'--kernel',str(a.kernel/'Image.lk'),'--modules',str(a.kernel/'modules'),'--module-order',str(order),'--init',str(init),'--overlay',str(overlay),'--busybox',str(a.busybox),'--cmdline','','--out',str(a.out)],check=True)
 info=json.loads(a.out.with_suffix('.json').read_text(encoding="utf-8"));info.update(profile='yuanhang41s',probe=True,android_slot=suffix,linux_slot=target,trial_hex=trial.read_bytes().hex(),fallback_hex=control(bc,android).hex(),cid_sha256=meta['cid.txt']['sha256'],stock_boot_sha256=meta['boot_'+target+'.img']['sha256'],kernel_release=(a.kernel/'kernel.release').read_text(encoding="utf-8").strip(),kernel_basis='M8 UMS9620 platform plus 41S-specific SGM41513 and TI65132B; hardware validation pending',init_sha256=hashlib.sha256(init.read_bytes()).hexdigest())
 a.out.with_suffix('.json').write_text(json.dumps(info,indent=2)+'\n')
if __name__=='__main__':main()
