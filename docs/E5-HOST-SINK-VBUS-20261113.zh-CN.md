# E5 部分外供扩展坞无法充电或枚举

## 已确认的现场

本轮设备是 E5，`6.18.54-e5-mobile4 #23`，MUSB `64a00000.usb`、
HS PHY `64300000.hsphy`、AW32257 `2-006a`。运行设备树中，控制器与 PHY
使用 Type-C extcon；Type-C 的 extcon 上游才是真实 VBUS GPIO。与此前
U30 Air 的 USB3 方向、PD 或双级 hub 初始化试验不是同一条驱动路径。

用户选择“主机＋充电”，接失败的外供扩展坞约 16 秒，再恢复自动并接电脑。
只读 netlink 事件与内核日志交叉确认：

| 开机秒数 | 记录 |
| --- | --- |
| 13784.30 | sysfs 策略已经是 `host-sink` |
| 13788.94 | GPIO extcon 的 USB=1，真实 VBUS 出现 |
| 13789.02 | Type-C 仍是 `detached snk`；其 USB/USB-HOST 均为 0 |
| 13789—13805 | 没有 CC 接入、没有启动 BC1.2，也没有启动 MUSB host |
| 13805.05 | 拔坞，GPIO VBUS 变 0 |
| 13813.47 | 自动模式接电脑，有正常 sink connect、BC1.2 SDP 与 gadget 枚举 |

设备上的 USB 后端和网页文件与当前仓库相同。本轮并不是页面未保存模式，
也没有证据支持“USB2 速率问题”。该坞为何未形成 CC sink 连接尚未确定，
不由 VBUS=1 推断它的 PD、CC 电阻或硬件内部实现。

## 源码缺口与受限修正

固定基线 `020b970e351eee3887e6caaf2d653926e7086e44` 的
`sc27xx_typec.c` 包含 `sc27xx_typec_handle_vbus()`：在 VBUS 存在、等待
500 ms 仍无 CC partner 时发布 SINK/USB extcon。但该基线只初始化其
`vbus_only_work`，物理 VBUS 通知没有调度它。现有手动外供主机又要求
Type-C USB extcon、真实 VBUS、PHY PRESENT 与 BC1.2 完成同时成立，
因此本轮永远到不了充电检测和主机启动。

本次复用上述 VBUS-only 语义和 500 ms 的一次性 CC 等待，仅在明确选择
`host-sink` 时启用。通过公共 `musb-host-sink.py` 和
`host-sink-typec.h` 接入，不更改自动、普通主机或设备策略：

- 由既有 GPIO VBUS 通知、CC 中断和显式策略变更调度一次工作，读取当前
  电平，未新增常驻服务或周期轮询。
- 必须仍是手动 UFP 受电策略、真实 VBUS 为正、没有真实 CC partner、
  没有 USB-HOST 事件，才发布 VBUS-only SINK/USB。供电提供者缺失、
  错误返回或已退出策略时不启动；不写 CC attached 状态，不伪造 partner。
- 不直接启动主机、升压或充电；后续仍由原 PHY BC1.2、MUSB 状态机、
  charger-manager 和芯片驱动处理。保留升压仍开着时禁止外供主机进入、
  真实外部电压存在时禁止普通 OTG 升压的检查。
- VBUS 消失或切回其他模式会撤销自己发布的状态；迟到的 CC sink 可接管
  相同 extcon 电平，不制造一次虚假的拔插。停止时与通知调度互斥并取消
  工作，避免驱动移除后残留回调。
- 实际 HCD 已启用时，角色回读同时接受真实 partner 或 VBUS-only 会话；
  仅有策略请求不会报告主机已运行。

基线源码说明参考机制，不等同于本机原厂 Android 对这只坞已实测成功。
本轮没有修改设备树、PD、CC 电流、电池表、温控、AT、NV 或 IMEI。

## 离线验证与硬件边界

- E5 完整 Image、配套模块构建成功，通过原有 MUSB 冷启动、主机生命周期、
  防反灌、BC1.2 顺序、AW32257 充电控制及电量回归。
- 新增实际 C 工作函数回归：手动 VBUS-only、正常模式不介入、拔线、延迟
  回调遇到已拔线、迟到 CC 接管、撤销模式、供电错误、旧 source 事件和
  清理后禁止重新调度。
- 单独运行生成的 Type-C IRQ/策略与 BC1.2 顺序回归通过；既有主机和
  充电时序保留。
- 测试 boot 根据当前已安装 Linux B 的备份生成，保留 init、安装元数据、
  启动参数、非模块 CPIO 记录和诊断区；替换内核及全部配套模块。
- 用户授权后已写 Linux B boot 的前 56 MiB 和 footer，并更新配套模块；
  均回读校验通过，Android A boot 的完整哈希未变。诊断区未写入。
- 正常重启进入 `#24 SMP Fri Nov 13 18:28:32 +08 2026`。新 boot 前缀及
  正式模块目录中的 293 个模块路径（含 25 个兼容链接）逐一核验一致；
  USB 自动/设备模式、NCM configured、热点 up、蜂窝 WAN IPv4 up。
  电池 100%，外部电源 online=1、状态 Full；这不是新增充电电流实测。
  启动日志未见 Oops、未知符号、无效模块或 BTF 加载失败。
- 用户随后测试三款扩展坞/转网口，明确反馈均可使用。对应日志三次进入
  `external=1` 主机，电源 online=1，r8152 网卡枚举成功；两轮有新增的
  `manual external host: VBUS-only attachment`，另一轮由原有 CC 路径接入。
  拔出后主机会话退出，切回自动后电脑连接恢复，未见内核异常。
  电池为 Full，不能由这轮声称实测了最大充电电流。

## 其他机型的共享接入

| 路径 | 机型 | 本轮验证 |
| --- | --- | --- |
| MUSB | E5 | 完整内核/模块编译、离线回归、本机三款外设通过 |
| MUSB | T551M、T512N、LYM-10K | 各自已登记源码的补丁生成、重复应用通过 |
| DWC3 legacy CC | M8、K1、Y60、畅行60 | 各自已登记源码的接入、生命周期、策略错误保留回归通过 |
| DWC3 legacy CC | 远航41S | 自身角色锁/策略字段的接入和重复应用通过 |

DWC3 的控制器/PHY 实现不同，但本轮检查上述五款实际准备源码，确认物理
VBUS 回调同样未调度 VBUS-only 处理。通过 `dwc3-typec-vbus.py` 复用同一
受限工作函数，分别保持既有 `policy_lock/external_host_policy` 和
`role_lock/host_sink`，不把 MUSB 控制器或 AW32257 适配搬过去。清理在
extcon 注册后、IRQ/notifier 暴露前登记；remove 先停止本工作。

三种锁/字段布局的实际 C 工作函数均通过 address/undefined sanitizers，
没有新增轮询。DWC3 基础/外供电补丁的组合重放另有回归：接受已存在的
外供检测例外，不重复插入 include 或初始化锁，不覆盖已完成的外供适配。

**其他八款仅更新源码并完成上述离线检查，尚未重编全部内核，也未实机验证。**
后续正式打包必须编译各自变化的内核与配套模块；不把 E5 的 Image 或模块
发给其他机型。此次不介入 MU300 或 Pro 的 TCPM/PD 策略。
