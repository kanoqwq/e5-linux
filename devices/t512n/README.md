# T512N / PB8 适配

独立 device，复用现有 E5/T551M/M8 公共功能；当前为 bring-up，已完成内存试运行，
并实机安装到独立内置分区：Android userdata 保留2 GiB，OpenWrt分区约19.85 GiB。
已从内置分区启动，完整流程和原厂引导器参数差异见 `INSTALL-NOTES.zh-CN.md`。
工作分支 `feat/t512n-bringup`。原始固件、设备树、驱动和恢复镜像保存在忽略的
`dumps/t512n-stock-20261018/`，不进入发布包或 Git。

原厂实机：`TCL/PB8_100A_FIH/PB8:13/TP1A.220624.014/512NCA05:userdebug/release-keys`，
内核 `5.15.119-android13-8-g10da4d9c53de-ab1`。`adb root` 可直接使用。
启动属性上报 locked/green，但机主已确认本机实际不校验 AVB；不将该属性作为阻止适配的依据，
也没有为此更改锁状态或格式化用户数据。

| 部分 | 本机证据与采用的实现 |
| --- | --- |
| SoC | UMS9621 / qogirn6lite。公共基线与 T551M 相同，单独的源码工作树和输出目录。 |
| 厂商程序 | `modem_control` 为 AArch64 ELF64，与 E5 的 64 位运行环境更接近；共享 android-run 按 ELF class 选择 lib/lib64，不能强套 T551M 的 32 位程序。 |
| 基带启动 | 复用 E5 stage_nv 的准备握手、cmdline/bootconfig 槽位一致性与本机隔离工作副本；保留 AT owner 和回放顺序，不读写原始 NV 做分析。 |
| 基带固件 | 本机 `5G_MODEM_V2_23B_W23.39.3`；不导入 T551M 的 152 工作模式与 LTE 候选覆盖。后续两卡已识别注册，切换上网卡均能获取 IPv4；用户确认短信、电话测试正常。 |
| 显示 | `lcd_ili9488_easyquick_mipi_fhd`，DRM/getevent 坐标 320×480；接近 T551M，但仍使用本机 DT 面板命令与时序。 |
| 背光 | GPIO10、`sprd,qogirn6l-backlight`，使用已有脉冲背光适配，不使用 E5 原生 PWM 假设。 |
| 触摸 | I²C3 0x38，FT6336U、IC ID 0x6426、固件 07、两点触摸。采用主线 EDT/FT6236 协议，加本机 DT 命名/坐标桥接，不移植自动刷写固件功能。用户已确认画面和上滑操作正常，反复息屏/唤醒待测。 |
| 充电 | I²C6 0x6b，原厂日志 REG0B=0x11，与 T551M 的 0x12 不同。按本机原厂模块反汇编实现电流编码，原厂 500mA 请求对应 REG02=0x88；不能使用 T551M 90mA 步长。 |
| 电池 | 2965mAh、恒压上限 4.4V、终止电流60mA，欠压 DT 3.10V。本机 OCV/温度/JEITA/remap 全表保留，不注入其他机型的 DCP 表或充电电流覆盖。 |
| Wi-Fi/BT | Marlin3Lite_AB_0x2355B001 / SDIO。复用公共发送与生命周期修复；固件、板级 ini、MAC 必须来自本机。 |
| 音频 | UMP9620 codec，原厂实际走 GPIO 脉冲 PA：DT `sprd,spk-ext-pa-info=<0 0 4 0>`，日志确认 `hook_general_spk ... mode:4`。UCP1301 模块虽然加载，但对应 I²C 节点为 disabled，不能据此启用 I²C PA。保留已有通用 GPIO hook，不导入 E5 AW87xxx 硬依赖或 M8 的听筒映射/音量上限；用户已确认短信提示音清晰无数字杂音，并反馈电话测试正常；各音频路由、音量及蓝牙细项未逐项确认。 |
| 存储 | eMMC Y2P032；userdata 是 FBE file 加密且有 metadata 加密。不能让 initramfs 直接读取 Android 解锁后的 /data 文件；本次经用户明确授权，离线调整几何，由原厂 recovery 重建加密 data，再返回 Android 续装到独立 ext4 分区。 |

## 原厂二进制依据

- `bq2560x-charger.ko` SHA256：`3335a81b95aad5154d820292949f0105e7f2f75fb3afaa2a4fbb7b594c398641`。
  `set_current=0x2c60`，读取 REG0B 低两位并分支；本机命中值1的路径。
  `usb_get_property` 的电流回读分支 `0x212c–0x2230` 确认相同的分段编码，
  setter/getter 两端均已核对，500mA 对应低六位8。
  `set_termina_vol=0x31e4`，REG04 32mV 编码；`set_termina_cur=0x3828`，REG03 60mA 编码。
- FocalTech `.ko` SHA256：`8903525c4282770b08b0b1bbc436f0cb8d9007f8d93d04c1e0734f158d3d4d8f`。
  原机 sysfs `fts_driver_info` 给出 IC ID、I2C 地址、GPIO 与坐标；不单凭 ko 文件存在判断硬件。
- `sprd-charger-manager.ko` SHA256：`481a5e9a025f52784c1b83d4c4e2d2f763d011145358e7b501b0fdf9423e2b8c`。
- 其他文件 SHA256 与恢复镜像信息见本机 `reference/manifest.json` / `manifest.json`。
  boot/vendor_boot/dtb/dtbo/vbmeta 两槽内容当前一致。收集时逐项回读校验，没有写分区或切槽。

## 构建入口

```sh
T512N_KERNEL=/path/to/independent/linux-lts-e5 \
T512N_BUILD=/path/to/t512n-objects \
T512N_OUT=/path/to/t512n-kernel \
T512N_RELEASE=1 bash devices/t512n/build-kernel.sh

python3 devices/t512n/stage.py --root /path/to/offline-root
python3 devices/t512n/test_charger.py
```

`prepare-kernel.py` 复用 UMS9621 已有移植，禁用 T551M 的电流表覆盖，再应用本机充电与触摸差异。
`stage.py` 复用 E5 的 64 位厂商启动/NV 隔离入口及公共网页、屏幕、短信、SIM/流量与关机服务。
安装包 profile/model/默认 SSID 分别为 `t512n` / `T512N` / `T512N-OpenWrt`，拒绝交叉安装其他型号。
屏幕按 INPUT_PROP_DIRECT 查找触控，不再要求控制器名字必须是 TLSC6x。

## 2026-10-18 首轮实测

- 内核 `6.18.54-t512n-bringup1-g020b970e351e-dirty` 和配套模块重新编译，
  充电模块包含本机 `T512N-0x11` 映射。USB NCM/ACM 可用。
- 第一次只启动硬件探测：16 个选定模块全部加载成功，自动回到原厂 Android A，
  `sys.boot_completed=1`。探测日志保存在忽略的 `out/t512n/probe1/`。
- 第二次运行完整 OpenWrt 25.12.5：以 E5 最新公共发布 root 为基线，
  加入当前公共源码、本机 62,683,648 字节厂商/固件归档及配套内核模块。
  原生 helper 重新编译。系统通过直接 USB 传入 tmpfs，`/proc/mounts` 确认
  没有挂载 userdata 或任何 eMMC 文件系统；默认启动仍为 Android A。
- 用户确认**屏幕画面和触摸上滑都正常**。面板 320×480，FT6336U 输入注册，
  息屏控制按 INPUT_PROP_DIRECT 发现该触控设备；反复息屏/来电等场景尚未验证。
- 本机 64 位 `modem_control/refnotify/cp_diskserver` 运行，ModemManager
  识别 W23.39.3 基带。两槽均未插 SIM，状态 `sim-missing`，射频处于 off；
  不能据此声称蜂窝拨号、移动重连和通话已实测。
- 本机 WCN 固件启动成功，hostapd AP-ENABLED，5GHz ch149 / VHT80。
  LuCI 登录页可访问，标题为 T512N。没有让电脑连接热点；客户端连通/吞吐待测。
- 蓝牙控制器、声卡0、音频准备服务出现。通过公共短信试听接口播放“叮”声，
  用户确认清晰正常、无数字杂音。蓝牙配对、录音、听筒和通话尚未验证。
- 三个 CPU 策略 0/6/7 使用 sprd-cpufreq-v2，默认 schedutil。
  CPU/温控 RPC 和充电管理 RPC 可读取。非零电压调整仍被固件校验阻止：
  本机 SML 两槽相同，SHA256 为
  `b0beb84a18b4d823bf62ec1ea36a46b960b5ea0a54433d449d8a007968879075`，
  其 DVFS 偏移 ABI 尚未逆向核验，**没有为了显示支持而直接加入白名单**。
- 电脑 USB 被识别为 SDP，输入/充电请求500mA，REG00=04、REG02=88，与
  原厂相同编码。实际充电器电流、C-C 快插拔、OTG、SOC 长期准确性及续航待测。
- 运行后复核原始 `nr_fixnv1/2_a/b`、`nr_runtimenv1/2`、`nr_deltanv_a/b`
  和 `calinv`，全部 `ro=1`；厂商写入只落到内存系统的隔离工作副本。

开发用内存启动入口为 `make-ram-boot.py` / `send-ram-root.py`；以本机验证备份生成启动镜像，
通过 `flash-probe.py` 写备用 B 槽并逐项回读。再次替换已验证的探测镜像时，提供
`--previous-image`，只允许其前56MiB匹配（尾部为诊断日志）。镜像进入 init 后先恢复 Android A，
收到并校验整份 root 归档后才切入 tmpfs 系统；传输失败保留超时回退。
它不是持久安装方式，重启会丢失本次 OpenWrt 设置，且不会自动格式化加密的 Android data。

后续进展见 `TEST-PLAN.zh-CN.md`：已完成持久安装、正常重启、插卡拨号和切卡，
用户确认 OTG、短信及电话测试正常；通话各细项未逐项记录。
本机 SML 调压 ABI 现已核验并接入公共后端，温控及三簇调压已完成实机验证；
依据和省电边界见 `CPU-POWER.zh-CN.md`。用户后续确认充电、通话、热点负载及系统往返正常，
弱信号恢复为初步正常，热点 ping 偏高仍保留待查。
仍需验证去除 bring-up 忽略未用资源参数后的完整休眠链路、完整充放电与续航，
以及进一步确认移动网络恢复；低电量耗尽测试按用户要求暂不进行。
不将首轮可启动写成所有硬件已适配成功。
