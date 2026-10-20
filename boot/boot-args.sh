# Read installer flags without changing the kernel's board command line.
# Prefer an image-bound request; CONFIG_CMDLINE_FORCE hides LK's arguments
# from /proc/cmdline and chosen/bootargs is usable only when LK forwards them.
boot_arg_value() {
    awk -v key="$1=" '
        { for (i = 1; i <= NF; i++) if (index($i, key) == 1) {
            n++; value = substr($i, length(key) + 1)
        } }
        END { if (n == 1) print value; exit(n == 1 ? 0 : n == 0 ? 1 : 2) }
    '
}

boot_arg() {
    local key=$1 value rc
    case "$key" in e5.part_prepare|e5.root) ;; *) return 2 ;; esac
    # A verified installer-generated initramfs carries its own request. Some
    # bootloaders discard the boot header command line entirely. Never source
    # this file; it contains exactly one literal, allowlisted argument.
    if [ -e /etc/e5/boot-request ]; then
        awk 'NF != 1 || $1 !~ /^e5[.](part_prepare|root)=[A-Za-z0-9:_-]+$/ {bad=1}
             END {exit(bad || NR != 1)}' /etc/e5/boot-request || return 2
        boot_arg_value "$key" < /etc/e5/boot-request
        return $?
    fi
    if value=$(boot_arg_value "$key" < /proc/cmdline); then
        printf '%s\n' "$value"; return 0
    else
        rc=$?; [ "$rc" = 1 ] || return "$rc"
    fi
    [ -r /proc/device-tree/chosen/bootargs ] || return 1
    if value=$(tr '\000' ' ' < /proc/device-tree/chosen/bootargs | boot_arg_value "$key"); then
        printf '%s\n' "$value"
    else
        return $?
    fi
}
