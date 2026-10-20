# Sourced by Magisk's module installer. Installing never changes boot slots.
[ "$BOOTMODE" = true ] || abort '请在 Android 的 Magisk 应用内安装 / Install through the Android Magisk app'
[ "$ARCH" = arm64 ] || abort '此模块仅支持 E5 arm64 / E5 arm64 only'
case "$(getprop ro.boot.slot_suffix)" in
 _a) android=a; linux=b ;; _b) android=b; linux=a ;; *) abort 'Android slot unavailable' ;;
esac
[ -b /dev/block/by-name/boot_$linux ] && [ -b /dev/block/by-name/misc ] && [ -b /dev/block/by-name/l_agdsp_$android ] || abort '未找到 E5 所需分区 / Required E5 partitions are missing'
[ -x /data/adb/magisk/magiskboot ] || abort '缺少 magiskboot / magiskboot is missing'
set_perm "$MODPATH/e5-bootctl" 0 0 0755
set_perm "$MODPATH/action.sh" 0 0 0755
set_perm "$MODPATH/switch.sh" 0 0 0755
set_perm "$MODPATH/bootchain.sh" 0 0 0755
ui_print '安装完成后点击模块「操作」即可切回 Linux。'
ui_print 'Linux 须已安装；保留当前 SD 系统选择，试启动失败回退 Android。'
ui_print '操作时会先备份并同步另一槽的启动链；当前 Android 槽不写入。'
