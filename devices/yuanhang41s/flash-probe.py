#!/usr/bin/env python3
"""Flash only the inactive slot for a bounded 41S probe; keep Android intact."""
import argparse,hashlib,json,subprocess,struct,zlib
from pathlib import Path

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--serial',required=True);p.add_argument('--backup',type=Path,required=True);p.add_argument('--image',type=Path,required=True);p.add_argument('--reboot',action='store_true');p.add_argument('--previous-image',type=Path);a=p.parse_args()
 adb=['adb','-s',a.serial]
 def read(cmd):return subprocess.check_output(adb+['exec-out','su','-c','stty -onlcr 2>/dev/null; '+cmd],timeout=180)
 def text(cmd):return read(cmd).decode().strip()
 def digest(b):return hashlib.sha256(b).hexdigest()
 def remotehash(path):return text('sha256sum '+path).split()[0]
 if text('getprop ro.product.model')!='ZTE 7546N' or text('getprop ro.product.device')!='P720S11' or 'uid=0(root)' not in text('id'):raise ValueError('Wrong device or no root')
 backup=json.loads((a.backup/'manifest.json').read_text(encoding="utf-8"));info=json.loads(a.image.with_suffix('.json').read_text(encoding="utf-8"))
 if info.get('profile')!='yuanhang41s' or info.get('probe') is not True:raise ValueError('Not a 41S probe')
 android=info['android_slot'];linux=info['linux_slot']
 if android not in ['a','b'] or linux not in ['a','b'] or android==linux:raise ValueError('Bad slots')
 if text('getprop ro.boot.slot_suffix')!='_'+android:raise ValueError('Android slot changed')
 cid=(a.backup/'cid.txt').read_bytes()
 if digest(cid)!=backup['cid.txt']['sha256'] or digest(cid)!=info['cid_sha256'] or text('cat /sys/block/mmcblk0/device/cid')!=cid.decode().strip():raise ValueError('Different physical handset')
 if int(text('cat /sys/class/power_supply/battery/capacity'))<15:raise ValueError('Charge before the probe')
 image=a.image.read_bytes();trial=a.image.with_suffix('.trial.bin').read_bytes()
 if digest(image)!=info['sha256'] or len(image)!=int(text('blockdev --getsize64 /dev/block/by-name/boot_'+linux)):raise ValueError('Invalid image size/hash')
 if trial.hex()!=info['trial_hex'] or len(trial)!=32 or zlib.crc32(trial[:28])!=struct.unpack('<I',trial[28:])[0]:raise ValueError('Invalid trial boot control')
 if digest(a.image.with_suffix('.init').read_bytes())!=info['init_sha256']:raise ValueError('Probe init changed')
 previous=None
 if a.previous_image:
  previous=json.loads(a.previous_image.with_suffix('.json').read_text(encoding="utf-8"))
  if digest(a.previous_image.read_bytes())!=previous['sha256'] or previous['cid_sha256']!=info['cid_sha256'] or previous['linux_slot']!=linux:raise ValueError('Previous probe mismatch')
 parts=['boot_'+android,'boot_'+linux]+[name+'_'+slot for name in ['uboot','sml','teecfg','trustos','vbmeta'] for slot in ['a','b']]
 for part in parts:
  local=a.backup/(part+'.img');h=backup[part+'.img']['sha256']
  if digest(local.read_bytes())!=h:raise ValueError('Backup changed: '+part)
  if previous and part=='boot_'+linux:
   actual=text('dd if=/dev/block/by-name/'+part+' bs=1M count=56 2>/dev/null | sha256sum').split()[0]
   if actual!=previous['sha256_head56m']:raise ValueError('Previous probe head changed')
  else:
   allowed=[h]
   if previous and part.endswith('_'+linux):allowed.append(backup[part[:-1]+android+'.img']['sha256'])
   if remotehash('/dev/block/by-name/'+part) not in allowed:raise ValueError('Stock partition changed: '+part)
 original=(a.backup/'misc-head.bin').read_bytes()
 livebc=read('dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null')
 if digest(original)!=backup['misc-head.bin']['sha256']:raise ValueError('Misc backup changed')
 if previous:
  idx=ord(android)-ord('a')
  if len(livebc)!=32 or livebc[4:8]!=b'BCAB' or zlib.crc32(livebc[:28])!=struct.unpack('<I',livebc[28:])[0] or not livebc[12+2*idx]&128 or (livebc[12+2*idx]&15)<(livebc[12+2*(1-idx)]&15):raise ValueError('Expected successful Android fallback')
 elif livebc!=original[2048:2080]:raise ValueError('Boot control changed')
 remote='/data/local/tmp/yuanhang41s-probe'
 read('mkdir -p '+remote+'; chown 2000:2000 '+remote+'; chmod 700 '+remote+'; restorecon -R '+remote)
 for local,name in [(a.image,'boot.img'),(a.image.with_suffix('.trial.bin'),'trial.bin')]:
  subprocess.run(adb+['push',str(local),remote+'/'+name],check=True)
  if remotehash(remote+'/'+name)!=digest(local.read_bytes()):raise ValueError('Upload hash mismatch')
 written=[]
 try:
  for name in ['uboot','sml','teecfg','trustos','vbmeta']:
   src=name+'_'+android;dst=name+'_'+linux
   if remotehash('/dev/block/by-name/'+dst)==backup[src+'.img']['sha256']:
    print('Verified inactive '+dst+' already matches Android '+android,flush=True);continue
   written.append(dst)
   read('dd if=/dev/block/by-name/'+src+' of=/dev/block/by-name/'+dst+' bs=1M conv=fsync && sync')
   if remotehash('/dev/block/by-name/'+dst)!=backup[src+'.img']['sha256']:raise ValueError('Boot chain readback failed: '+dst)
   print('Synced inactive '+dst+' from this handset Android '+android,flush=True)
  written.append('boot_'+linux)
  read('dd if='+remote+'/boot.img of=/dev/block/by-name/boot_'+linux+' bs=4M conv=fsync && sync')
  if remotehash('/dev/block/by-name/boot_'+linux)!=digest(image):raise ValueError('Probe readback failed')
  if remotehash('/dev/block/by-name/boot_'+android)!=backup['boot_'+android+'.img']['sha256']:raise ValueError('Android boot changed')
 except Exception:
  for part in reversed(written):
   local=a.backup/(part+'.img');subprocess.run(adb+['push',str(local),remote+'/restore.img'],check=True)
   if remotehash(remote+'/restore.img')!=backup[part+'.img']['sha256']:raise RuntimeError('Restore upload failed')
   read('dd if='+remote+'/restore.img of=/dev/block/by-name/'+part+' bs=1M conv=fsync && sync')
   if remotehash('/dev/block/by-name/'+part)!=backup[part+'.img']['sha256']:raise RuntimeError('Restore readback failed')
  raise
 read('dd if='+remote+'/trial.bin of=/dev/block/by-name/misc bs=1 seek=2048 conv=notrunc,fsync && sync')
 if read('dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null')!=trial:
  local=a.image.with_suffix('.restore-bc.bin');local.write_bytes(original[2048:2080]);subprocess.run(adb+['push',str(local),remote+'/restore-bc.bin'],check=True)
  if remotehash(remote+'/restore-bc.bin')!=digest(local.read_bytes()):raise RuntimeError('Boot-control restore upload failed')
  read('dd if='+remote+'/restore-bc.bin of=/dev/block/by-name/misc bs=1 seek=2048 conv=notrunc,fsync && sync')
  if read('dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null')!=local.read_bytes():raise RuntimeError('Boot-control restoration could not be verified')
  raise RuntimeError('Trial readback failed; original boot control restored')
 print('Verified probe in '+linux+'; Android '+android+' preserved; bounded trial and automatic return; no userdata/NV changes',flush=True)
 if a.reboot:subprocess.run(adb+['reboot'],check=True)
if __name__=='__main__':main()
