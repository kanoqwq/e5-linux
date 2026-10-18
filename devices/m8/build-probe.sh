#!/bin/bash
# Bounded hardware probe only; not a production/full-feature firmware.
set -euo pipefail
# Shared config disables automatic SCM suffixes; suppress the fallback '+' too.
export LOCALVERSION=
HERE=$(cd "$(dirname "$0")" && pwd)
TOP=$(cd "$HERE/../.." && pwd)
K=${M8_KERNEL:?Set M8_KERNEL to a separate pinned linux-lts-e5 worktree}
O=${M8_BUILD:?Set M8_BUILD to a Linux-native object directory}
OUT=${M8_OUT:-$TOP/out/m8/probe}
mkdir -p "$O" "$OUT"
python3 "$HERE/prepare-kernel.py" "$K"
python3 - "$TOP/upstream/e5-mainline.config" "$O/m8.config" <<'PY'
from pathlib import Path
import sys
s = Path(sys.argv[1]).read_text()
s += '''
# M8 trial: native display/audio/charging and dual-role USB peripherals.
CONFIG_LOCALVERSION="-m8-probe12"
CONFIG_OF_OVERLAY=y
CONFIG_CMDLINE="earlycon=sprd_serial,0x20210000,115200n8 console=ttyS1,115200n8 console=tty0 loglevel=7 log_buf_len=4M rdinit=/init panic=10 clk_ignore_unused pd_ignore_unused regulator_ignore_unused fw_devlink=permissive"
CONFIG_SPRD_UMS9621_CLK=n
CONFIG_SPRD_UMS9620_CLK=y
CONFIG_PINCTRL_SPRD_QOGIRN6LITE=n
CONFIG_PINCTRL_SPRD_QOGIRN6PRO=y
CONFIG_SPRD_QOGIRN6LITE_USB2_PHY=n
CONFIG_USB_MUSB_HDRC=n
CONFIG_SPRD_UMS9620_IPA_PD=y
CONFIG_USB_SPRD_UMS9620_SSPHY=y
CONFIG_USB_DWC3=y
CONFIG_USB_DWC3_GADGET=n
CONFIG_USB_DWC3_DUAL_ROLE=y
CONFIG_USB_XHCI_HCD=y
CONFIG_USB_XHCI_PLATFORM=y
CONFIG_USB_NET_DRIVERS=y
CONFIG_USB_USBNET=y
CONFIG_USB_RTL8152=y
CONFIG_USB_NET_AX8817X=y
CONFIG_USB_NET_AX88179_178A=y
CONFIG_USB_NET_CDCETHER=y
CONFIG_USB_NET_CDC_NCM=y
CONFIG_USB_NET_RNDIS_HOST=y
CONFIG_USB_NET_SMSC75XX=y
CONFIG_USB_NET_SMSC95XX=y
CONFIG_SCSI=y
CONFIG_BLK_DEV_SD=y
CONFIG_USB_STORAGE=y
CONFIG_USB_UAS=y
CONFIG_HID=y
CONFIG_HID_GENERIC=y
CONFIG_USB_HID=y
CONFIG_USB_DWC3_OF_SIMPLE=y
CONFIG_NOP_USB_XCEIV=y
CONFIG_CHARGER_AW32257=n
CONFIG_CHARGER_BQ2560X_SPRD=m
CONFIG_SPRD_THERMAL_R5P0=y
'''
s += '\n' + (Path(sys.argv[1]).parents[1] / 'devices/t551m/cpufreq/config').read_text()
Path(sys.argv[2]).write_text(s)
PY
cd "$K"
args=(O="$O" ARCH=arm64 LLVM=1 LLVM_IAS=1 CROSS_COMPILE=aarch64-linux-gnu-)
make "${args[@]}" allnoconfig > "$O/config.log"
scripts/kconfig/merge_config.sh -m -O "$O" "$O/.config" "$O/m8.config" >> "$O/config.log"
if grep -q '^config NFS_V4_1$' fs/nfs/Kconfig; then
    scripts/config --file "$O/.config" --enable NFS_V4_1
fi
make "${args[@]}" olddefconfig >> "$O/config.log"
for option in SPRD_UMS9620_CLK USB_DWC3 USB_DWC3_DUAL_ROLE USB_XHCI_PLATFORM USB_ROLE_SWITCH USB_RTL8152 USB_USBNET USB_STORAGE USB_HID USB_SPRD_UMS9620_SSPHY MMC_SDHCI_SPRD F2FS_FS DEVTMPFS; do
    grep -qx "CONFIG_${option}=y" "$O/.config" || { echo "Missing $option" >&2; exit 1; }
done
make "${args[@]}" -j"${JOBS:-8}" Image modules > "$O/build.log" 2>&1 || { tail -65 "$O/build.log"; exit 1; }
cp "$O/arch/arm64/boot/Image" "$OUT/Image"
cp "$O/.config" "$OUT/config"
cp "$O/include/config/kernel.release" "$OUT/kernel.release"
python3 "$TOP/upstream/wrap-image.py" "$OUT/Image" "$OUT/Image.lk"
sha256sum "$OUT/Image" "$OUT/Image.lk" > "$OUT/SHA256SUMS"
