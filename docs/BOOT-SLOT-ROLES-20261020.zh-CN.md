# 动态 Android / OpenWrt 槽位修复

2026-10-20。物理 A/B 和系统角色分开：Android 在 A 就使用 B 安装 Linux；
Android 在 B 就使用 A。当前远航 41S 的实际布局是 Android B / OpenWrt A。

## 已确认的问题

1. 旧桌面入口只接受 Android A，启动链同步及多个写 boot 的位置固定为 B。
2. initramfs 的 CPIO 缺少 `etc/e5` 父目录。内核解包不会自动补父目录，
   因而 `linux-slot` 没有落地，旧的默认 B 又把诊断日志写到了 Android B。
3. 新内置根的自动识别依赖 WCN 固件导入；不早载 WCN 的机型无法走到该分支。
   41S 的独立分区实际已正确安装，但早期启动仍进入限时救援并回退 Android。

## 改动

- `boot/build-boot-image.py` 自动补 CPIO 父目录；从本机 misc 推导槽位，支持
  显式选择 Android 槽，记录 `slot_roles_version=1` 和两种角色。
- `boot/init` 从有效 trial 控制块推导 Linux 槽，核对可选的角色文件。
  角色未知或冲突时停止写诊断，不再默认为 B。
- 独立根的 `part-autoboot` 识别与 WCN 解耦；只读发现使用 `ro,noload,noatime`，
  保留显式镜像、TF、试启动和失败回退原有优先级。
- 桌面 CLI/GUI 按当前 Android 属性识别两槽；固件取当前 Android 槽。
  `l_agdsp_a.img` 仍是兼容文件名，其内容不再暗示来自 A。
- 一份公共 boot 在安装时生成临时的设备角色绑定版本：仅追加小型 initramfs
  角色段，原内核和原 ramdisk 保留。包内仍只有一份 boot/rootfs，不增加第二份载荷。
  Android B 安装遇到旧的固定槽 initramfs，会在格式化或写分区之前明确拒绝。
- 两阶段安装记录保存 Android/Linux 角色；续装会核对角色，旧记录不能静默套到反向布局。
- PC 更新读取运行中系统的两个控制块，核对 CRC/角色后绑定新 boot。
  设备侧更新按 Linux 角色找目标分区，并拒绝缺失、截断或冲突的角色信息。
- Magisk TF 入口也动态选槽；在原有临时根的 Python 中完成同样的 boot 绑定和
  固件角色生成，之后才释放/写入 TF。不会重新分区 TF 或增加 OTA 保留区。
- Android APK、Magisk 系统切换动作只检查/选择启动目标，不挂载 OpenWrt 根。
  APK 在 Android B 上选择 OpenWrt 会写 Linux A 的启动控制信息。
- MU300 仓库同步了 CPIO 父目录和未知角色拒写修复；未改其既有正常切卡、拨号或机型配置。

启动链同步仍只覆盖另一槽的 `uboot/sml/teecfg/trustos/vbmeta`，先备份、写入、
刷盘、回读；失败只恢复本次修改的目标槽。当前 Android、userdata 和 NV 不在写入集合。

## 实机核对

41S 已从 100 GiB 的 `/dev/mmcblk0p77` 永久启动，并多次正常重启。
曾受影响的 Android boot 逐块比对后，仅恢复了已确认的预留诊断尾部；
其完整 SHA-256 已恢复为原始值，并在之后的部署和重启中反复保持一致。
设备绑定的追加 ramdisk 也已实际启动验证，包含正确的 Android B / Linux A。
没有重新格式化或再次缩容 Android userdata，没有改写 NV。

## 回归范围

- 启动链真实临时文件事务：A/B 两种布局、只读检查、成功同步、部分写入失败回滚、坏 CRC。
- CPIO 实际解包、LZ4 实际解压、父目录、两槽控制块 CRC、内核/原 ramdisk 保持。
- CLI 设备检查和临时上传、两阶段安装、独立根发现及设备侧 boot 更新。
- Android APK 的 Java 控制块算术和实际 shell 的 32 字节 misc 写入。
- Magisk C 控制工具按两种 Android 角色执行真实临时文件写入。
- GUI 安装器完整测试 210 项：199 项通过，11 项因平台等条件跳过。

离线测试不等同于再次在其他机型执行缩容/重装；未为了回归重复破坏样机分区。
最终安装器 EXE/app 和正式发布包尚未重新构建，本记录不表示旧产物已经更新。
