#!/bin/sh
# Explicit laboratory handoff. Payload, writable root and NV copies stay in RAM.
ram_handoff() {
    [ -f /tmp/ram-root.ready ] || return 0
    rm -f /tmp/ram-root.ready
    (cd /tmp && [ "$(wc -l < ram-root.sha256)" = 1 ] &&
        grep -Eq '^[0-9a-f]{64}  ram-root.tar.gz$' ram-root.sha256 &&
        sha256sum -c ram-root.sha256) || { log 'RAM payload checksum failed'; return 1; }
    mkdir -p /newroot
    mount -t tmpfs -o size=1536M,mode=0755 tmpfs /newroot || return 1
    if ! tar -xzpf /tmp/ram-root.tar.gz -C /newroot ||
       [ "$(cat /newroot/etc/e5/device-profile)" != PROBE_PROFILE ] ||
       [ ! -x /newroot/sbin/init ] ||
       [ ! -d "/newroot/lib/modules/$(uname -r)" ] ||
       [ -e /newroot/etc/rc.d/S99e5-boot-ok ] ||
       [ ! -x /newroot/etc/init.d/e5-ram-probe ]; then
        log 'RAM root identity, modules or fallback guard failed'
        umount /newroot
        return 1
    fi
    log 'Verified RAM-only OpenWrt; keeping Android fallback selected'
    persist
    # A static child holds sysrq open across switch_root, even if procd fails.
    /newroot/usr/libexec/e5-ram-watchdog 480 /newroot/tmp/probe-watchdog.ready &
    guard=$!
    i=0
    while [ ! -s /newroot/tmp/probe-watchdog.ready ] && [ "$i" -lt 20 ]; do
        sleep 0.1; i=$((i + 1))
    done
    [ -s /newroot/tmp/probe-watchdog.ready ] || { log 'RAM watchdog failed'; reboot -f; exit 1; }
    rm -f /tmp/ram-root.tar.gz
    killall display-test udhcpd telnetd 2>/dev/null || :
    for child in $(cat /proc/1/task/1/children); do
        [ "$child" = "$guard" ] || kill "$child" 2>/dev/null || :
    done
    mkdir -p /newroot/dev /newroot/sys /newroot/proc /newroot/run /newroot/config
    for path in config dev sys proc; do
        mount --move /$path /newroot/$path || { reboot -f; exit 1; }
    done
    exec switch_root /newroot /sbin/init
}
