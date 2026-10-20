#!/usr/bin/env python3
"""Collect this handset's stock evidence and boot backups; never touch raw NV."""
import argparse,hashlib,json,shlex,subprocess
from pathlib import Path

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--serial',required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 def read(cmd):
  return subprocess.check_output(['adb','-s',a.serial,'exec-out','su','-c','stty -onlcr 2>/dev/null; '+cmd],timeout=180)
 assert read('getprop ro.product.model').strip()==b'ZTE 7546N','Different model'
 assert read('getprop ro.product.device').strip()==b'P720S11','Different board'
 assert b'uid=0(root)' in read('id'),'Root required'
 a.out.mkdir(parents=True,exist_ok=True);manifest={}
 def save(name,cmd,verify_path=None):
  data=read(cmd);h=hashlib.sha256(data).hexdigest()
  if verify_path:assert read('sha256sum '+shlex.quote(verify_path)).decode().split()[0]==h, name
  (a.out/name).write_bytes(data);manifest[name]=dict(bytes=len(data),sha256=h)
  (a.out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8');print(name,len(data),flush=True)
 commands={
 'properties.txt':'getprop','uname.txt':'uname -a','cid.txt':'cat /sys/block/mmcblk0/device/cid','cmdline.txt':'cat /proc/cmdline',
 'bootconfig.txt':'cat /proc/bootconfig 2>/dev/null; true','modules.txt':'cat /proc/modules','inputs.txt':'cat /proc/bus/input/devices',
 'partitions.txt':'ls -l /dev/block/by-name; cat /proc/partitions','mounts.txt':'cat /proc/mounts; df -h /data',
 'dmesg.txt':'dmesg','live.dtb':'cat /sys/firmware/fdt','config.gz':'cat /proc/config.gz 2>/dev/null; true',
 'i2c.txt':'for d in /sys/bus/i2c/devices/*; do echo "$d"; cat "$d/name" 2>/dev/null; readlink "$d/driver"; done; true',
 'power.txt':'for d in /sys/class/power_supply/*; do echo "$d"; cat "$d/uevent"; done',
 'display.txt':'for d in /sys/class/backlight/*; do echo "$d"; cat "$d/brightness" "$d/max_brightness"; done; dumpsys SurfaceFlinger --display-id; wm size; wm density',
 'crypto.txt':'getprop ro.crypto.state; getprop ro.crypto.type; getprop ro.crypto.metadata.enabled; cat /vendor/etc/fstab*',
 'module-files.txt':'find /vendor/lib/modules /vendor_dlkm/lib/modules -type f 2>/dev/null; true',
 'runtime-files.txt':'ls -l /vendor/bin/modem_control /vendor/bin/cp_diskserver /vendor/bin/refnotify /vendor/lib/libimpl-ril.so /vendor/lib64/libimpl-ril.so; ls /vendor/firmware; true',
 'sound.txt':'cat /proc/asound/cards; cat /proc/asound/pcm; ls /vendor/etc/*audio*; true',
 'cpu.txt':'for p in /sys/devices/system/cpu/cpufreq/policy*; do echo "$p"; cat "$p/scaling_driver" "$p/scaling_available_frequencies" "$p/scaling_governor"; done; true',
 }
 for n,c in commands.items():save(n,c)
 for part in ('boot_a','boot_b','vendor_boot_a','vendor_boot_b','init_boot_a','init_boot_b','dtb_a','dtb_b','dtbo_a','dtbo_b','vbmeta_a','vbmeta_b','sml_a','sml_b','teecfg_a','teecfg_b','trustos_a','trustos_b','uboot_a','uboot_b'):
  path='/dev/block/by-name/'+part
  if read('test -e '+path+' && echo yes || true').strip()!=b'yes':continue
  save(part+'.img','cat '+path,path)
 save('misc-head.bin','dd if=/dev/block/by-name/misc bs=4096 count=1 2>/dev/null')
 # File-based driver and init references only; exclude modem partition contents/NV.
 save('vendor-reference.tar','cd /; tar -cf - vendor/lib/modules vendor/etc/init vendor/etc/fstab* vendor/etc/audio* vendor/lib64/libimpl-ril.so vendor/lib/libimpl-ril.so vendor/bin/modem_control vendor/bin/refnotify vendor/bin/cp_diskserver 2>/dev/null; true')
if __name__=='__main__':main()
