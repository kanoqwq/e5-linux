# angelcare K1 原厂比对与实机适配记录

日期：2026-10-20。本次通过指定目标的 ADB 与现有 Magisk `su` 读取 K1 原厂 Android 信息，对照仓库 `dumps/m8-stock/` 的 M8 原厂采集及当前 `devices/m8/` 实现。

## 结论

**K1 与 M8 高度同源，适合以 M8 为基础增加独立机型适配。** 这一判断来自原厂内核配置、完整设备树和实际绑定驱动，不只是外观或 Android 型号字符串。

两机的解压后内核配置逐字节一致；设备树均为 1041 个节点，节点路径集合完全相同，其中 1035 个节点的全部属性逐字节相同。剩余 6 个节点涉及设备身份、启动参数、电池/充电策略和 ILI9488 面板。两机 I²C 设备名与驱动绑定记录也逐字节相同。

这些证据支持同一 UMS9620/ums9157_2h10 设计家族的判断，尚不足以断言 PCB 料号、器件批次或所有外设固件完全相同。随后使用独立 K1 profile 编译内核并完成下文实机探测；不能将 M8 整包直接视为 K1 固件。

## 原厂版本与硬件

| 项目 | K1 本次实机 | M8 原厂参照 |
| --- | --- | --- |
| Android 型号 | `angelcare K1` | `VIPME M8` |
| 产品 / 板型 | `ums9157_2h10_native` / `ums9157_2h10` | 相同 |
| SoC | `ro.soc.model=T157`，`ro.board.platform=ums9620`；DT 根 compatible `sprd,ums9620` | T157 / UMS9620 |
| Android | 13，API 33 | 13 |
| 原厂内核 | 5.4.210，2024-08-07 21:46:36 CST | 5.4.210，2024-01-18 12:09:23 CST |
| 内核配置 | 与 M8 原厂配置字节一致 | 同左 |
| 显示 | `lcd_ili9488_ruiheng_mipi_hvga`，320×480；`lcd_id=ID9488` | `lcd_st7796u_yihua_mipi_hvga`；`lcd_id=ID7796` |
| 触控 | `tlsc6x_ts`，I²C 3-002e，实际输入设备 `tlsc6x_touch` | 相同绑定 |
| 充电 | `bq2560x_chg`，I²C 6-006b | 相同绑定 |
| 功放 | `aw87xxx_pa`，I²C 6-0058 | 相同绑定 |
| Wi-Fi 设备树 | Marlin3lite SDIO / SC2355 | 相同节点及属性 |
| USB | 枚举包含 `25100000.dwc3` | 相同控制器路径 |
| 可见内存 | MemTotal 2498340 KiB，约 2.38 GiB | 内存 DT 节点相同 |
| eMMC | 30736384 KiB，约 29.31 GiB | 相同容量记录 |
| 启动 | Android boot header v4、A/B；安装器动态识别当前槽 | 相同处理方式 |

K1 固件显示版本：`AngelCare.K1.YUAN.YD.T1.0`。系统 fingerprint 为 `UNISOC/ums9157_2h10_native/ums9157_2h10:13/TP1A.220624.014/aa08072233:user/release-keys`；vendor 构建标识为 `aa08080151`。这些是本次判断对应的原厂版本，不能外推至所有 K1 批次。

## 不能混用的差异

### 面板

K1 的活动面板由 `/sys/class/display/panel0/name` 和启动参数共同确认是 ILI9488。两份 DT 都包含 ILI9488 与 ST7796U 节点，不能仅凭“DT 中有 ST7796U”就把 K1 认成 M8 屏幕。

K1 的 ILI9488 节点还修改了初始化命令及 ESD 检查属性。适配沿用 K1 本机 DT、面板选择和命令，没有覆盖成 M8 的显示配置；实机已确认彩条和完整看板显示、触控。

### 电池与充电

K1 的原厂 DT 与 power_supply 均给出 3100000 µAh 设计容量，DT 的 `constant-charge-voltage-max-microvolt` 为 4380000。M8 分别为 3075000 µAh、4250000 µV。这里的 4.38 V 是原厂配置值，不是本次测得的充电截止电压。

电池 OCV 曲线、温度容量/内阻表、JEITA 和部分快充参数也不同。K1 删除了 M8 DT 中的 PPS 支持标记及 charge-pump 名称，并调整 Type-C sink PDO；不能据此宣称 K1 支持 M8 的 PPS/充电泵路径。

已将 K1 本机 `bq2560x-charger.ko` 与留存的 M8 原厂反汇编逐条比较：`.text` 均有 3888 条指令，指令字完全一致。两者使用 REG0E[7:1] 的 `3856 + 8 × code` mV 编码，写入时保留 bit0。K1 独立适配器采用自己的 4380 mV DT 上限，按原厂编码向下取整为 4376 mV；M8 保持 4250 → 4248 mV。K1 的 JEITA、OCV、温度表及 PDO 继续取自本机 DT，未引入 M8 的 PPS 标记。

随后最小内核探测中读到设计容量 3100000 µAh、REG0E=0x82（4376 mV）、USB 输入限流 500 mA、充电电流配置 450 mA，状态 Charging。这验证了驱动绑定及本次配置读回，尚未测量实际充满截止、电流曲线、温升或续航。

### 安装器运行时状态

userdata 是否加密是每台设备的当前状态，不属于 K1 与 M8 的机型差异。K1 profile 不记录或强制加密策略，直接沿用安装器已有的属性、实际挂载来源及映射检测；保数据缩容和需要重置的两阶段路径由运行时检查选择。本次新增回归覆盖同一 K1 型号的加密与未加密状态。

Android/OpenWrt 槽位同样由公共安装流程动态判断：当前 Android 槽受保护，另一槽用于 OpenWrt。K1 profile 不固定 A/B 角色，保留公共动态槽位实现及旧 initramfs 兼容检查。GUI、CLI 和 Magisk TF 预检均已覆盖 Android A、B；没有套用旧 M8 专用、固定槽位的短时探测脚本。

### 本机固件

已加载模块中，`mali_kbase`、`sprd_camera`、`sprd_gsp`、`sprd_wlan_combo` 的模块大小与 M8 原厂记录不同；这不足以证明具体行为差异，但说明不能将两份 vendor 构建视作完全相同。基带、Wi-Fi、蓝牙、触控和校准依赖应按 K1 本机提取，触控固件保留本机版本。未验证射频能力，不复制其他机型的网络模式表。

## 已接入的代码

`devices/k1/` 提供独立 profile、内核入口、充电适配器、构建入口和 userspace stager。复用 M8 的 UMS9620 时钟、pinctrl、USB PHY、CPU/温度、显示 DMA 与旧音频 DT 适配基础；不覆盖 K1 原厂面板和电池数据，不导入 M8 专用音量校准。M8 默认充电上限与专用配置保持原有行为。

公共 rootfs 组装、TF 打包、GUI/CLI 和 Magisk 入口已识别 K1；型号必须为 `angelcare K1`，Android 板型必须为 `ums9157_2h10`。公共 AT 路由、用户网络模式、启动重放和隔离 NV 逻辑继续共用。非零 CPU 电压偏移仍受原有 SML 固件哈希校验约束，没有把 K1 当成已验证的 M8 固件。

已完成可恢复的原厂启动相关备份和本机 vendor / WCN / 蓝牙 / DSP 文件提取，均核对传输哈希。实际启动与未完成项目见下文。

## 采集与验证范围

初始只读采集已验证：ADB 目标唯一匹配 `angelcare K1`，现有 `su` 返回 uid 0；FDT 二进制 magic/长度、gzip 解压校验通过；原厂充电模块传输 SHA256 与设备端一致；boot_b 头确认为 v4。后续补齐完整备份后才执行用户批准的试启动。

- 解压后两机内核配置 SHA256：`629919433bc42c1efa081d53d4df6eaee49794699467768b2b7c207b081f3f36`。
- 两机 I²C 名称/驱动绑定记录 SHA256：`b25f853036e5a7a568af87fe0af0dd46d51dec989d1ba1b75e702b8130f53ab2`。
- K1 原厂 `bq2560x-charger.ko`：67584 字节，SHA256 `d28c676b033b58b6fab277aa44d8e4273c3dc24d227423b432d52e7a43360119`。已完成上述原厂反汇编比对。

原始 DT、模块及脱敏文本存放在 git 忽略的 `work/angelcare-k1-audit-20261020-6dee26/`，含文件哈希清单和逐节点差异 JSON。完整备份在 `dumps/k1-stock-20261020/`，25 个文件、约 400 MiB，包含两槽启动镜像、启动链、misc 控制和完整校验清单；64 字节头本身仍不能作为恢复镜像。唯一标识不写入本报告。

## 实机启动

独立源码提交 `020b970e351eee3887e6caaf2d653926e7086e44`，使用完整 UMS9620 基线和 K1 增量，生成 `6.18.54-k1-bringup1`，234 个模块通过 cohort / 容器功能校验。输出保存在区分大小写的 Linux 文件系统；公共导出器已增加检查，避免 Windows 覆盖大小写不同的 xt 模块。

1. 最小探测：用户明确批准后，将 Android B 的 uboot/SML/teecfg/trustos 同步至 A，写入 K1 boot_a 和 misc 的 32 字节控制，进入内核后恢复并回读 Android B 回退。用户确认彩条，USB NCM 和串口可用，充电配置如上。自动返回 Android B 后，20 个启动相关分区回读通过。
2. 完整 RAM 试运行：另获用户批准，仅更新 boot_a/misc，Android B 保留。本机固件及 OpenWrt 25.12.5 用户态经 169734520 字节传输与 SHA256 校验，切至 1536 MiB 上限的 tmpfs 根。串口确认 procd 和服务启动、无 userdata/TF 挂载。原始 9 个 NV 先设只读，工作副本位于 RAM，隔离验证多次通过。补齐模块后用户确认看板和触控正常，Wi-Fi PHY 注册。约 7 分钟自动回到 Android B，开机完成且 root 可用；20 分区及 misc 回读通过。两轮日志均完整，没有 BUG / Oops / panic / CPU WARNING。
3. 修正版完整 RAM 试运行：169736204 字节载荷，SHA256 `6ad400345724a6dafe57fb8ac72cd383dc5a48f01dbf63f5cabe74490b2203b0`。S07 先保护原始 NV、按内核真实依赖加载完整模块，再进入 vendor 和看板服务。电脑自动取得 USB 地址 192.168.29.2，SSH 成功且已复核物理身份；基带被 ModemManager 识别为 V1.0.1-B7。其 sim-missing 状态与用户确认的“没有插 SIM 卡”吻合，不等于验证过插卡联网。Wi-Fi 实际扫描 21 个周边 AP，临时 WPA2 AP 在 channel 6 / HT20 进入 AP-ENABLED 后关闭；未让电脑连接热点。蓝牙 hci0 注册。完整服务下约 1.34 GiB 可用内存，多次 NV 校验通过，无 OOM 或内核故障。本轮也自动返回 Android B，开机完成且 su 正常；20 个启动相关分区与 misc 控制回读通过，持久化日志完整，内核故障计数为 0。

实机日志和回读清单保存在 `out/k1/probe1/logs/`、`out/k1/probe2/logs/`、`out/k1/probe3/logs/`（git 忽略）。试运行没有挂载或格式化 userdata/TF，没有写入原始 NV/校准数据，没有自动拨号或发送短信，也没有修改电脑网卡、路由或连接热点。保留其他任务的远航 41s、动态槽位等已有修改。

## 已知问题与待验证项

- 已修复并复测 RAM 试运行的模块/设备节点与服务启动顺序：不再把 16 个最小探测模块用于完整系统。S07 早于 vendor / 看板加载完整依赖，GPU、基带和 Wi-Fi 能在本轮自行初始化。首次出现的 EGL、`/dev/spipe_nr2` 不可用保留在原始日志中，作为失败与修正的证据。
- 已在 RAM 探测专用配置中补充本主机 MAC 的广播租约，本轮 USB DHCP 与 SSH 实机通过；没有改动普通系统的 DHCP 策略。
- S07 的版本/模块/NV 保护失败路径使用模拟模块及临时文件自动测试；不能把错误注入测试称作实机破坏性验证。
- 尚未验证：插卡识别与蜂窝注册/联网、USB 双角色、5 GHz AP、Wi-Fi 客户端连接/吞吐、蓝牙配对、音频、完整充放电、长期稳定性，以及实际安装/更新/卸载。CPU 电压偏移未放宽 SML 哈希限制。

自动化结果与复现命令见 [K1 profile 说明](../devices/k1/README.md)。当前完成 26 项 K1 自动化测试；没有编译 Windows GUI 或生成发布安装包。

## 内置安装测试包与首次反馈

随后按用户要求生成 Windows 测试包 `k1-openwrt-25.12.5-test-20261020-174504-k6.18.54`。复用相同 K1 内核，重新组装不含 RAM 试运行标记、定时重启服务或测试看门狗的干净系统；更新共享 Windows 后端和源文件对应的 ModemManager APK。包内真实 EXE 完成文件校验、协议及模拟 ADB 的安装方式隔离检查。此阶段没有对实机执行缩容。

首次 10 GiB 内置分区计划在 `prepare` 阶段被阻止：misc 前 2048 字节只有 `bootonce-bootloader`，其余字段全零。相同内容也存在于试启动之前的原厂备份中。原守卫将任意非零字节均视为 recovery/OTA，因此误报；日志显示电脑备份已完成，尚未缩容、格式化或写入本次安装的启动镜像。

修复位于共用 `openwrt/bundle/part-two-stage.py`，没有增加 K1 特判。只接受全零消息和精确的 bootloader-only 消息；任何 recovery 参数、status、stage、保留区非零、未知命令或短读继续拒绝。bootloader-only 分支还要求 Android 完成启动。先备份原 BCB、保存电脑事务并校验目标 boot，再复核完整 BCB 与 A/B 控制区哈希，只清除开头 32 字节命令，随后回读 BCB 和 A/B 控制区；失败不选择启动槽或重启。真正的恢复/升级请求不会被清除。字段含义参照 [AOSP bootloader_message](https://android.googlesource.com/platform/bootable/recovery/+/main/bootloader_message/bootloader_message.cpp)，本机是否残留的判断依据仍是上述实机日志和原始备份。

两阶段测试共 12 项通过，包括 POSIX sh / BusyBox ash 下对普通文件执行实际清除命令、保留 A/B 与厂商尾部、拒绝消息/槽位变化及无效回读，以及备份/事务先于写入、失败不重启。安装器 214 项无失败（11 项平台工具跳过），共用槽位 6 项、分区启动 9 项通过。已修补本地解压目录并通过包内真实冻结后端检查；boot/rootfs/ext4-tools 三项载荷不变，现有失败事务兼容，电脑备份未改动。未代替用户执行设备重试，实际缩容安装结果仍待验证。

## 第一阶段没有执行分区准备：第二次反馈与修复

用户随后提供 revision 238 的续装诊断：第一阶段启动后约 604 秒返回 Android B，在 `recheck` 被几何守卫阻止。只读核对确认 userdata 仍从 15224832 开始、46243840 个扇区，尚无 e5root；设备当前主、备 GPT 与该事务保存的两份原始备份逐字节一致。因此此次没有完成缩容，不能继续传输系统或绕过几何检查。

boot_a 持久化日志来自 `6.18.54-k1-bringup1`，没有分区准备阶段，只显示普通 root 搜索、加密 userdata 无法挂载及安全定时回退。所记录 `/proc/cmdline` 没有安装请求；内核使用 `CONFIG_CMDLINE_FORCE=y`。原先已经有设备树 `chosen/bootargs` 备用读取，但此次仍未触发请求。没有在失败的 Linux 启动中单独采集 chosen，因此不将“LK 具体如何丢弃参数”表述为已证明的固件行为。

公共修复让 `boot_command()` 同时在临时/最终 initramfs 中附加明确的安装请求和严格的读取器，保留原头参数便于识别；无需依赖内核命令行或 LK 转发。只解析允许的字面参数，不执行文件内容。复用动态槽位的 LZ4/newc 追加函数，保留原内核、原 ramdisk 文件、AVB 元数据和 56 MiB 处日志保留区。第一阶段仍逐项核对 CID、userdata 起点/旧容量、挂载与映射占用及事务参数，未放宽破坏性操作守卫。正式独立分区启动的 `e5.root=part` 也采用同一交接方式。

对旧版 header-only 事务提供受限重试：原设备/型号/槽位和载荷匹配，分区必须完全维持原样，无 e5root，原 boot/启动链备份完整，主备 GPT 必须与原始备份逐字节一致，且没有 recovery/bootloader 请求。先持久化 `rearming`，再写入并回读带内嵌请求的临时镜像，使用独立的 effect 名称重新选择槽位；保留原事务和原备份。已有部分分区变化、证据不足、处于系统写入阶段或新版内嵌请求仍失败时停止，不循环缩容/格式化。

自动化验证：两阶段 13 项测试（包含多种故障子用例和 BusyBox 普通文件上的实际准备脚本）、启动参数 6 项、共用槽位 6 项、入口组装 7 项通过；安装器 214 项无失败、11 项平台工具跳过。包内真实冻结后端校验安装包并验证 revision 238 兼容，实际生成并解码准备/最终 boot 的所有 initramfs 段，验证原文件保留和 Linux A 角色；用真实 GUI Adapter/Context 加模拟传输续装，确认旧 effect 不会跳过修复写入，预期重启进入 `waiting_device`。测试不访问真实设备。另有实机只读 GPT 对照，不等同于完成缩容验证。

已更新原测试包解压目录的三个共享脚本与清单，附独立 `k1-prepare-handoff-fix.zip`；没有重编 GUI、内核或系统载荷，没有改动实际续装记录，也没有替用户写分区、选择槽位或重启。修复后真实缩容及 recovery 重置流程仍需用户本轮安装验证。证据位于 git 忽略目录 `work/k1-shrink-diagnostics-20261020/`。

## 用户完成内置安装及普通通话无声修复

用户随后确认安装完成。对同一 K1 的 SSH 身份核对及只读检查显示：独立分区分配和 ext4 超级块计算容量均为 `10737418240` 字节；userdata 为 `12939427840` 字节。此前“实际缩容安装待验证”已由这次用户安装及容量回读补齐，不能据此扩展为其他机型或其他安装方式全部通过。

普通通话无声、免提有声的原因也有原厂证据：Android B 当前 ODM 的 `audio_route.xml` 使用 AO 的 handset 接线，公共默认则使用 EAR_HPL。修复以 K1 profile 的 `audio_receiver_wiring=aol` 选择 AO 听筒；保留 `Earpiece Function` 与免提 `Speaker Function` 的区别，DSP 模式仍随用户选择，模拟增益从本机 codec 的相应模式读取。M8 的共享扬声器和其他机型 HPL 路由不改动。更新收集路径补齐 `codec.xml`，避免更新后丢失本机模拟增益。

原厂数据提取、哈希、回归与实机部署边界见 [K1 说明](../devices/k1/README.md#2026-10-20-后续安装与通话验证)。此修复已在同一台 K1 无通话时应用并验证服务恢复，原文件备份于设备 `/root/k1-audio-backup-20261020-190549/`；未主动拨号、修改 NV/分区、重启设备或改动电脑网络。随后用户手动通话，明确确认“普通和免提都正常了”；实机日志中两模式均走正确 AO 路径，切换免提后 AO2/DACS0 回读与本机标定一致。蓝牙实际通话、全部音量档位及长期稳定性不在本次实机结论内。

## 无卡启动和插卡恢复

用户随后确认短信正常，并报告无卡启动后插卡需要重启。检查区分出两项缺陷：charger/shutdown 启动下，公共冷启动 helper 缺少 K1 原厂 modem_control 的精确哈希/指令 profile，导致 CP 未启动；补齐该 profile 后，无卡状态能正常导出模组。实际插卡又复现四字段 `+ECIND: 3,0,0,1` 被公共解析器丢弃，SIM busy 过后未再次确认 READY。分别修正冷启动 profile 和共享 SIM 事件解析，未用轮询或电台复位掩盖问题。

实机在更新 ModemManager r944 后，由用户从确认无卡状态插回同槽，不重启、不手动重新探测或拨号，约 20 秒内自动注册并建立 WAN；绑定蜂窝接口的外网 HTTP 测试返回 204，用户确认已识别或联网。网络配置与原厂 NV 校验通过，DEBUG 捕获已关闭。该轮只构建 ModemManager，并未重建整包；另一 MU300 仓库的相同解析限制已同步修正，但仅完成自动化验证。原厂二进制依据、各阶段边界和测试详见 [K1 说明](../devices/k1/README.md#无卡后插卡恢复) 与 [公共 SIM 事件记录](SIM-EVENTS-20261016.zh-CN.md#2026-10-20k1-扩展就绪通知)。
