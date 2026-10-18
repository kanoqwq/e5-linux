# 主线内核版本号稳定化

参考上游主线构建方式：
https://github.com/sprd-linux-devs/e5-linux/blob/main/upstream/build.sh
https://github.com/sprd-linux-devs/e5-linux/blob/main/upstream/e5-mainline.config

公共配置关闭 `CONFIG_LOCALVERSION_AUTO`，所有主线构建入口显式
`export LOCALVERSION=`。两者配套，避免自动附加 Git 哈希、`-dirty`
或退回追加 `+`。覆盖容器、原生交叉编译，以及 E5、T551M、M8、T512N
机型入口；保留各机型原有后缀。

该环境贯穿 Image、模块编译和 modules_install，继续使用内核生成的
kernel.release 打包模块目录，不在打包后改名或修改 vermagic。
发布包原有 source_commit、source_fingerprint、source_dirty 记录保持不变。
`-dirty` 本来表示源码工作区有修改，与文件系统损坏无关。

验证：在临时 Git 仓库执行当前固定内核源码的真实 scripts/setlocalversion，
覆盖未打补丁、已打补丁、五种后缀及缺少显式 LOCALVERSION 时的 `+` 对照；
同时检查六个构建入口会清空继承的 LOCALVERSION。

```sh
KERNEL_TREE=/path/to/linux python3 upstream/tests/test_kernel_release.py
python3 openwrt/tests/upstream-build.py
```

这是构建逻辑验证，未重新编译内核、更新发布包或刷写设备。新命名在下次
完整编译 Image 和配套模块后生效；已部署设备和旧产物仍保留原版本号。
