# 离线升级恢复旧 eSIM 页面问题

## 实机证据

E5 的 `image-version` 已是 `e5-r20261111.155212-g6e30740d-s7e1df341`，
但 `/etc/e5/release.json` 仍是 10 月 27 日记录；eSIM 的 CSS、JS 与旧版一致，
后台组件则与新源码一致。文件字节本身已是旧版，不能只用浏览器缓存解释。

前一次复核覆盖了 `device-install-image.sh` 和 `device-install-part.sh` 的
配置保留入口，却漏掉了启动 ramdisk 中 `boot/part-update.sh` 的恢复路径。
该路径只暂存新版 phone 应用，未暂存 eSIM，版本文件列表也漏了 release.json。
恢复旧 settings.tar 因而把新页面及版本记录覆盖。

## 修复

- 离线恢复先暂存新版 phone、eSIM 及完整的发行版本文件，恢复用户配置后
  重新放回；只替换新镜像实际携带的内置应用目录，并去掉旧版残留代码。
- 升级准备端的 settings.tar 同时排除上述固件管理文件。即使升级仍由旧
  启动 ramdisk 执行，也不会再次把旧页面和 release.json 回灌到新根。
- 使用实机 OpenWrt BusyBox tar 支持的 `-X` 排除清单。清单放在临时目录并
  及时清理，不进入离线事务的四份载荷及其校验清单。
- eSIM 应用版本变为 1.1.1，HTML 的 CSS/JS 链接带同版本参数，避免文件
  修复后浏览器仍复用旧静态资源。

不修改无线密码、壁纸、用户安装插件、NV 或分区布局，也不放宽设备及数据校验。

## 验证

新增真实恢复夹具先在旧代码上复现失败：新 CSS 被恢复为旧 CSS。
修复后 `boot/tests/part-update.py` 的 21 项通过，覆盖内置分区、分区 TF、
裸 TF、容量、无线配置、壁纸、自装应用、新版本记录、旧代码清理及原有 NV 流程。
夹具始终使用普通稀疏文件与合成设备路径。

`openwrt/tests/update-device-files.py` 用宿主 GNU tar 与实际 ARM64 OpenWrt
BusyBox tar 执行同一生产归档命令，核对排除内置代码及版本文件，同时保留
用户配置、壁纸及自装插件；原有本机固件收集检查通过。
`openwrt/tests/update-memory.py` 的四项内存与 NV 归档回归通过。

本机已备份后热更新 eSIM 文件，并从与本机 image-version、kernel 完全匹配的
已验证发布 ZIP 的 `files/RELEASE.json` 恢复发行记录；未手写构造版本身份。
实际本地 HTTP 返回内容逐项核对，信息屏进程运行正常，壁纸和配置摘要未变。
未刷启动分区、重启整机或操作 eUICC profile。

热修只恢复当前屏幕。升级链路修复须随重新组装的九款正式包交付；新增
ramdisk 与包内升级准备脚本均由现有构建入口收录，无需重新编译内核 Image。
