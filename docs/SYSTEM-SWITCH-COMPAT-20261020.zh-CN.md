# 系统切换 APK / UFI 插件兼容性核对

## 结论与修改

- 当前 `op-system-switcher.apk` 已按 `ro.boot.slot_suffix` 动态推导 Android 和
  Linux 两槽；Android B / Linux A 也支持。APK 二进制与当前 Java/资源指纹
  完全匹配，不需要为本次检查重新编译。
- UFI JS v4 遗留了 E5 系列必须 Android A 且 verifiedbootstate=orange 的限制，
  型号列表也缺少 T512N、ZTE 7546N（远航 41S）、angelcare K1。v5 已补齐，
  并同步 MU300 仓库的共享插件副本；未更改 MU300 自身的型号/平台识别规则。
- APK、JS 与现行 boot 控制块布局一致：misc 偏移 2048、长度 32、BCAB v1、
  双槽、CRC32；Linux 设 priority=15/tries=2，保留 Android 成功状态作为回退。
- 两者不挂载 OpenWrt 根，不改默认启动配置，也不写 boot 或 NV。JS 只读
  boot 头尾确认已安装镜像，并保存 misc 备份；写入失败执行原控制块恢复。
  首次安装和启动链同步仍由安装器处理。

## 验证与边界

- Java 控制块测试通过，包括 Android A/B、幂等、恢复 Android、保留字段及坏 CRC。
- APK 的 5 项 Python 测试通过，包括已编译 APK 指纹、主题，以及真实 shell
  在临时普通文件上的 32 字节写入和临时文件清理。
- 两仓库 JS 各 14 项测试通过。覆盖当前全部型号及 A/B 两种 Android 槽，
  Bash/BusyBox 实际写入范围、部分写失败和回读错误恢复、互斥、并发变更、
  校验成功后才能重启，以及 UFI 持久 root shell 的命令结束标记。
- 使用本次已部署的 K1 boot 镜像头尾和原厂 Android B misc 存档核对，镜像
  识别通过，产生正确的 Linux A 控制块。此项是存档验证，没有在设备上执行切槽。

本轮没有切回 Android 运行 APK/UFI 做完整物理重启测试，也没有重新打包旧的
发布 ZIP。源码打包入口会把新版 JS 加入下次产物，现有旧包不会自动更新。
