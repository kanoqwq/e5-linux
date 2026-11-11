# E5 热点开启时的 Wi-Fi 搜索

## 现场与已确认原因

2026-11-11 用户连接 E5，内核 `6.18.54-e5-mobile4 #22`，设备报告的
发行标识为 `r20261027.195039-g8b891d81-s05c0240a`。安装的扫描库是
`libiwinfo20230701-2026.01.14~f5dd57a8-r1`。

LuCI 的 `ubus call iwinfo scan` 执行约 1.3 秒，正常返回但结果为空。
事件日志确认 AP 模式下固件返回 `EVT_ACS_DONE`，最终报告 `0 BSSes`。
这与 F50 现场的 ACS/热点发现路径差异相同，不能据此认为旧版与新版
SC2355 的所有协议行为都相同。E5 的扫描版本、abort_scan、N79 策略保留。

## 用户态修复与 AP 边界

为 SC2355 的 AP 显式选择专用搜索路径：从 PHY 的 driver/module 确认
`sprd_wlan_combo`，创建进程私有、未关联的临时 STA，搜索附近 BSS，
完成后关闭并删除这个临时接口。其他驱动和原有 STA 路径不变。

- 不关闭、重建或更改原 AP，不发送 hostapd reload，不重置 WCN。
- 创建或扫描失败时直接返回，仅清理本次临时接口，不用关闭 AP 作为后备。
- 使用 `NL80211_ATTR_SOCKET_OWNER`，调用者退出由内核回收接口。
- 不新增常驻服务，不自动连接上级 Wi-Fi，不宣称支持关联状态下 AP＋STA。
- 单射频离信道扫描可能短暂影响时延；保持 AP/客户端存在不等于吞吐零损失。

这里的 libiwinfo 补丁以固定的 OpenWrt 源码构建；对应版本与构建散列由
`openwrt/iwinfo-ap-scan-state.py` 检查。复用既有 SDK，不复制工具链。
源码、rootfs 构建和本地多机型组包均已接入，统一记录
`/etc/e5/iwinfo-ap-scan.build-id`，CI 发布核查也要求该标识匹配。

```sh
python3 tools/build-release.py run --workspace /home/kano/openwrt-build \
  --label iwinfo-ap-scan -- sh openwrt/build-iwinfo-ap-scan.sh
```

## 驱动请求归属修复

即使使用较新的驱动，E5 仍存在另一个确定的源码问题：发现已有扫描时仅
记录错误却继续覆盖请求；命令启动失败后既调用 `cfg80211_scan_done` 又返回
失败。修复通过 `devices/common/wifi-scan.py` 接入公共 WLAN 准备路径，
拒绝重入；失败只回收本次请求且不发送完成通知；若完成事件先到则返回
接受，避免同一个请求被重复交回 cfg80211。等待扫描定时器时不持有 scan_lock。
不更改扫描命令、固件版本、AP 配置或现有中止流程。

`devices/test_wifi_scan.py` 对实际 E5 扫描入口及取消函数编译运行，覆盖
AP、STA、第二 STA 下的接受、拒绝、完成先到、重入，并用旧的重复交回行为
作为失败对照。重复应用补丁保持一致，原 abort_scan 函数保持不变。
对应测试已接入本地多机型、远航系列及 K1 构建入口。

## 实机结果与限制

先通过独立进程加载新库，真实搜索得到 17 个热点；随后仅升级扫描库 APK
并重启 rpcd。连续 5 次 LuCI 搜索分别得到 17、18、18、18、19 个热点，
每次约 2 秒；事件日志包含新临时接口及新收到的 BSS，并非只读旧缓存。

强制结束独立扫描进程后，临时接口自动删除；再用 `device=radio0` 搜索得到
19 个结果。原 AP 的 ifindex、MAC、149 信道/80 MHz 不变，同一个已关联客户端
仍存在，无线配置 SHA-256、boot ID 保持不变，内核 taint 保持 0。
没有修改启动分区、刷驱动、重启整机/基带或操作 NV/IMEI。

本次已编译用户态 APK，驱动请求归属修复完成离线 C 回归，但尚未重新编译、
部署该 E5 驱动，也未生成新完整固件包。其他 E5 系列机型接入同一公共路径，
尚无本轮实机验收；关联客户端持续打流和长期运行仍待验证。

现场记录保留在忽略目录 `work/e5-ap-scan-20261111/`：
`baseline-luci-*`、`isolated-library.txt`、`patched-luci-*`、
`interruption.json`、`final-health.txt`。含附近热点和设备标识，不作为公开构建输入。

## 后续发行构建

同日随后完成 E5、T551M、M8、T512N、LYM-10K、畅行60、Y60、远航41S、
K1 的内核和完整模块补编。日志见
`out/build-logs/20261111-044245-nine-kernels-ap-scan-esd-build-55682/`（前两款）
及 `20261111-044548-seven-kernels-ap-scan-esd-91806/`（后七款）。
各自的扫描请求归属及配套交付件检查通过；M8 额外执行 DSI/ESD 回归。
这些编译结果更新了前述“尚未重新编译”的调查阶段状态，不增加硬件验收范围。
