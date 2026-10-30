# Agent 本机构建指导

## 工作区与边界

本机 Windows 固件仓库为 D:\Git\e5-linux 与 D:\Git\mu300-linux-clean-tf-7.2；两边 installer/ 都是本仓库子模块。已有 Node/Cargo/Gradle/Python/SDK 缓存不复制、不迁移。优先在已有缓存的 E5 installer/ 子模块构建一次，两边复用同一份经 sources() 校验的桌面交付件。

Linux/WSL 唯一工作区 /home/kano/openwrt-build。独立仓库通过 tools/build-release.py run 使用同一个 locks/openwrt-release.lock；从固件根目录构建时也可沿用父仓库工具。不要同时嵌套调用两个 runner。产物 artifacts/，正式独立工具发布 out/releases/，日志和状态 out/build-logs/ 与 out/build-state/。

## 离线验证

```powershell
$env:OPENWRT_E5_SOURCE='D:/Git/e5-linux'
$env:OPENWRT_MU300_SOURCE='D:/Git/mu300-linux-clean-tf-7.2'
C:\Python312\python.exe -X utf8 -m unittest discover -s tests -v
npm --prefix ui run build
```

省略两个源码变量时，核心测试仍可运行；需要对应固件入口的集成测试会明确跳过。不要把 skip 数量算成通过。POSIX 设备脚本和临时文件系统测试在 WSL 执行，不连接真实设备。

## Windows

在 WSL 仓库目录用固定 runner 调用本机 PowerShell。已有本机 Python C:\Python312\python.exe、Node/Rust 环境；ADB 输入使用已有 platform-tools，不重复下载。

```sh
python3 tools/build-release.py run --workspace /home/kano/openwrt-build --label windows -- /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -Command 'Set-Location D:\Git\e5-linux\installer; $env:OPENWRT_TOOLS_WORK="D:/Git/e5-linux/work"; & C:\Python312\python.exe build.py --adb-dir D:/Git/e5-linux/work/installer-platform-tools/platform-tools; exit $LASTEXITCODE'
```

仅当已核实当前 UI/Rust 源码与现有二进制一致才能使用 --skip-ui。sources()、二进制摘要和运行资源必须匹配，不得手改 build.json 冒充重编。

## Android ARM64

复用 SDK D:/Android/Sdk、NDK27.0.12077973、既有 JDK21、Python3.13 与 Gradle/Rust 缓存。

```sh
python3 tools/build-release.py run --workspace /home/kano/openwrt-build --label android -- /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -Command 'Set-Location D:\Git\e5-linux\installer; & C:\Python312\python.exe build-android.py --sdk D:/Android/Sdk --jdk C:/Users/kano/.gradle/jdks/eclipse_adoptium-21-amd64-windows.2 --build-python D:/Android/Python313-portable/python.exe --signing-config C:/Users/kano/.android/openwrt-tools-signing.json; exit $LASTEXITCODE'
```

签名配置与密钥在仓库外，密码不写命令行或 Git。保留现有证书轮换 lineage，保证覆盖升级身份一致。只在用户确认控制端空闲时安装 APK；构建不等于实机安装/分区验收。

## Apple Silicon macOS

用户没有本地 Mac Agent。macOS App 只能使用真实 Apple Silicon 原生环境；按用户授权可使用本仓库的 macOS GitHub Actions。不能用旧 App 冒充新源码，也不能要求用户恢复不存在的 Mac 连接。下载产物后核验 sources()、架构及文件摘要，再接入父仓库打包。

## 两边接入

子模块固定到本仓库提交，构建后更新父仓库的 gitlink。package_gui.attach 允许通过 OPENWRT_GUI_ARTIFACT 与 OPENWRT_GUI_MACOS_ARTIFACT 指向同一份已校验交付件，避免两边重复编译。旧 artifact 指纹包含父目录资源路径，提取后必须第一次重编；以后只有相关源码变化才重编。

固件刷新/热补命令显式设置 OPENWRT_FIRMWARE_SOURCE。installer 本身无需固件源码即可构建；不得用相邻旧仓库文件兜底补缺输入。
