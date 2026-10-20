#!/bin/sh
# Flash an e5-linux boot image into its identified Linux slot from the running system,
# over the USB management LAN.  No Android round trip.
#
# Android-side flashing needs adb+su in the current Android slot, so every
# test costs an arm-Android / reboot / wait-for-Android / flash / reboot cycle.
# Linux is already root; its boot partition is separate from the running root.
# The current Android boot is never written. A half-written Linux image is
# safe too -- LK finds no valid boot image, burns the two tries and falls back
# to Android.
#
# The device pulls over HTTP rather than the host pushing over nc: macOS nc
# truncates a piped file at the first block when it is the sender, while
# busybox wget on the device is reliable (it is how the modules and the .debs
# got there).
#
# Only the first 56 MiB are sent: boot/build-boot-image.py keeps the image
# inside that and reserves the rest of the partition for boot/init's persistent
# log, which is what the .json's sha256_head56m covers.
#
# usage: boot/flash-from-linux.sh work/boot-linux-foo.img
set -eu
IMG=$1
BASE=${IMG%.img}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
HOST=${E5_HOST:-192.168.9.1}
HTTP_PORT=${E5_HTTP_PORT:-8778}
TELNET="$ROOT/tools/e5-telnet.py"

jget() { python3 -c "import json,sys;print(json.load(open(sys.argv[1]))[sys.argv[2]])" "$BASE.json" "$1"; }

EXP=$(jget sha256)
EXP_HEAD=$(jget sha256_head56m)
HEAD_MB=$(( $(jget persist_log_offset) / 1048576 ))

[ "$(shasum -a 256 "$IMG" | cut -d' ' -f1)" = "$EXP" ] || {
    echo "local image hash mismatch" >&2; exit 1; }

say() { echo "== $*"; }
# The telnet helper appends "; echo <sentinel>", so a command may not end in a
# bare '&' -- background work has to be wrapped as "( ... &)".
dev() { python3 "$TELNET" "$1" 2>/dev/null | tr -d '\r'; }

say "checking the device is running e5-linux"
dev 'echo E5-MARK-$(cat /etc/hostname)' | grep -q 'E5-MARK-e5-linux' || {
    echo "no e5-linux on $HOST (telnet)" >&2; exit 1; }

# Historical BC filenames describe roles. Require the new image to agree
# with the running Linux role before writing either physical partition.
LINUX_SLOT=$(dev 'dd if=/run/e5linux/misc-bc-slot-b-trial.bin bs=1 count=2 2>/dev/null' | grep -oE '^_[ab]$' | cut -c2)
case "$LINUX_SLOT" in a|b) ;; *) echo 'Linux slot unavailable; nothing written' >&2; exit 1 ;; esac
[ "$(jget linux_slot)" = "$LINUX_SLOT" ] || { echo 'Boot image slot differs from this installation; nothing written' >&2; exit 1; }
say "locating boot_$LINUX_SLOT"
BOOTB=$(dev "for u in /sys/class/block/mmcblk*p*/uevent; do grep -qx PARTNAME=boot_$LINUX_SLOT \"\$u\" && echo /dev/\$(basename \$(dirname \"\$u\")); done" \
        | grep -o '/dev/mmcblk[0-9]*p[0-9]*' | head -1)
[ -n "$BOOTB" ] || { echo "Linux boot not found on the device" >&2; exit 1; }
echo "   boot_$LINUX_SLOT = $BOOTB"

# The host address on the LAN is whatever the device's DHCP server handed out,
# so ask the interface rather than hard-coding it: 192.168.9.x from br0, or
# 192.168.77.x while the host still holds the initramfs's rescue-mode lease.
LOCAL_IP=$(ifconfig 2>/dev/null | awk '/inet 192\.168\.9\./{print $2; exit}')
[ -n "$LOCAL_IP" ] || LOCAL_IP=$(ifconfig 2>/dev/null | awk '/inet 192\.168\.77\./{print $2; exit}')
[ -n "$LOCAL_IP" ] || { echo "host has no address on the device's LAN (192.168.9.0/24)" >&2; exit 1; }

SERVEDIR=$(mktemp -d)
trap 'kill "${HTTP_PID:-0}" 2>/dev/null || true; rm -rf "$SERVEDIR"' EXIT
dd if="$IMG" of="$SERVEDIR/head.img" bs=1M count="$HEAD_MB" 2>/dev/null

say "serving ${HEAD_MB} MiB on $LOCAL_IP:$HTTP_PORT"
( cd "$SERVEDIR" && exec python3 -m http.server "$HTTP_PORT" --bind "$LOCAL_IP" ) >/dev/null 2>&1 &
HTTP_PID=$!
sleep 2

say "writing $(basename "$IMG") to $BOOTB"
dev "/usr/local/bin/busybox wget -q -O - http://$LOCAL_IP:$HTTP_PORT/head.img | dd of=$BOOTB bs=1M conv=fsync 2>&1 | tail -1; sync"

say "verifying the partition"
GOT=$(dev "dd if=$BOOTB bs=1M count=$HEAD_MB 2>/dev/null | sha256sum | cut -d' ' -f1" \
      | grep -oE '^[0-9a-f]{64}' | head -1)
[ "$GOT" = "$EXP_HEAD" ] || {
    echo "Linux boot verify failed" >&2
    echo "  got      ${GOT:-<nothing>}" >&2
    echo "  expected $EXP_HEAD" >&2
    echo "Android slot is untouched" >&2
    exit 1; }
echo "   head56m ok"

# Only now is it safe to point the bootloader at slot b.  init copied both
# bootloader_control blocks into /run/e5linux at boot.
say "arming Linux slot $LINUX_SLOT"
dev 'dd if=/run/e5linux/misc-bc-slot-b-trial.bin of=$(cat /run/e5linux/misc-dev) bs=1 seek=2048 conv=notrunc 2>&1 | tail -1; sync' >/dev/null

say "rebooting into the new image"
dev 'systemctl reboot' >/dev/null 2>&1 || true
echo "done."
