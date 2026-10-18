# T512N 独立内置分区安装记录

2026-10-18。目标是本次已验证的 T512N / PB8 工程 Android，不能套用其他型号的启动镜像。

## 已完成的准备

- 已导出内存试用系统的设置、iperf3 和系统日志；正常重启回原厂 Android A，ADB root 可用。
- 再次读取确认 userdata 使用 FBE 和 metadata 加密，挂载源为 dm 映射，不能保数据直接离线缩容。
- 用户已明确同意清空 Android 用户数据，给 OpenWrt 分配 8 GiB，扩满所选分区。
- 当前 userdata 为约 21.85 GiB，计划缩至约 13.85 GiB，其余 8 GiB 建立 `e5root`。
- 已保存 GPT 主副本及 misc 头，验证主 GPT 头和分区数组 CRC；Android A 的 boot 哈希仍与原厂备份一致。
- 记录和备份位于忽略目录 `out/t512n/install-preflight/`，不包含原始 NV 数据。
- 安装器补齐工程版 root adbd 的识别，不依赖 AOSP `su` 支持 Magisk 的 `-c` 语法；图形入口原已有该能力。

## 采用公共两阶段流程

1. 电脑保留设备身份、原分区表和续装事务，先准备 T512N 专用内核/配套模块和完整安装载荷。
2. Android 下不修改正在挂载的 userdata 几何。写入备用 B 槽的离线准备镜像，回读校验，再设置试启动。
3. initramfs 校验设备 CID 和预期几何后，创建独立分区；原厂 recovery 负责清空、重建 Android data 和加密元数据。
4. 回到 Android 完成初始化、USB 调试和 root 授权后，核对新几何及事务标记，再安装完整 OpenWrt。
5. 写入、扩容和校验 `e5root`，最后更新启动镜像并切槽。实际持久启动通过后才能记为安装成功。

以上为安装开始前的准备记录，最终实机结果见下文；离线流程回归本身不代表硬件通过。
原始 NV 保持既有隔离策略，安装流程不格式化 NV 分区。

## 首次安装暴露的参数传递问题

- 第一轮进入救援环境，读取 userdata 仍为原始 45,826,048 个扇区，未执行分区修改。
- 实机 boot header v4 的 cmdline 位于偏移44；本机 LK 实际转发到 `chosen/bootargs` 的内容
  与 header 偏移64起的字符串一致：原请求丢失前20字节，仅剩 `prepare=...`。
  同时 `CONFIG_CMDLINE_FORCE=y` 使 `/proc/cmdline` 只包含固定内核参数。
- 只在 T512N 构建入口增加20字节无操作前缀，使 LK 转发完整的安装参数。
  公共 initramfs 对安装请求/独立分区选择优先读 `/proc/cmdline`，缺省时读取原始
  `chosen/bootargs`；拒绝同一来源中重复的同名选项，不接受截断后的 `prepare` 别名。
  不更换内核，不启用原厂命令行中的其他内核选项。
- 修正后的启动镜像及试启动控制信息已写入备用槽并回读校验；继续原事务、原容量和原 token。
  事务中保留修正前记录和新旧 boot 哈希，恢复备份另存到电脑用户目录 `e5-backups`。
- 新增5项真实 shell 参数解析回归；两阶段/分区发现14项回归仍通过。

## 第一次缩容成功及容量调整

- 第一轮离线准备和原厂 recovery 已实机完成，Android 正常回到 A 槽，ADB root 可用。
  userdata 为 29,048,832 扇区（约13.85 GiB），`e5root` 为16,777,216扇区（8 GiB）；
  新分区仍是匹配事务 token 的准备标记，尚未安装 OpenWrt。
- 用户随后要求 Android 只保留必要容量。新 Android 实际使用约1.3 GiB，调整目标为
  Android userdata **2 GiB**、同一个 `e5root` **约19.85 GiB**，不创建第二个根分区。
- 针对本次设备生成一次性离线计划，原 GPT、两分区几何、未安装标记和 CID 全部匹配后，
  才修改 userdata 的结束位置和原 e5root 的起点；其他分区条目、GUID、MBR 不变。
  先写副 GPT，再写主 GPT，分别回读校验；之后再次交由原厂 recovery 重建 data。
- 本次专用脚本和包含设备身份的诊断镜像只保存在忽略目录 `out/t512n/replan-2g/`。
  不加入正式安装包；正式 boot 仍使用通用、无 overlay 的启动镜像。
- 用稀疏磁盘执行了实际脚本的正常路径，以及错误 CID、已挂载 userdata、标记不符三种拒绝路径，
  确认正常路径 GPT/标记/BCB 与预期一致，拒绝路径不修改 GPT 或 misc。
- 第二轮临时启动镜像已写入并回读校验。返回 Android 后仍需验证最终几何，之后才能续装根文件系统。

## 最终安装结果

- 第二轮原厂 recovery 完成后 Android 正常启动，userdata 为4,194,304扇区（2 GiB），
  `e5root` 起点19,435,520、长度41,631,744扇区（19.8515625 GiB）。
  主副 GPT 与离线计划逐字节匹配，新的准备标记及原事务 token 一致。
- 续装暴露出公共 `su_checked()` 将完成状态紧接二进制标记输出的缺陷；已修复，
  原因和 MU300 对照见 `docs/INSTALLER-BINARY-MARKER-20261018.zh-CN.md`。
- 根镜像写入、完整回读校验、扩容、ext4 检查、本机固件提取/校验和最终 boot 写入成功，
  续装耗时约186秒，事务状态 `complete`。
- 再次直接读取 ext4 超级块，文件系统字节数与19.8515625 GiB分区完全一致。
  Android A 槽 boot 的 SHA256 仍与原厂备份相同。
- 完整恢复记录另存到电脑用户目录 `e5-backups/t512n-part-20261018-212327-bd9900b7/`。
  已从 `/dev/mmcblk0p71` 以 ext4 读写根启动，随后正常重启再次回到 OpenWrt，boot ID 确认改变。
- 默认启动为 `linux`，试启动标记已由正常运行的确认服务清除。
  USB NCM/SSH、LuCI、桌面服务、5GHz ch149/VHT80热点和 ModemManager 识别可用。
  两卡均未插入，`sim-missing` 且射频 off；不代表蜂窝拨号或通话验收通过。
  正式分区启动后的实物屏幕画面尚未重新向用户确认，内存试运行时画面/触摸已由用户确认。
- 临时测试所用 iperf3 已在持久系统补装，当前服务监听 `192.168.9.1:5201`，没有设置额外开机常驻服务。

安装时原厂 Android 内核日志中出现 SWCQ 请求超时记录，随后写入和全部回读/文件系统校验完成。
这与 RAM 试运行阶段不同：发生时运行的是原厂 Android 内核，不能归因于新 OpenWrt eMMC 驱动。
日志保存在忽略目录 `out/t512n/install/android-write-progress.txt`，原因尚未定位，不据此降低总线速率或禁用休眠。
