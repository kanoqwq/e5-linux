# 安装器准备标记误报修复

2026-10-18，T512N 两阶段内置分区安装实测。

`part-two-stage.py` 通过 `su_checked()` 读取准备分区前128字节，标记行后包含 NUL 填充。
原包装器直接追加 `__E5_STAGE_STATUS__=0`，该字符串与尾部 NUL 位于同一行，
无法匹配按行解析规则，误报“device returned no completion status”。这不表示分区准备失败。

公共 `openwrt/bundle/flash.py` 在状态记录前输出换行，确保命令有无末尾换行、
是否带二进制填充均不影响完成状态识别。原始准备标记及事务校验保持原样。
`openwrt/tests/android-root-tools.py` 新增实际 shell 回归，覆盖无换行输出与 NUL 填充标记。
该文件6项测试在 Linux/WSL 通过。开发用 T512N 安装目录也已应用修正并通过分区标记检查。

MU300 当前读取路径使用暂存文件、ADB pull 和长度/哈希校验，不存在同样的状态行拼接缺陷。
MU300 仓库已增加512字节 NUL 填充分区标记的真实 shell 传输回归，相关9项测试通过。
没有为了形式同步而引入另一套完成标记或传输兜底。
