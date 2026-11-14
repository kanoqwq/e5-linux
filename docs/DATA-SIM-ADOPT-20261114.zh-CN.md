# 选择当前实际卡时保存开机卡并恢复原有数据请求

E5 使用 e5-sim / ModemManager，与 Pro 的 mu300cell 实现独立。
源码确认三个相连的短路：网页禁止选择当前实际卡，RPC 对同卡直接成功返回，
e5-sim 也在保存开机卡之前退出。实际卡与保存卡不一致时，显式选择无法纠正
旧设置；若 WAN 已因旧卡缺卡进入不可用状态，单纯 ifup 也不会恢复。
这是离线确认的公共缺陷，不代表九款机型都已实机复现。

网页只在实际卡、保存卡与目标全部相同时禁用按钮；RPC 同样只把这类请求视为
无需操作。e5-sim 在同卡但保存值不同的分支，保存并提交默认卡；只有 WAN
autostart=true、未连接且未拨号才恢复协议可用状态并 ifup。不重载现有模组，
不发新增 AT，不改变射频权限。手动数据关闭、已连接及正在拨号均保持原状，
存储失败时不会恢复 WAN。正常异卡路径沿用原实现。

E5 信息屏的 native/sim-status.c 已从实际 sipc_wwan card 参数读取 active，
公共看板同样读该参数，没有 Pro feeder 只读保存值的问题，不改这两条取卡路径。

正式文件位于公共 overlay 和 luci-app-t551m 中，仍由九机型现有组包流程收录。
新增 data-sim-adopt.py 覆盖生产 shell 的双向保存、不可用恢复、手动断网、
连接／拨号保持、保存失败以及真实 RPC 入口；data-sim-adopt-browser.py 使用
实际网页源码测试双向选择、取消和桌面／移动端按钮行为，所有 RPC 均为夹具。
现有 reload、radio、network-recovery 回归继续运行。未热部署或重新打包。

离线结果：选卡采用 6 项、原有 reload 6 项、射频 14 项、恢复策略 18 项均通过，
日志 out/build-logs/20261114-144911-data-sim-adopt-regression-41682/command.log。
原有状态读取测试在这轮主机无原生 ucode 时跳过，随后用隔离 ARM64 容器运行
实际 ucode 重新完成 3 项，包含新增“保存卡1、实际卡2”的检查，未跳过：
out/build-logs/20261114-145219-data-sim-actual-screen-proof-63043/command.log。
桌面与手机端浏览器检查通过；普通已保存的同卡按钮仍禁用，保存值不一致时可
明确采用当前卡，不必绕到另一张卡再切回来。没有新增状态轮询或改动 AT 所有权。
