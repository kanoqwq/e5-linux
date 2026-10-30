# Shared eMMC/UFS topology. Geometry in sysfs and public callers is always
# 512-byte sectors; only GPT encoders use the device's logical block size.
# No disk selection by enumeration order and no removable/USB disk fallback.
e5_storage_parent() {
    local node=${1##*/} path
    [ -f "${E5_STORAGE_SYS:-/sys/class/block}/$node/partition" ] || return 1
    path=$(readlink -f "${E5_STORAGE_SYS:-/sys/class/block}/$node") || return 1
    path=${path%/*}
    case ${path##*/} in mmcblk[0-9]*|sd[a-z]*) printf '%s\n' "${path##*/}" ;; *) return 1 ;; esac
}

e5_storage_controller() {
    local path
    path=$(readlink -f "${E5_STORAGE_SYS:-/sys/class/block}/${1##*/}/device") || return 1
    while [ "$path" != / ] && [ -n "$path" ]; do
        case ${path##*/} in *.ufs) printf '%s\n' "$path"; return 0 ;; esac
        path=${path%/*}
    done
    return 1
}

e5_storage_kind() {
    local disk=${1##*/}
    case "$disk" in
        mmcblk[0-9]*) [ "$(cat "${E5_STORAGE_SYS:-/sys/class/block}/$disk/device/type" 2>/dev/null)" = MMC ] && echo emmc ;;
        sd[a-z]*) e5_storage_controller "$disk" >/dev/null && echo ufs ;;
        *) return 1 ;;
    esac
}

e5_storage_identity() {
    local disk=${1##*/} kind value controller lun
    kind=$(e5_storage_kind "$disk") || return 1
    case "$kind" in
        emmc)
            value=$(cat "${E5_STORAGE_SYS:-/sys/class/block}/$disk/device/cid") || return 1
            [ "${#value}" = 32 ] || return 1
            case "$value" in *[!0-9a-f]*) return 1 ;; esac
            printf '%s\n' "$value" ;;
        ufs)
            controller=$(e5_storage_controller "$disk") || return 1
            # Android and mainline can render the same UTF16 descriptor
            # differently. Use one READ-DESCRIPTOR tool in both environments;
            # never silently fall back to a lossy serial/WWID presentation.
            value=$("${E5_UFS_ID_HELPER:-/usr/libexec/e5-ufs-id}" "$controller") || return 1
            case "$value" in ufs-descriptor-v1:*:*) ;; *) return 1 ;; esac
            lun=$(readlink -f "${E5_STORAGE_SYS:-/sys/class/block}/$disk/device") || return 1
            lun=${lun##*:}
            case "$lun" in ''|*[!0-9]*) return 1 ;; esac
            # Keep legacy transaction field width, but domain-separate UFS IDs.
            # Never use the generic SAMSUNG WWID as a unique device identity.
            printf '%s:%s' "$value" "$lun" | sha256sum | cut -c1-32 ;;
        *) return 1 ;;
    esac
}

e5_storage_partnode() {
    case "$2" in ''|0*|*[!0-9]*) return 1 ;; esac
    case "$1:$2" in
        mmcblk[0-9]*:[1-9]*) printf '%sp%s\n' "$1" "$2" ;;
        sd[a-z]*:[1-9]*) printf '%s%s\n' "$1" "$2" ;;
        *) return 1 ;;
    esac
}

e5_storage_part() {
    local label=$1 disk=${2:-} u node name key value parent found=
    for u in "${E5_STORAGE_SYS:-/sys/class/block}"/*/uevent; do
        [ -r "$u" ] || continue
        node=${u%/uevent}; node=${node##*/}
        if [ -n "$disk" ]; then
            case "$node" in "${disk}"p[0-9]*|"${disk}"[0-9]*) ;; *) continue ;; esac
        fi
        name=
        while IFS='=' read -r key value; do [ "$key" != PARTNAME ] || name=$value; done < "$u"
        [ "$name" = "$label" ] || continue
        parent=$(e5_storage_parent "$node") || continue
        if [ -n "$disk" ]; then
            [ "$parent" = "$disk" ] || continue
        else
            e5_storage_kind "$parent" >/dev/null || continue
        fi
        [ -z "$found" ] || return 1
        found=$node
    done
    [ -n "$found" ] && printf '%s\n' "$found"
}

e5_storage_disk() {
    local node disk
    node=$(e5_storage_part userdata) || return 1
    disk=$(e5_storage_parent "$node") || return 1
    e5_storage_kind "$disk" >/dev/null || return 1
    printf '%s\n' "$disk"
}

e5_storage_sector_size() {
    local value
    value=$(cat "${E5_STORAGE_SYS:-/sys/class/block}/${1##*/}/queue/logical_block_size") || return 1
    case "$value" in 512|4096) printf '%s\n' "$value" ;; *) return 1 ;; esac
}

# Mounted roots may occupy a partition or a whole removable SD card.
e5_storage_root_disk() {
    local node=${1##*/}
    if [ -f "${E5_STORAGE_SYS:-/sys/class/block}/$node/partition" ]; then
        e5_storage_parent "$node"
    else
        [ "$(cat "${E5_STORAGE_SYS:-/sys/class/block}/$node/device/type" 2>/dev/null)" = SD ] || return 1
        printf '%s\n' "$node"
    fi
}
