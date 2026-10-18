# T512N 插卡现场与两项独立的软件修复

## 现场事实

2026-10-18，T512N/PB8，原厂基带 `5G_MODEM_V2_23B_W23.39.3`，
OpenWrt 内核 `6.18.54-t512n-bringup1-g020b970e351e-dirty`。
用户最后确认卡槽没有插好、接触不良；不能将本次无卡归因于下面的软件缺陷。

读取现有 AT owner 返回：SIM1 为 `+CME ERROR: 10`，SIM2 为 `+CPIN: READY`。
SIM2 已注册电信 5G SA，但上网卡仍选择 SIM1，ModemManager 因此报告
`sim-missing`。使用现有 `data-sim`/`e5-sim` 流程切到 SIM2 并持久保存后，
ModemManager 进入 connected，WAN 经 `sipa_eth8` 获取 IPv4 和 DNS。
屏幕采集也更新为 active=1、SIM2 present/home/5G SA。

用户随后主动拔掉 USB，并说明接触问题；没有把这次断开当作设备异常。

## 独立确认的软件缺陷

1. 开机 `e5-sim-probe` 在读到 `+CME ERROR:` 前缀时就结束查询。
   SIPC 是字节流，数字错误码可能在下一次 read 才到达，导致错误地得到 unknown。
   当前实现改为等待完整终结行，再解析错误码。只修响应分帧，不增加命令、
   重试、睡眠时间或猜测性切卡，也不将 busy/未知响应当作无卡。
   旧源码在伪终端上可稳定复现：将错误码 10 拆开发送，会保留空的首选卡槽。
   现场没有记录原始分片，所以不能证明本次开机 unknown 由该缺陷引起。

2. 实机切卡时，`e5-sim` 调用 rc.common 异步重建 WWAN 后已经返回，
   `data-sim` 随即读取暂时不存在的 `/sys/module/sipc_wwan/parameters/card`，
   将已经进行中的成功切换误报为失败。源码现在只等待该 sysfs 节点恢复，
   使用原有 180 秒操作预算，之后仍核验实际卡槽与保存配置一致。
   不改变 AT 所有权、回放、拨号及射频命令顺序。

修复位于公共 OpenWrt 源码，适用于各屏幕机型共用的构建/部署路径。
首次定位后已编译新 ARM64 静态探测程序；用户当时拔线，未开始替换文件。
随后用户重新连接并报告屏幕切卡误报，实机旧任务记录确认两次错误均为
`/sys/module/sipc_wwan/parameters/card` 暂时消失。现已原子替换公共
`data-sim` helper，并校验源码/设备 SHA256 和设备侧 Python 语法；
开机探测程序仍未部署，未重新打包。

## 后续切卡实测

- 两张卡均已插入并注册，通过与屏幕/网页相同的 `data-sim` 后端执行
  SIM2→SIM1→SIM2。两次任务都返回 `ok=true, state=done`，没有误报 ENOENT。
- SIM1 经 `sipa_eth0` 获取 IPv4，SIM2 经 `sipa_eth8` 获取 IPv4。
- `ubus call mu300dash sim_status` 回读两次任务均为成功，确认屏幕所用
  RPC 路径也返回正确状态；没有修改旧失败任务记录来制造成功结果。
- 最终恢复并保存 SIM2，ModemManager connected，电信网络 home，WAN up，
  两卡射频开关均保持开启。本次修复不需要重启设备或屏幕程序。
- 部署前文件备份及往返任务证据保存在忽略目录 `out/t512n/sim-insert/`。

## 验证

- `openwrt/tests/sim-probe.py`：14 组伪终端场景通过，包括两槽插卡、
  PIN 锁、busy/未知、延迟节点及错误码跨 read 分片；验证 AT 命令序列不变。
- `devices/t551m/test_data_sim.py`：7 项通过，包含异步 WWAN 节点暂时消失。
- `devices/t551m/test_sim_status.py`：9 项通过。
- `openwrt/tests/sim-radio.py`：12 项通过。
- ARM64 静态编译以 `-Wall -Wextra -Werror` 通过。
- `sim-lock.py` 另需 `e5-openwrt-base:25.12.5` Docker 镜像，当前环境缺少镜像，
  未能执行该项；本轮没有修改 SIM/SMS 互斥代码。

切卡 helper 已完成上述实机往返；开机探测的离线分帧回归通过，
不等同于新探测程序已经在本机开机验证。

## 公共修复覆盖

- 切卡修复只有一份，位于
  `openwrt/luci-app-t551m/root/usr/libexec/unisoc-modem/data-sim`，
  没有 T512N 专属判断。网页和屏幕通过同一组 `sim_set` / `sim_status`
  RPC 使用它。
- 已对 E5、T551M、M8、T512N 分别执行公共 `devices/t551m/ui.py`
  的实际 staging，确认四份输出都包含相同的新版 helper、保留可执行权限，
  并保留已有用户配置。构建和公共 UI 更新由此带入修复，无需逐机型复制代码。
- MU300 的公共切卡后端使用 AT 选择数据槽，不卸载 `sipc_wwan`，也不读取
  该 sysfs 卡槽节点；U30 Air 转交其原生切卡实现。因此没有照搬这一节点等待。
  MU300 现有切卡离线回归 8 项通过，本轮未改其切卡代码。
- 当前仅 T512N 已热部署并完成上述实机切卡往返。其他机型是公共源码和
  staging 覆盖验证，尚未逐台热部署或实测；本轮没有重新打包。
