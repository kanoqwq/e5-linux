#!/bin/sh
# Sourced only by the explicitly enabled, bounded laboratory probe.
# All input and extraction stay in RAM; never mount userdata.
ram_handoff() {
    [ -f /tmp/ram-root.ready ] || return 0
    rm -f /tmp/ram-root.ready
    (
        cd /tmp || exit 1
        [ "$(wc -l < ram-root.sha256)" = 1 ] || exit 1
        grep -Eq '^[0-9a-f]{64}  ram-root.tar.gz$' ram-root.sha256 || exit 1
        sha256sum -c ram-root.sha256
    ) || { log 'RAM payload checksum failed'; return 1; }
    mkdir -p /newroot
    mount -t tmpfs -o size=2G,mode=0755 tmpfs /newroot || return 1
    if ! tar -xzpf /tmp/ram-root.tar.gz -C /newroot ||
       [ "$(cat /newroot/etc/e5/device-profile)" != yuanhang41s ] ||
       [ ! -x /newroot/sbin/init ] ||
       [ ! -d "/newroot/lib/modules/$(uname -r)" ]; then
        log 'RAM root extraction/identity failed'
        umount /newroot
        return 1
    fi
    # The root intentionally has no automatic slot-success service. Its only
    # successful default remains Android, restored before the probe starts.
    [ ! -e /newroot/etc/rc.d/S99e5-boot-ok ] || {
        log 'Refusing RAM root with slot-success hook'
        umount /newroot
        return 1
    }
    log 'Verified RAM root; handing PID 1 to OpenWrt'
    persist
    killall display-test udhcpd telnetd 2>/dev/null || :
    # Do not retain children with an executable/cwd in the old initramfs.
    # The trial timeout is re-established in the new root by its own service.
    for child in $(cat /proc/1/task/1/children); do
        kill "$child" 2>/dev/null || :
    done
    mkdir -p /newroot/dev /newroot/sys /newroot/proc /newroot/run /newroot/config
    mount --move /config /newroot/config || { reboot -f; exit 1; }
    mount --move /dev /newroot/dev || { reboot -f; exit 1; }
    mount --move /sys /newroot/sys || { reboot -f; exit 1; }
    mount --move /proc /newroot/proc || { reboot -f; exit 1; }
    exec switch_root /newroot /sbin/init
}
