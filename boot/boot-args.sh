# Read installer flags without changing the kernel's board command line.
# CONFIG_CMDLINE_FORCE hides LK's arguments from /proc/cmdline; the original
# chosen/bootargs still carries the verified boot-header request.
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
