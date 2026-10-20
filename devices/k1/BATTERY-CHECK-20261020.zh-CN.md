# K1 电池功能核查（2026-10-20）

本轮核对同一台 K1 的 profile/eMMC 身份、当前内核和模块，读取实际供电状态，
通过真实充电管理 RPC 短时测试限充，随后编译并部署已确认计算缺陷的修复。
未改 SOC、原厂充电上限或用户最终配置。原厂参照为本机
`AngelCare.K1.YUAN.YD.T1.0` 的设备树及充电驱动，见 `profile.json` 的证据记录。

## 参数与当前状态

- 本机原厂电池表为 3100 mAh、CV 上限 4380 mV；REG0E=0x82 按原厂
  3856+8×code 编码为 4376 mV，正确向下取整，没有沿用 M8 的 4250 mV。
- 现接电脑为 SDP，输入上限 500 mA、充电电流档位 450 mA，与本机参数和
  寄存器量化匹配。这不是实际充电器最大功率测试。
- 本次读数电压约 4.323 V、OCV 4.317 V、温度约 37°C、health=Good；UI
  100%、FGU 1000/1000。电池净充入约 28–66 mA，而不是 1 A。
- CM 报 Full，充电芯片仍报 Charging。本机原厂电池表另有 fullbatt-voltage
  4320000、fullbatt-current 120000、first-fullbatt-current 150000 和芯片终止
  电流 60000；逻辑满电与芯片硬件终止不是同一条件。因此不把这一个状态差异
  直接判成假满电或停充失效；完整硬件终止仍未现场验证。
- `unisoc-battery` 服务在运行。未人为伪造 Critical/0% 或低电压触发关机。

## 实际限充控制

经 `e5-charge.configure` 临时启用 start=90/stop=95：CM stop_charge 与
externally_control 均为 1，充电芯片为 Not charging。finally 逐字节恢复原
配置并重启充电管理服务后，两位恢复 0、芯片恢复 Charging，USB 管理仍可用。
因此手动限充/恢复链已在 K1 上通过，不代表全充放电或温度保护已实物验证。

## 确认存在的剩余电荷量读数错误

本机 charge_counter 返回 1 µAh，而设计容量为 3100000 µAh、SOC=100%。
运行中的 sprd-charger-manager.ko 与所审计 K1 模块 SHA-256 一致。
`charger_get_property` 的电荷量路径在模块偏移 0xa6e8 使用 `mul w8,w8,w9`，
先做 32 位乘法，再除以 1000；3100000×1000 超出有符号 32 位，变成负值，
后面的非正值保护返回 1。这是接口计算错误，不能解释为电池只剩 1 µAh。

公共源码的 `devices/common/charger-capacity-arithmetic.patch` 将乘法提升为
64 位；K1 准备器通过 M8 平台准备器取得同一修复，不增加机型专用计算分支。
回归覆盖 2–20 Ah、K1 的 3100 mAh、全部 1001 个 SOC 值及底层读取失败。

## 修复部署与复查

2026-10-20 在原有 K1 源码、对象目录完成增量构建，部署配套内核与全部
234 个模块，包括启动镜像内的 45 个早期模块。保留已安装 initramfs 的
启动脚本、`e5.root=part` 请求、动态槽位信息和诊断尾部，未另换安装流程。
同时包含公共 charger-manager 的 devm 重复释放修复，不热卸载旧驱动。

写入前已备份并校验旧启动镜像、完整模块树；写入后回读校验通过，Android
B 槽启动镜像哈希保持不变。正常重启后：

- SOC 仍为 100%、原始 FGU 为 1000，charge_counter 已恢复为 3100000 µAh；
  未重置电量计、强行校准或改写百分比。
- 234 个根文件系统模块逐项匹配构建哈希，无 BTF 校验、未知符号、内核崩溃
  或文件系统 I/O 错误。蜂窝获得 IP、热点及屏幕服务自动恢复。
- 再次经实际 RPC 设置 90/95 阈值，芯片进入 Not charging；逐字节恢复用户
  原有配置后回到 Charging，两个控制位均恢复 0。
- 原有充电配置、CPU 0 mV 设置保持不变；网页 CPU 调压能力仍可用。

构建与部署回执见 `work/k1-battery-20261020/cohort.json`、`repack.json`、
`deployment-written.json`、`verified.json`、`charge-policy-after.txt`。
这是 K1 实机部署验证，尚未重新制作发布安装包。

修复后版本仍为 `6.18.54-k1-bringup1`，用完整哈希区别同名构建：

- Image.lk：`49c553ad697c0900c7cf7c3f71d226082aa3a1fd39060668eaf2f1c263f67c75`
- sprd-charger-manager.ko：`5c530bf3ab4407237544222836b062fee49d8fc230abfeb05b66d759f769587b`

## 尚未验证

实际充满后的硬件终止、重充阈值、完整放电精度/重启保留、低电自动关机、
关机充电的实体按键与拔线行为、物理充电器/温升及长时间待机功耗仍需物理
验证。当前状态与短时 RPC 测试不能替代这些完整周期。

原始快照、模块比对和限充回执在忽略目录 `work/k1-battery-20261020/`。
