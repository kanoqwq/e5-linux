# OpenWrt Tools

E5 与 MU300 系列共用的安装和维护工具。一个界面支持 Windows、Apple Silicon macOS 与 Android 控制端；保留各系列独立的 NV、启动链和存储处理。

- 安装到 TF 卡、userdata 镜像或独立内置分区；支持在线保数据缩容、重置后续装及卸载归还空间。
- 使用 USB/OTG ADB、无线 ADB 或 SSH；提供安装计划、进度、日志、恢复记录管理及备份导入导出。
- Diff Studio 比较完整包或旧版本 JSON 摘要；生成保留用户插件、配置及数据的差异更新。
- 系统补丁与电脑/Android 更新器共用更新引擎。

Android 手机控制另一台随身 WiFi，控制端无需 root；不会把目标设备当作本机刷写。无线 ADB 支持经典 TCP 端口，尚不包含 Android 配对码/TLS 协议。

使用说明：[桌面工具指南](GUIDE.md) · [Android 指南](mobile/GUIDE.md) · [适配及子模块接入](docs/INTEGRATION.zh-CN.md)

## 源码构建

Python 3.12+、Node 22、Rust 1.90；Android 另需 SDK/NDK、JDK21 和与内嵌 Python 3.13 对应的构建 Python。签名密钥仅使用仓库外的本机配置，不提交私钥或密码。

```sh
python -m pip install -r requirements-build.txt -r ota-requirements.txt
python -m unittest discover -s tests -v
npm --prefix ui ci
npm --prefix ui run build
python build.py
```

build.py 在 Windows x64 或 Apple Silicon macOS 原生构建。Android 使用 build-android.py，其 SDK/JDK/Python/签名配置参数见 mobile/README.md；所有构建产物在 artifacts/，不提交到 Git。

完整本机流程和固定工作区规则见 [构建指导](docs/BUILD.zh-CN.md)。运行时资源随本仓库固定，不需要 E5 或 MU300 源码才能构建安装器。可选固件集成回归显式指定两边源码位置，缺失则跳过，不伪称通过。

本仓库不包含 Linux 内核、rootfs、机型固件或用户 NV。两边固件仓库在 installer/ 通过 Git submodule 引用本仓库的固定提交。
