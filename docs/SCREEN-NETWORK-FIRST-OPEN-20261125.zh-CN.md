# 信息屏网络设置首次进入为空

日期：2026-11-25。适用 devices/common/infoscreen 的公共信息屏。

## 原因

网络设置通过 mu300dash lock_get 读取当前控制卡槽的锁定缓存。缓存不存在时，
后端立即返回未知状态并启动既有后台读取。settings.uc 因此将网络模式、
LTE／NR 频段等渲染为只读项，这是避免把未知值当成“自动”的正确行为。
缺口在于页面没有跟进后台结果：第一次一直显示未知，退出后再进入才取得
已完成的缓存。此外，“刷新锁定状态”原来未标记 reload，成功刷新后也可能
只更新动作行，其他只读项仍不变。

## 修复

- 锁定状态行携带 pending，明确区分首次读取尚未完成与可编辑快照。
- 复用屏幕已有状态刷新节拍，只在当前网络分类仍待数据时补读共享缓存；
  不新增常驻定时器、AT 读取器或直接基带命令。
- 读取完成自动更新同一页面，显示动态频段候选并恢复控件；等待时显示
  中英加载提示及既有加载指示。
- 同一分类的慢请求不重叠；离开分类、息屏、锁屏、打开编辑器或保存时
  不发新的跟进请求。已在途响应不会重新打开退出的页面。
- 首次等待有 30 秒期限，超时提示手动刷新；网络请求失败也停止自动跟进，
  保留显式重试入口。解锁刷新可重新开始等待。
- 手动刷新后重载分类；保存后重载先等待操作前的在途请求结束，再发新请求，
  不把操作前的快照当作操作后的结果。

未改变锁定设置的写入、卡槽选择、AT 所有者、开机回放、射频或重连策略。
已有缓存正常时不会持续重读网络设置。

## 离线回归

以下均未连接设备：

| 检查 | 结果 |
| --- | --- |
| 冷缓存第一次进入，后台结果就绪后原页自动可编辑；SIM 2 候选与编辑目标一致 | 通过 |
| 热缓存及读取完成后的多个状态节拍不再补读 | 通过 |
| 慢响应期间多个节拍只产生一个在途请求 | 通过 |
| 离页、息屏、锁屏、编辑器停止跟进，迟到响应不重开页面 | 通过 |
| 超时边界、中英提示、手动刷新及控件恢复 | 通过 |
| 请求失败退出加载状态并可显式重试 | 通过 |
| 手动刷新与旧在途读并发，完成后取得独立新快照 | 通过 |
| 原有任务进度、重复点击保护、卡槽固定、频段编辑与 AT 选择 | 通过 |
| 实际 ARM64 ucode 运行原始及正式 staging 后的 settings.uc | 通过 |
| 原有页面元数据／错误响应两项回归 | 通过 |
| 实际差分生成／应用，首次和后续更新均替换代码；普通镜像配置迁移保留用户数据 | 通过 |

正式 staging 的 sources() 已包含两份修复文件：
usr/share/e5-infoscreen/www/app.js、usr/share/e5-infoscreen/settings.uc。
代码保持镜像所有权，不落入用户配置目录；沿用现有完整镜像、普通更新、
差分更新和屏幕更新的公共路径。没有改动安装器或收紧 OTA 校验。

测试入口：

- devices/t551m/test_screen_network_loading.py（真实生成页面、Edge、7 项浏览器回归）
- devices/t551m/test_screen_bands.uc（真实 ucode）
- devices/t551m/test_settings_sync_browser.py（既有浏览器回归）
- devices/t551m/test_screen_metadata.py
- devices/t551m/test_screen_network_delivery.py（真实比较器、事务执行器和 keep_config）

最终日志：

- out/build-logs/20261125-094346-screen-network-final-browser-9157/command.log
- out/build-logs/20261125-094518-screen-network-staged-ucode-9578/command.log
- out/build-logs/20261125-094431-screen-network-existing-browser-9207/command.log
- out/build-logs/20261125-094456-screen-network-metadata-regression-9488/command.log
- out/build-logs/20261125-094409-screen-network-delivery-final-9169/command.log

这是源码修复和离线验证，没有热部署或新固件 ZIP，实机验收待后续进行。
