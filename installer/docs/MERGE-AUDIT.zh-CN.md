# E5/MU300 安装器合并审计（2026-10-30）

本次整理使用 E5 的 Android 控制端实现作为 UI/主机基础，逐项比较 MU300 的跟踪源码；不是用一个目录整体覆盖另一个目录。来源记录见 runtime/ORIGINS.json。

## 已保留的差异

1. E5 工作 NV 保留与来源记录、校验异常后输入 yes 的恢复确认和持久化 nonce 保留；MU300 适配器不能触发此流程。机内的原机 NV 同步页面与 NV 写回仍属于 E5 固件，不移植进 MU300。
2. E5 和 MU300 都支持 eMMC/UFS。E5 使用原始 READ-DESCRIPTOR 身份，MU300 保持 sysfs 描述符派生身份；不重算现有记录身份。共享 Python 存储层选择对应库；两套脚本均包含于桌面与 APK，不能因构建位置误选 E5。
3. MU300 升级 misc 支持 mmcblk 与 sdX UFS 分区，保留整盘 UFS/loop 根设备读取；E5 保留动态 Android/Linux A/B、畅行60 dtbo/vendor_boot 备份与源工具准备。
4. MU300 原有 hidden-area/container、SD、内置分区、userdata-image 升级；E5 的 TF/ext4 与两阶段分区流程继续由包内入口执行。临时启动继续仅操作 misc，不为识别安装而挂载系统根。
5. Windows 仅 MU300 有线 ADB 使用 MTP 规避；Android/无线 ADB 与 E5 不套用此措施。

## 本次补齐的公共缺口

- 主机最终校验按持久化事务里的 adapter 选存储身份库；恢复记录时不要求先在当前界面载入同一包。
- 单个包同时出现 E5 和 MU300 两种布局时拒绝选择流程，不可能通过“先匹配某个文件”选择错误 NV 策略。
- 独立运行不再读取 ../boot、../openwrt 或 ../tools 的缺失构建资源；CLI、APK、冻结桌面和 Diff 导出各自使用明确资源布局。
- Android 的 MU300 legacy ADB 调用直接进入原生 USB/TCP 传输，禁止尝试执行桌面 adb.exe；备份继续 binary dd/ADB，不改为文本编码传输。
- 导入恢复记录时兼容本机 POSIX 目录别名，保留跨 Windows/macOS/Android 的路径迁移；不改备份镜像或 NV 字节。

## 验证性质

本轮不连接真实设备。Windows/WSL 的测试使用模拟 ADB/SSH、临时 GPT/文件系统、合成发行包和原始代码入口。它们不能证明真实电源、刷写、蓝牙、通话或 NV 写回效果。正式结果与实际跳过数另记验证记录，不能把“源码回归通过”描述为“所有机型实机验收”。
