#!/system/bin/sh
# No boot hooks: only an explicit Magisk action or root-shell invocation.
set -eu
MODDIR=$(cd "${0%/*}" && pwd)
case "${1:-check}" in check|reboot) ;; *) echo 'usage: switch.sh check|reboot' >&2; exit 2 ;; esac
fail() { echo "错误 / Error: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fail '需要 root / Root is required'
case "$(getprop ro.boot.slot_suffix)" in
 _a) android=a; linux=b ;; _b) android=b; linux=a ;; *) fail '无法识别 Android 槽位 / Android slot unavailable' ;;
esac
for p in boot_$linux misc l_agdsp_$android; do [ -b "/dev/block/by-name/$p" ] || fail "缺少分区 / Missing partition: $p"; done
"$MODDIR/e5-bootctl" check "$android" || exit 1
MB=/data/adb/magisk/magiskboot
[ -x "$MB" ] || fail '缺少 magiskboot'
umask 077
WORK=$(mktemp -d "$MODDIR/.check.XXXXXX")
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT
# Extract only to temporary files. Neither boot slot is written or repacked.
(cd "$WORK" && "$MB" unpack /dev/block/by-name/boot_$linux >/dev/null 2>&1 &&
 "$MB" cpio ramdisk.cpio 'extract init linux-init' >/dev/null 2>&1 &&
 "$MB" cpio ramdisk.cpio 'extract etc/misc-bc-slot-b-trial.bin linux-trial.bin' >/dev/null 2>&1) || fail '无法解析 Linux boot / Cannot unpack Linux boot'
grep -q 'ROOT_FILE=/data/e5linux/rootfs.ext4' "$WORK/linux-init" &&
 grep -q 'misc-bc-slot-b-trial.bin' "$WORK/linux-init" || fail '目标不是 e5-linux；请先安装 Linux / Not an e5-linux boot'
[ "$(dd if="$WORK/linux-trial.bin" bs=1 count=2 2>/dev/null)" = "_$linux" ] || fail 'Linux 启动角色不匹配 / Linux boot role mismatch'
# Inspect boot only. Do not mount root filesystems or alter their selectors.
echo "Linux 启动镜像已验证 / Linux boot verified"
if [ "${1:-check}" = check ]; then
 /data/adb/magisk/busybox sh "$MODDIR/bootchain.sh" check
 exit $?
fi
# Android OTA can leave the other slot's chain stale. Only after verifying
# the installed root, synchronize the five allowed partitions before arming Linux.
/data/adb/magisk/busybox sh "$MODDIR/bootchain.sh" sync || exit 1
backup="$MODDIR/boot-control-$(date +%Y%m%d-%H%M%S)-$$.bin"
"$MODDIR/e5-bootctl" arm "$backup" "$android" || exit 1
sync
cleanup
trap - EXIT
echo '正在切回 Linux / Rebooting into Linux'
/system/bin/reboot
