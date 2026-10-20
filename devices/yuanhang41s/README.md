# 中兴远航 41S / ZTE 7546N

适配进行中，尚不是可发布的完整机型包。不得把 USB 探测通过等同于屏幕、通话、充电和蜂窝已通过。

## 原厂基准

- 设备：`P720S11 / CN_P720S11`；当前样机为 12 GiB RAM。
- Android：`ZTE/CN_P720S11/P720S11:13/TP1A.220624.014/20240508.225723:user/release-keys`。
- 原厂内核：`5.4.254-android12-9-gd0931382de8e`。
- 实际芯片：T760 / UMS9620-AB，qogirn6pro；复用 M8 的平台移植，外设分别适配。
- 当前 Android 位于 B。探测使用 A；B 槽 boot 保留。探测前将本机 B 槽的
  `uboot/sml/teecfg/trustos/vbmeta` 同步到 A，并逐项回读校验。
- userdata 实际未加密：本机 `ro.crypto.state=unsupported`，`/data` 直接来自
  `mmcblk0p76`，原始块设备可识别 F2FS；实际 userdata fstab 无 `fileencryption`
  或 metadata 加密配置。不能把其他挂载项的 `encryptable` 或 F2FS 的 encrypt
  功能位当作本机启用了数据加密。此前的加密判断已纠正。
- 本机固件、原始分区备份和设备标识留在忽略目录，不能提交或分发。

## 硬件差异与证据

| 外设 | 原厂证据 | 适配方式 / 当前状态 |
| --- | --- | --- |
| USB | qogirn6pro DWC3＋UMP9620 Type-C | NCM 双向传输、手动策略/恢复通过；用户确认普通 OTG/供电；实验性 HOST＋SINK 控制链路已验证，扩展坞数据通信待测 |
| 充电 | DT 为 `ti,bq2560x_chg@6b`，原厂 `sqc_bq2560x.ko` 实际切至 `0x1a`，日志明确为 SGM41513 | 专属分段电流编码，保留本机电池表；已注册并读回寄存器，充电器/OTG 实测待做 |
| 电量计 | `sprd,ump9620-fgu`，5000 mAh 电池，多组原厂 OCV/JEITA 表 | 补自身原厂实时 OCV 与 POCV 比较；拒绝过期样本实机生效，历史误差恢复与长时计量未验证 |
| 显示 | 720×1600，`lcd_huaying_sitronix_st7123_mipi_hdplus` | 32 位 coherent DMA＋低地址 CMA；导入 GPU 缓冲通过本机 DPU IOMMU；用户确认画面、实际 PRIME 导入扫描输出通过 |
| 屏幕偏压 | I2C6 `0x3e`，TI65132B，偏压 `0x14`，原厂启用增强模式 | 专属兼容匹配和 REG03=43，不套用其他型号的屏幕电压 |
| 触控 | SPI ST7123，IRQ13，reset14；原厂 `zte_tpd.ko` | 补齐 MCU 复位序列后，72 KiB RAM 下载和读回通过；用户确认上滑、点击正常；不烧写 flash |
| 功放 | I2C6 `0x34`，原厂只读 REG00=`0x2049`，AW883xx v1.3.0 | 保留原厂 ACF 监控保护，使用本机 IIS3 数字功放路由；用户确认 5 秒测试音和短短信提示音清晰正常 |
| 音频 DSP | 本机 `l_agdsp_a` 与 `l_agdsp_b` 哈希不同 | 提取运行中 Android 槽；`l_agdsp_a.img` 只是用户空间兼容文件名，不代表读取 A 槽 |
| 基带 | Android B 槽自己的运行库、控制程序和固件 | 已自动启动、注册；用户确认 SIM2 蜂窝上网和热点管理页正常；短信按要求跳过，通话待测 |
| 蓝牙 | 本机 PSKEY、RF 与有效本机地址，sprd-mtty | 补齐 sprdbt_tty 自动加载后，hci0 注册、上电和附近设备扫描通过；配对/音频待测 |

SGM41513 的电流编解码参考本机模块行为及 ZTE GPL 源码
`dikeckaan/zte-ums9620-kernel-5.4.254` 提交
`b50db5b6224c11db42a246e8fcdaad1e1d59f478` 下的 `sqc_v8/sqc_bq2560x.c`。
触控协议和 TI 偏压增强模式使用同一提交的 Sitronix、ZTE 驱动作参考，
与本机设备树、模块及实际固件交叉核对；该源码提交不等同于本机固件的精确构建版本。

内核原生 AW88395 驱动不能解析本机 ACF 的监控段，故未采用跳过该段的方式。
`port/aw883xx-sources.json` 固定参考源码及哈希，`audio.py` 仅作现代内核 API 兼容；
不带入参考机固件，不开启参考源码中默认关闭的校准落盘示例。

## 完整 RAM 启动中的修正

- SIPC 虚拟总线、PM、mailbox 必须先于 SIPC 消费者和音频加载。
  错序会让 PMS provider 缺失，继而使原厂 modem loader 初始化失败；
  补齐 SIPA 模块后，ModemManager 才能找到实际数据端口。
- GPU 使用本机平台的 `panfrost`；只有 fbcon 正常不代表 WebKit 能启动。
- 720×1600 面板采用 Cog 2 倍基础缩放，界面按约 360×800 逻辑尺寸布局，
  状态栏、导航、锁屏及应用一起放大；保留原生显示时序和用户的 `ui_size` 选择。
- 公共根目录准备器补齐 `usr/sbin` 等目录的可执行权限；原来的 `e5-sim`
  被复制为 0644，导致切卡报 Permission denied。本机经已有切卡 RPC 切到
  SIM2 成功，没有替换 AT 所有权、回放或切卡顺序。
- 音频从本机 `audio_route.xml`、`audio_pga.xml` 生成 UCM。
  媒体硬件增益取原厂值 25；不沿用 E5 的模拟 PA 路径和增益 39。
  输出为 LF，避免 Windows 换行使增益校验失败、音频 ready 标记缺失。
- `call-audio.py` 只覆盖本机的数字扬声器接线，听筒仍是独立模拟听筒。
  共用通话状态、音量、静音、PCM 启动和恢复流程；实机已读取全部 56 项
  控制并启动观察服务，但这不等于实际通话已验证。

此前使用有时限的 RAM 测试系统，Android B 保留为回退槽。
本机 userdata 实际未加密。已按授权保留 Android 文件，完成 100 GiB 独立
OpenWrt 分区安装；实际根设备为 mmcblk0p77，正常重启仍进入 OpenWrt。
Android B 的完整 boot 哈希与原始备份一致；详情见安装记录。

完整对齐范围及验证边界见 [公共功能清单](FEATURE-PARITY.zh-CN.md)，
调压接口逆向依据见 [CPU/温控记录](CPU-POWER.zh-CN.md)，
本机 100 GiB 保数据缩容进度及安装边界见 [内置安装记录](INSTALLATION.zh-CN.md)，
逐项实测范围及待办见 [实机验证记录](ACCEPTANCE.zh-CN.md)。

## 工作入口

- `collect.py`：识别本机并采集原厂证据、启动链；不采集 NV。
- `collect-files.py`：提取本机运行库和固件，记录 Android 槽和文件哈希。
- `prepare-kernel.py`、`kernel.config`：平台及本机外设适配；复用已有内核源码/构建目录。
- `build-kernel.py`：在调用者指定的现有 UMS9620 源码/对象目录构建，输出匹配内核与完整模块，不复制额外源码树。
- `stage.py`：公共网页和屏幕功能、网络恢复、隔离 NV、USB、关机充电等用户空间。
  未取得本机固件槽元数据时禁止启动基带；保留已有显式槽位选择。
- `make-probe.py`：有时限的 RAM 探测，启动后先恢复 Android 默认槽，再装载驱动。
  `--ram-handoff` 允许通过 USB 向 RAM 上传完整系统，校验 SHA-256 后由 PID 1
  切换到 tmpfs；测试根必须禁用自动标记启动成功，并保留限时退出服务。
- `flash-probe.py`：设备身份、备份、上一探测镜像和写入回读校验，仅更新非 Android 槽。
- `read-probe.py`：返回 Android 后读取探测槽尾部的日志。

## 已做的离线检查

- SGM41513 原厂寄存器样本、电流区间边界及 0–10000 mA 请求范围检查通过。
- 本机 Sitronix 固件 CRC24 校验通过；不代表 SPI 下载或触摸已验证。
- 公共 Android 槽选择覆盖 A/B/current、两种 cmdline 拼写、bootconfig 及无槽参数场景；
  确认固件别名和 loader 输入一致，隔离 NV 不进行跨槽别名映射。

后续需要完成蓝牙配对/音频、通话、真实充电器和扩展坞边充边用，以及完整充放电和长期
调压稳定性验证。CLI/GUI/Magisk/更新的源码入口已接入，正式 EXE/app 和发布
包尚未重新构建；不能把源码回归当作已更新旧产物。
