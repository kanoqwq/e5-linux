# op系统切换器

适用于 E5 仓库已适配机型，需要 root。根据当前 Android A/B 动态选择另一槽的 OpenWrt，
不要求 Android 固定在 A；远航 41S 的 Android B／OpenWrt A 也按同一规则处理。
APK 文件为 `op-system-switcher.apk`，包名保留 `com.minikano.e5switch`，可覆盖安装旧版。

点开应用选择 OpenWrt 或 Android，确认后只设置下次启动，再选择是否立即重启。
弹窗跟随系统深浅色，文字、按钮和背景使用明确配色。

## 切换范围

- 只读取 misc 和当前 Android 槽位；拒绝不支持的槽位、无效控制块和错误 CRC。
- 不判断 OpenWrt 是否安装，也不读取 B 槽镜像、内核命令行或 TF 卡。TF 卡与内置存储安装使用相同的槽位切换。
- 只修改 misc 偏移 2048 的 32 字节启动控制块并回读校验。
- 不挂载 OpenWrt 分区或镜像，不写 `boot-os`、`default-boot`，不修复文件系统或同步启动链。
- 不更改原有默认启动策略。需要永久默认启动时，进入 OpenWrt 后在网页／屏幕设置。
- 临时文件只放 Android 的 `/data/local/tmp`，完成后删除。
- 是否能进入 OpenWrt 由已安装的启动镜像与根文件系统决定；存储缺失或系统损坏仍可能启动失败并回退 Android。

5.0 移除了旧版对 `boot_b` 固定偏移处 `loglevel=5` 的硬编码检查。
该字符串不是安装标记，会把使用不同命令行或镜像布局的已安装系统误判为未安装。

旧版 `e5-switch.apk` 会读写挂载 e5root 来改变默认启动设置，请换成新版。
本工具不能用于 MU300／F50／V50，其槽位和启动规则不同。

## UFI 网页插件

完整安装包另附 `extras/openwrt-system-switch-plugin.js`。v5 支持 E5、T551M、
M8、T512N、远航 41S（ZTE 7546N）和守护宝 K1，以及 MU300 系列。
E5 系列不再要求 Android 固定在 A 槽，也不再以 `verifiedbootstate=orange`
作为切换前提；仍核对已安装的 OpenWrt 镜像格式、当前 Android 槽位和 misc CRC。

插件只备份、修改并回读 misc 的 32 字节控制块，不挂载根文件系统、不改默认
启动策略；验证成功才重启。APK 和 JS 均不能代替首次安装及安装器的启动链同步。

## 安装

发布包包含 `extras/android/op-system-switcher.apk`，在 Android 中打开安装，
首次使用时授予 root。也可在电脑上执行：

```sh
adb install -r op-system-switcher.apk
```

## 构建与测试

Windows Git Bash，Android SDK + JDK：

```sh
./build.sh
```

构建运行启动控制块 Java 测试，使用原有 debug.keystore 签名，并生成 APK 源码／二进制指纹。
不要删除自己的 keystore，否则不能覆盖原来签名的安装。
`openwrt/bundle/finalize-package.py` 校验指纹后才把 APK 加入发布包。

```sh
python3 test/test_switcher.py
```

测试检查深浅色文字／按钮对比度，并在 Linux/WSL 的临时普通文件上执行实际读取与写入命令，
核对只改 misc 的 32 字节、回读一致、临时文件清理、OpenWrt 根内容不变。
构建与静态检查通过不代表已在真机验证每种厂商主题。
