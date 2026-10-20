# 充电管理卸载与电荷量计算修复

## 实际触发的重复注销

2026-10-20，按用户授权给远航 41S 做一次临时 SOC 对齐。为避免旧 CM 的 UI
保存覆盖已校正的 FGU，解绑 CM 时触发其已有卸载缺陷：

- probe 用 `devm_power_supply_register()` 注册 `charger_psy`；
- remove 又手动调用 `power_supply_unregister()`；
- remove 返回后 devres 再次注销同一个对象，导致 `kernfs_find_and_get_ns`
  空指针异常，经 `devm_power_supply_release` 调用栈确认。

这是本次维护操作触发的崩溃，不是声称机器日常充电时已复现相同故障。样机
自动重启恢复；保留了 pstore 证据，没有重跑缩容、格式化、改 NV 或 Android。

公共 `charger-lifetime.patch` 移除重复的手工注销：先停止写入工作并正常关闭
充电，再由 devres 唯一释放。实际 C 回归检查 remove 结束前对象仍存活，且
所有工作已停止、停充已经调用。修复后 #18 实机解绑同一路径没有再崩溃。

## 剩余电荷量整数溢出

SOC 对齐至 94.8% 后，5000 mAh 电池的 `charge_counter` 曾返回 445032 µAh，
正确数量应约为 4740000 µAh。公共 CM 的 `cm_get_charge_now()` 用 32 位
`total_uah * cap` 相乘再除以 1000，中间结果已经溢出；低一些电量时还可能
变成负数，被上层误当无效值返回 1。

`charger-capacity-arithmetic.patch` 将中间乘积提升为 64 位，不改变 SOC、
设计容量或校准策略。C 回归覆盖 2–20 Ah 各 1001 个 SOC 值、94.8% 现场
数值和读取失败传播。这是电荷量展示接口修复，不是一次新的 SOC 重校准。

两项均接入公共 FGU/CM 准备器；公共源码修复与其他型号的硬件验证分开。

## 最终实机确认

远航 41S #21 内核已应用两项公共修复及全部 237 个匹配模块。正常重启后
UI=95%、FGU raw=953/1000、设计容量 5000000 µAh，`charge_counter` 实读
4765000 µAh，符合 64 位计算结果，已不再返回溢出的 445032 µAh。

通过真实 `e5-charge.configure` 接口临时设置 85% 开始、90% 停止，CM 停充位
和外部控制位均变为 1，充电芯片报告 `Not charging`；恢复原配置后两位均为
0、芯片报告 `Charging`。原配置逐字节恢复，未改写 SOC 或自动开机选择。
回执：`out/yuanhang41s/acceptance/charge-policy-final-live.txt`、
`soc-sipa-final-reboot.json`。Android B 原始 boot 保持不变，根分区仍为 100 GiB。
