# 切卡重载窗口与单卡短信状态

## 切卡结果误报（issue #21）

反馈来源：[sprd-linux-devs/e5-linux#21](https://github.com/sprd-linux-devs/e5-linux/issues/21)。
`e5-sim` 返回时，异步重建的 `sipc_wwan` 可能还没有重新创建 `parameters/card`，
此时立即读取会得到 `ENOENT`，不能据此判定切卡失败。

本仓库 `fd47cfc1` 已在 data-sim worker 的完成校验中处理这个窗口：只重试
`FileNotFoundError`，且包含切卡命令在内共用原有 180 秒操作预算。普通状态查询
仍立即返回；卡号错误、无效值、权限错误、保存选择不匹配和命令失败仍报错。
本次补充直接运行生产 worker 的离线回归，没有再叠加另一套等待或重启逻辑。

## E5 空卡槽被当成短信读取错误

E5 接收程序原先把未插卡时的 CCID 失败保存为 `slots[card].ok=false`，旧的
“服务 → 短信”页直接显示该槽位错误。单卡机型或双槽只插一张卡时会出现误报。

修改后，仅在 CCID 读取失败时，通过已有 AT owner 对同一槽位查询 CPIN；
只有明确的 `CME ERROR: 10`、`SIM not inserted` 或 CPIN 未插卡结果才标记
`ok=true,present=false`，页面显示“未插卡”。不增加正常扫描的 AT 命令，
不增加定时器、不切上网卡。SIM 锁定、繁忙、超时、无响应或未知身份故障仍是错误。

未插卡不删除该槽历史短信或初始化基准，另一槽仍可接收、提醒、转发；重新插卡后
继续现有身份隔离及去重。仍从 NR0 接收自动通知，接收通道不作为短信归属依据。

MU300、Pro 已有按 `sim_slots` 限制第二槽服务以及明确空槽跳过 AT 的实现；
检查对应 sim-scope 与短信空闲/插卡回归，不将 E5 的 ModemManager 实现复制过去。

## 验证范围

- `python3 openwrt/tests/data-sim-reload.py`：6 项切卡窗口、超时预算和真实错误测试。
- `python3 openwrt/tests/dual-sms.py`：28 项，包含任一槽空缺、插回、历史保留、
  明确卡槽查询、NR0 通知、去重、归档、AT 上下文恢复与另一槽提醒/转发。
- `node openwrt/tests/sms-empty-slot-ui.js`：运行生产页面的列表渲染，检查空槽提示、
  真实错误显示和历史短信保留。
- MU300、Pro：各自 `tests/test_sms_idle.py` 与 `tests/test_sim_scope.py`。

以上为离线验证；尚未对群友的具体固件或机器进行实机复现，本次不打包、不部署。
