#!/bin/sh
# Boot the CP (baseband) by running Android's modem_control in a chroot.
#
# Every line here was found by failure, and the order matters:
#   * /dev/__properties__ must be owned by root:root -- bionic's
#     PropertyInfoAreaFile::LoadPath() rejects a property_info whose st_uid/st_gid
#     is not 0, and then the *whole* property system stays uninitialised, so
#     modem_control cannot read ro.vendor.radio.modemtype and never boots the CP.
#   * liblog wants a logd writer socket; without /dev/socket/logdw modem_control
#     spins on connect() forever.
#   * modem_control drops to uid system (1000), so the device nodes need the
#     ownership Android's ueventd.rc gives them, and /dev/block/by-name must exist
#     (Android's ueventd creates it; devtmpfs does not).
#   * sprd_modem_loader only accepts a task whose comm is exactly "modem_control",
#     so the binary is exec'd directly instead of through linker64.
set -u
A=/opt/e5/android
[ -x "$A/vendor/bin/modem_control" ] || { echo "no Android vendor subset in $A" >&2; exit 4; }

echo "stage=ownership"
. /opt/e5/android-import-perms.sh
android_import_perms "$A" dev/__properties__ || { echo "Cannot repair imported Android permissions" >&2; exit 4; }

echo "stage=dev-nodes"
mkdir -p /dev/block/by-name /dev/socket
for u in /sys/class/block/mmcblk0p*/uevent; do
  d=$(basename "$(dirname "$u")"); n=$(sed -n 's/^PARTNAME=//p' "$u")
  ln -sfn "/dev/$d" "/dev/block/$d"
  [ -n "$n" ] && ln -sfn "/dev/$d" "/dev/block/by-name/$n"
done
# Select the Android firmware slot independently of the OpenWrt boot slot.
# Existing installations default to A; a device collected from Android B must
# explicitly select B. "current" preserves the actual bootloader slot.
modem_slot=$(cat /etc/e5/modem-slot 2>/dev/null || echo a)
case "$modem_slot" in a|b|current) ;; *) echo "invalid modem-slot" >&2; exit 4 ;; esac
if [ "$modem_slot" != "current" ]; then
  other_slot=b
  [ "$modem_slot" != "b" ] || other_slot=a
  for p in nr_modem nr_phy nr_deltanv nr_fixnv1 nr_fixnv2; do
    if [ -e "/dev/block/by-name/${p}_a" ] && [ -e "/dev/block/by-name/${p}_b" ]; then
      ln -sfn "/dev/block/by-name/${p}_${modem_slot}" "/dev/block/by-name/${p}_${other_slot}"
    fi
  done
  echo "stage=modem-slot $modem_slot (Android's own images; _$other_slot names remapped)"
fi
/opt/e5/node-perms.sh 2>/dev/null || true
chmod 0666 /sys/power/wake_lock /sys/power/wake_unlock 2>/dev/null || true

echo "stage=logd"
mkdir -p /dev/__properties__
mountpoint -q /dev/__properties__ 2>/dev/null ||
    mount --bind "$A/dev/__properties__" /dev/__properties__ 2>/dev/null
if [ ! -S /dev/socket/logdw ]; then
  mkdir -p /var/log
  # the compiled sink where there is one (the OpenWrt image has no Python,
  # and no nohup; a service manager's child needs none)
  if [ -x /opt/e5/bin/logdw ]; then
    /opt/e5/bin/logdw /dev/socket/logdw >> /var/log/e5-android-log.txt 2>&1 &
  else
    nohup python3 /opt/e5/logdw.py /dev/socket/logdw >> /var/log/e5-android-log.txt 2>&1 &
  fi
  sleep 1
fi

echo "stage=mounts"
for m in proc sys tmp data/vendor mnt system/bin linkerconfig; do mkdir -p "$A/$m"; done
elf_class=$(od -An -tu1 -j4 -N1 "$A/vendor/bin/modem_control" | tr -d ' \n')
case "$elf_class" in
  1) android_lib=lib; android_linker=linker ;;
  2) android_lib=lib64; android_linker=linker64 ;;
  *) echo "invalid modem_control ELF class" >&2; exit 4 ;;
esac
ln -sfn "/apex/com.android.runtime/bin/$android_linker" "$A/system/bin/$android_linker"
: > "$A/linkerconfig/ld.config.txt"
mountpoint -q "$A/proc" || mount -t proc proc "$A/proc"
mountpoint -q "$A/sys" || mount -t sysfs sysfs "$A/sys"
mountpoint -q "$A/dev" || mount --rbind /dev "$A/dev"

echo "stage=cmdline"
# LK's bootargs carry the slot as Unisoc spells it -- sprdboot.slot_suffix=_b -- and
# this is also what modem_control reads: with _b it loads the *other* slot's modem
# images and NV (nr_phy_b, nr_fixnv1_b, nr_deltanv_b) instead of the ones Android
# itself runs. Supply both spellings: some recipient loaders only parse the
# Unisoc name, and some read a bounded prefix of the command line.
src=/proc/cmdline
grep -qE '(android|sprd)boot\.slot_suffix' /proc/cmdline ||
    [ ! -r /proc/device-tree/chosen/bootargs ] || src=/proc/device-tree/chosen/bootargs
tr -d '\000' < "$src" > /run/e5-cmdline.android
if [ "$modem_slot" != "current" ]; then
    sed -e 's/sprdboot\.slot_suffix=_[ab]//g' \
        -e 's/androidboot\.slot_suffix=_[ab]//g' \
        /run/e5-cmdline.android > /run/e5-cmdline.without-slot
    { printf 'androidboot.slot_suffix=_%s sprdboot.slot_suffix=_%s ' \
        "$modem_slot" "$modem_slot"; cat /run/e5-cmdline.without-slot; } \
        > /run/e5-cmdline.android
    rm -f /run/e5-cmdline.without-slot
fi
chmod 0666 /sys/devices/platform/soc/*/*sipa-dele*/sipa_dele_reset 2>/dev/null || true
mountpoint -q "$A/proc/cmdline" || mount --bind /run/e5-cmdline.android "$A/proc/cmdline"

echo "stage=exec"
modem_exe=/vendor/bin/modem_control
if [ -x /opt/e5/e5-modem-coldboot ]; then
  modem_exe=$(/opt/e5/e5-modem-coldboot "$A" /run/e5-cmdline.android) || exit 5
  case "$modem_exe" in /vendor/bin/modem_control|/tmp/e5-coldboot/modem_control) ;; *) exit 5 ;; esac
  [ "$modem_exe" != /tmp/e5-coldboot/modem_control ] ||
    echo "stage=cold-modem-start verified NR/CH-only loader; live PM retained"
fi
unset LD_PRELOAD
export LD_LIBRARY_PATH=/apex/com.android.runtime/$android_lib/bionic:/system/$android_lib:/vendor/$android_lib
exec chroot "$A" "$modem_exe"
