# E5 间歇性 OTG 角色交接（2026-11-11）

## 实机证据

本轮设备为荣悦 E5，AW32257（I2C 2-006a）、SPRD MUSB
`64a00000.usb`。运行内核为 `6.18.54-e5-mobile4`，构建时间
2026-11-06，Build ID 为 `860b25b182a776e9963969ee6d88ff273dbcf0b1`。
保留的 rootfs release 标记较早，不能据它判断实际内核版本。
当前登记的本地内核 Build ID 不同，不能视作实机二进制的逐字复现。

- 本次重启后 34–46 秒，Type-C 已报告 source/host，但 MUSB 一直报告
  `waiting glue suspended in host mode`，未进入升压和主机总线注册。
- 用户随后保持自动模式重新插入普通 OTG，591 秒和 600 秒两次均进入
  host，打开升压并识别 USB hub、RTL8152 网卡和存储设备。
  拔出后退出主机，runtime suspend 完成，再接电脑恢复 NCM。
- 上次启动的 pstore 有 `configured as A device timeout`、`Babble` 和
  重复的 `UDC had already started`。这些说明需检查角色交接，不能据此
  单独认定外设、线缆或某一次部署导致故障。
- 当前设备模式读到 DEVCTL=0x99（含 SESSION）。实机未开启高级 PM
  调试计数，无法直接取得占用 runtime PM 引用的调用者。

诊断原件保存在被忽略的 `work/e5-otg-20261111/`，不作为构建输入。
本轮只读取硬件状态并运行限时诊断采集；尚未刷入下面的候选修复。

## 已确认的驱动缺陷与修正

AW32257 的 OTG regulator 查询使用 `(REG01 & 1) >> 3`，结果恒为 0。
实机模块反汇编也确认了该路径。实际启停使用 REG01 的 OPA_MODE bit 0，
查询现改为读取同一位，并保留 I2C 读取错误。这个错误不等于上述所有
等待休眠的直接原因：失败日志尚未进入 regulator enable。

共用 MUSB 源码还存在两条由通用 OTG 核心抢先建立 SESSION 的路径：

1. 无线缆时绑定 gadget，B_IDLE 的 `musb_start()` 也置 SESSION，通用
   session 检查随后额外持有 runtime PM 引用；SPRD 外层角色机要先等
   glue 休眠才能进入主机。实际 C 函数离线重放可复现这条等待条件。
2. gadget 重新绑定时，PHY 的旧 `last_event=USB_EVENT_ID` 会让通用回调
   再次请求 source/VBUS，即便当前已经回到设备角色。

SPRD 的 CC/glue 本来负责实际角色与供电，现阻止上述两处抢先启动，保留
真实主机的 A_WAIT_BCON 启动、有效 VBUS 下的外设模式、主机退出的 PM
引用释放及原有主机/外供电策略。不新增恢复轮询，不强制挂起或关闭全局
省电。非 SPRD 控制器保留原行为；DWC3 机型不经过该 MUSB 准备器。

这是代码层面可复现的交接缺口，与实机故障表现相符；尚不能宣称已读取
到实机卡住时的那一份 PM 引用，或已完成候选内核硬件验收。

## 回归与构建

`test_otg_readback.py` 覆盖 256 个寄存器值、无关位、I2C 错误与重复准备。
`test_usb_gadget_session.py` 编译并执行实际启动、gadget 和 session-PM C
函数，覆盖旧代码负对照、空载绑定、旧 ID 事件、有效设备角色、五次真实
主机会话以及其他平台。既有主机初始化、HCD 生命周期和 host-sink 回归
也接入 `tools/build-profile-kernel.py`。

新内核必须通过登记的 E5 构建入口生成 Image 与完整配套模块，再单独
安排实机刷写与插拔测试。离线回归通过不代表硬件验证通过；不修改基带、
NV、IMEI、GPT 或用户 USB 策略。

本轮构建任务 `20261110-173903-e5-otg-session-verified-96781` 已完成，
输出仍在登记的 `/home/kano/openwrt-build/kernels/e5/`，候选内核 Build ID
为 `fb3d49556e266581dc2b031a8980d12e53d05485`。Image.lk、完整模块归档和
root-modules 归档的 SHA-256 复核通过，上述回归与既有电池/NCM 回归通过；
host-sink 测试中的 BQ 专用分支在 AW 机型上按设计跳过。尚未生成正式
发布 ZIP，也未更新实机 boot 或模块。

后续按用户要求完成 T551M、T512N、LYM-10K 的独立编译与回归，任务分别为
`20261110-174500-t551m-otg-session-15149`、
`20261110-175700-t512n-otg-session-33356`、
`20261110-175821-lym10k-otg-session-51471`。三者均保留各自充电芯片、
电流及板级规则，T512N/LYM-10K 的地址和芯片 ID 隔离矩阵也通过。
共同交接修复已经编译进四款的各自 Image；未把 E5 的 AW 修正用于 BQ。
这些机型仍待新内核实机插拔验证，不能由 E5 的旧内核偶发成功推断通过。

## 冷启动复测仍失败

用户随后报告：开机后直接接普通 OTG 不供电，先插拔充电器才恢复。
只读核对确认运行的内核 Build ID 正是上述候选，并非误用旧内核。
本轮日志证据保存在 `new-kernel-usb-logread.txt`、
`new-kernel-cold-boot.txt` 和 `new-kernel-state.txt`。

- 4.818 秒 glue 完成首次 runtime suspend。
- 4.998 秒创建 gadget 时唤醒，之后至首次 OTG 都未再出现 glue suspend。
- 77.985 秒 Type-C 已报告 source/host，MUSB 仍停在
  `waiting glue suspended in host mode`，尚未打开升压。
- 充电器插拔后的 111.800 秒再次 runtime suspend；113.878 秒下一次
  host 事件顺利启用升压和主机总线。

因此上一轮修正的缺陷并未覆盖全部冷启动路径，不能宣称 OTG 问题已修复。
当前证据指向 gadget 创建后到首次 host 前的 runtime PM 交接；尚需区分
多余引用、活跃子设备、错误状态和缺少休眠请求，不以猜测强制减引用或挂起。

准备了一次性诊断：独立 ftrace instance 只记录本控制器及其子设备的 RPM
事件，在首次 host 等待时读取一次现有 regdump，利用它已平衡的 PM get/put
观察引用计数与返回值。读取可能触发正常 autosuspend，所以须保留读取前
的失败事件；它不是生产修复，不写角色/充电/升压设置，不解绑或重置 USB。
一次运行或超时后自动关闭 instance，并取消下次开机启用。

第二次冷启动的 `first-host-pm-report.txt` 已捕获失败现场：无外部供电的
OTG 接入后 DEVCTL=0x99；受控读取带来的那一份临时引用使 child 的计数
从 1 升至 2，读取结束后仍为 1，child_count=0，disable_depth=0。
之前的 IRQ work 也反复呈现 2→1，glue 始终 active。它不是子设备数量
未清、PM 被用户禁用或单纯忘记触发 autosuspend；残留的核心会话引用
足以阻止 glue 挂起，而首次 host 正在等待这个挂起。

本次补齐 `musb_pm_runtime_check_session()` 的 SPRD 分支：不再由通用
DEVCTL 检查额外持有引用。SPRD 原厂角色流程已经在实际 host/peripheral
启动和停止时管理 controller 的 get/put，这份真实会话引用继续保留。
其他 MUSB 平台仍走原有核心计数，不更改计数器、不强制挂起、不放弃
真实主机会话的供电保护。原有 teardown 刷新工作仍保留，并回归旧计数
路径的释放，避免无关回退。

补充回归使用实机 0x99 冷启动值，覆盖无真实会话、实际主机与设备会话、
五次进入/退出、重复 gadget 绑定和非 SPRD 核心；旧核心会稳定复现多余
引用。新修正仍须重新编译及不经过充电器的冷启动硬件验证。

## 本轮冷启动实机通过

经用户明确授权，备份并校验了原 Linux B 槽 boot 和 268 个现有模块。
候选 boot 保留本机命令行、77 个非模块 initramfs 记录、原 vbmeta blob
和持久日志，只替换 Image.lk 与嵌入模块；根目录的完整模块组一并更新。
未写 Android A、GPT、userdata 或 NV，也未改 USB 策略。回退副本仍保留。

用户拔掉 USB 后从屏幕正常重启、不接充电器，首次直接接普通 OTG 外设，
确认“首次直接接 OTG 已正常”。只读取回的
`cold-owner-hardware-success.txt` 进一步确认运行 `#22` 候选内核，
Build ID 为 `dfb443e5a7997bc495664ab01d0525c1f5af6f64`：

- 创建 gadget 后，6.107 秒 glue 已再次正常 runtime suspend。
- 33.682 秒首次 source/host 事件直接进入 host_idle，33.683 秒启用升压，
  没有再卡在首次 host 等待休眠。
- 36.881 秒开始 USB 高速枚举，37.824 秒 RTL8152 注册 eth0。
- 此前没有 sink/充电器事件；首次 sink 事件在 58 秒以后。
- 拔掉 OTG 后重新接电脑，USB 管理通道恢复，SSH 可只读访问。

一次性诊断的启动项、脚本和独立 trace instance 已清理，不加入生产固件。
T551M、T512N、LYM-10K 也已分别重编并通过自身回归；它们尚未进行这轮
实机冷启动验证，不将 E5 的硬件结果冒充其他机型的实测。
