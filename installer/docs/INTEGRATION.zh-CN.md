# 独立安装器与固件仓库接入

公共 UI、事务记录、ADB/SSH 传输、在线 F2FS 缩容、卸载归还空间、Diff、版本摘要、OTA 更新器和 Android 控制端统一维护。固件、内核及机型安装载荷仍属于 E5/MU300 仓库。

## 适配边界

| 项目 | E5 系列 | MU300/F50/V50/U30 Air |
| --- | --- | --- |
| 安装包识别 | files/device-profile.json | desktop/manifest.json |
| 安装与升级执行 | 包内 E5 入口及 update_e5 | 包内 MU300 入口及 update_mu300 |
| 隔离工作 NV | 保留工作副本、来源记录与输入 yes 的恢复确认 | 不进入 E5 工作 NV 恢复流程 |
| 根分区 | e5root | mu300root；保留旧 e5root 布局识别 |
| UFS 身份 | READ-DESCRIPTOR 原始描述符算法 | 保持已发布安装记录的 sysfs 描述符算法 |
| Windows MTP 处理 | 不启用 | 仅有线 ADB 使用 |
| 初始管理地址 | 192.168.9.1 | 192.168.77.1 |

两边都支持 eMMC 和 UFS；合并不转换旧存储身份，不把 E5 的 NV 恢复应用到 MU300。具体分区写入继续由所选完整包中的安装入口执行，不把固件脚本混成一套。

## 外链方式

两个固件仓库均以 installer/ 子模块固定引用本仓库提交，不能在打包时自动跟随最新分支。

```sh
git clone --recurse-submodules <firmware-repository>
git submodule update --init --recursive
```

先在本仓库提交、测试、构建，再让两个父仓库分别更新 gitlink。父仓库 CI 的 checkout 必须启用 submodules: recursive。本仓库的 sources() 只对仓库内资源计算指纹；旧桌面产物因资源结构改变需要重新构建，不得改指纹绕过。

固件调用 installer/package_gui.py、installer/stage-ota.py、installer/build-ota.py 的现有路径保持有效。固件刷新命令 release.py/hotfix.py 可显式设置 OPENWRT_FIRMWARE_SOURCE；独立安装器和 APK 构建不需要固件源码目录。没有此环境的独立检出不会随机寻找另一份固件树。

## 资源归属

runtime/ 是安装器自己拥有并校验的运行资源；ORIGINS.json 记录从两个仓库提取时的来源提交和文件摘要。storage-e5.sh 与 storage-mu300.sh 各自保持身份算法，不因另一边脚本存在就替换。OTA 引擎合并保护目录，保留 etc/e5-nv 及两家的 NV 工作目录。更新这些资源时同时检查父仓库对应的设备端实现；不要依赖本机相邻目录补齐缺失输入。

## 回归边界

核心测试可在没有固件仓库的独立检出执行；需要真实固件入口源码的离线集成测试使用 OPENWRT_E5_SOURCE 与 OPENWRT_MU300_SOURCE。它们只读取源码、使用模拟设备，不连接真实硬件。未提供的可选集成输入明确跳过，不能作为通过计数。

Android 实机只可在用户授权范围内操作。自动化不得自行安装、停止用户正在运行的控制端，更不得写目标设备 GPT、boot、misc 或 NV。

## 未列出的机型

两种系列都将机型差异作为计划确认：展示目标与实测信息，必须输入小写 yes；确认与包、设备身份、板号/平台及操作绑定。Android 和桌面共用此规则，旧包内 MU300 的 android_profile 检查也接入同一确认，避免前面同意后又被旧入口拒绝。包元数据不再硬编码机型名单；格式、散列、root、分区、NV 与容量校验不因机型确认而跳过。未知硬件仍使用用户所选包所属系列的安装算法，不自动换包或猜测另一系列。
