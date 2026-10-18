#!/bin/bash
# Build the independent T512N kernel; never writes a connected device.
set -euo pipefail
# Shared config disables automatic SCM suffixes; suppress the fallback '+' too.
export LOCALVERSION=
HERE=$(cd "$(dirname "$0")" && pwd)
TOP=$(cd "$HERE/../.." && pwd)
K=${T512N_KERNEL:?Set T512N_KERNEL to an independent linux-lts-e5 checkout}
O=${T512N_BUILD:?Set T512N_BUILD to the object directory}
DEST=${T512N_OUT:-$TOP/out/t512n/kernel}
command -v pahole >/dev/null || { echo 'BTF requires pahole' >&2; exit 1; }
mkdir -p "$O" "$DEST/modules"
K=$(cd "$K" && pwd)
O=$(cd "$O" && pwd)
python3 "$HERE/prepare-kernel.py" "$K"
python3 - "$TOP" "$O/t512n.config" "${T512N_RELEASE:-0}" <<'PY'
from pathlib import Path
import sys
top, target, release = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
text = (top/'upstream/e5-mainline.config').read_text()
text = text.replace('CONFIG_LOCALVERSION="-e5"', 'CONFIG_LOCALVERSION="-t512n-bringup1"')
text = text.replace(' e5.openwrt=openwrt-mainline.ext4',
                    '' if release == '1' else ' e5.openwrt=openwrt-t512n.ext4')
text = text.replace('CONFIG_CHARGER_AW32257=m', 'CONFIG_CHARGER_AW32257=n\nCONFIG_CHARGER_BQ2560X_SPRD=m')
text = text.replace('CONFIG_SND_SOC_UNISOC_PA_AW87XXX=m', 'CONFIG_SND_SOC_UNISOC_PA_AW87XXX=n')
text += '\n' + (top/'devices/t551m/cpufreq/config').read_text()
text += '''
# T512N FT6336U, plus USB host networking/storage and common disk formats.
CONFIG_TOUCHSCREEN_TLSC6X=n
CONFIG_TOUCHSCREEN_EDT_FT5X06=m
CONFIG_SCSI=m
CONFIG_BLK_DEV_SD=m
CONFIG_USB_STORAGE=m
CONFIG_USB_UAS=m
CONFIG_EXFAT_FS=m
CONFIG_NTFS3_FS=m
CONFIG_NLS_UTF8=m
CONFIG_USB_USBNET=m
CONFIG_USB_RTL8152=m
CONFIG_USB_NET_AX8817X=m
CONFIG_USB_NET_AX88179_178A=m
CONFIG_USB_NET_CDCETHER=m
CONFIG_USB_NET_CDC_NCM=m
CONFIG_USB_NET_RNDIS_HOST=m
CONFIG_USB_NET_SMSC75XX=m
CONFIG_USB_NET_SMSC95XX=m
'''
target.write_text(text)
PY
cd "$K"
args=(O="$O" ARCH=arm64 LLVM=1 LLVM_IAS=1 CROSS_COMPILE=aarch64-linux-gnu-)
make "${args[@]}" allnoconfig
(cd "$O" && "$K/scripts/kconfig/merge_config.sh" -m -O "$O" "$O/.config" "$O/t512n.config")
if grep -q '^config NFS_V4_1$' fs/nfs/Kconfig; then
    scripts/config --file "$O/.config" --enable NFS_V4_1
fi
make "${args[@]}" olddefconfig
python3 - "$O/t512n.config" "$O/.config" "$TOP/upstream/config-ignored.txt" <<'PY'
from pathlib import Path
import sys
def values(path):
    return dict(line.split('=', 1) for line in Path(path).read_text().splitlines()
                if line.startswith('CONFIG_') and '=' in line)
wanted, actual = values(sys.argv[1]), values(sys.argv[2])
ignored_path = Path(sys.argv[3])
ignored = ignored_path.read_text().splitlines() if ignored_path.exists() else []
bad = [key for key, value in wanted.items() if actual.get(key, 'n') != value and key not in ignored]
if bad:
    raise SystemExit('Config not accepted: ' + ', '.join(bad))
PY
make "${args[@]}" -j"${JOBS:-8}" Image modules > "$O/build.log" 2>&1 || { tail -80 "$O/build.log"; exit 1; }
cp "$O/arch/arm64/boot/Image" "$O/.config" "$O/System.map" "$O/modules.builtin" "$O/modules.builtin.modinfo" "$DEST/"
mv "$DEST/.config" "$DEST/config"
cp "$O/include/config/kernel.release" "$DEST/kernel.release"
python3 "$TOP/upstream/wrap-image.py" "$DEST/Image" "$DEST/Image.lk"
make "${args[@]}" INSTALL_MOD_PATH="$O/staged" INSTALL_MOD_STRIP=1 modules_install
release=$(cat "$DEST/kernel.release")
find "$DEST/modules" -maxdepth 1 -type f -name '*.ko' -delete
find "$O/staged/lib/modules/$release" -name '*.ko' -exec cp {} "$DEST/modules/" \;
tar -C "$O/staged" -cf "$DEST/modules.tar" "lib/modules/$release"
sed -e 's/aw32257_charger.ko/bq2560x-charger.ko/' -e 's/tlsc6x.ko/edt-ft5x06.ko/' "$TOP/upstream/module-order.txt" > "$DEST/module-order.txt"
grep -qx 'edt-ft5x06.ko' "$DEST/module-order.txt" || echo 'edt-ft5x06.ko' >> "$DEST/module-order.txt"
git rev-parse HEAD > "$DEST/source-commit"
sha256sum "$DEST/Image.lk" "$DEST/modules.tar" > "$DEST/SHA256SUMS"
python3 "$TOP/upstream/check-container-support.py" "$DEST" --record
python3 "$TOP/upstream/stage-root-modules.py" "$DEST"
