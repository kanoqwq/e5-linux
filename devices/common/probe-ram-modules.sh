#!/bin/sh
# Laboratory RAM root only. Finish the normal early module pass before services.
ram_modules() {
    [ "$(cat /etc/e5/ram-probe)" = "$(cat /etc/e5/device-profile)" ] || return 1
    [ "$(uname -r)" = "$(cat /etc/e5/ram-kernel-release)" ] || return 1
    [ -s /lib/firmware/wcnmodem.bin ] && [ -s /lib/firmware/gnssmodem.bin ] || return 1
    [ -s /etc/e5/ram-module-order ] || return 1
    # Protect factory NV before any additional driver or vendor startup.
    /opt/e5/t551m-nv-prepare || return 1
    directory=/lib/modules/$(uname -r)
    while read -r module; do
        case "$module" in ''|*[!a-zA-Z0-9_.-]*|*..*) return 1 ;; esac
        [ "${module%.ko}" != "$module" ] || return 1
        [ -f "$directory/$module" ] || return 1
    done < /etc/e5/ram-module-order
    while read -r module; do
        name=$(printf '%s' "${module%.ko}" | tr - _)
        [ -d "/sys/module/$name" ] && continue
        logger -t e5-ram-probe "Loading $module"
        insmod "$directory/$module" || return 1
    done < /etc/e5/ram-module-order
}
if ! ram_modules; then
    logger -t e5-ram-probe 'RAM module preparation failed; returning to Android'
    /usr/libexec/e5-ram-probe --once || :
    sync
    reboot -f
    exit 1
fi
