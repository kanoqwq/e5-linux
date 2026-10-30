# Android 安装器与工具集移植

目标：Android 手机作为控制端，支持 OTG ADB、无线 ADB、SSH；共用现有
React 界面及 Python 安装事务，不要求控制手机 root。目标设备的 root、
动态 A/B、GPT、NV 和恢复校验沿用既有规则。

开发分支：`feat/android-installer`。当前已交付签名 APK，完整验收仍未完成；不能将 APK 可启动
或模拟测试通过表述为完整设备刷写验收。

## 架构

- `installer/ui`：桌面与 Android 共用 React 页面、主题、翻译、帮助和通知。
- `installer/mobile`：独立 Tauri Android 宿主，避免改变桌面程序的启动方式。
- `installer/mobile/plugin`：Android USB Host、系统文件选择、前台服务与
  Chaquopy 内嵌 Python。设备 I/O 不依赖手机 PATH、Termux 或另装 Python。
- `installer/backend`：共用分区、缩容、卸载、备份、NV 保留和补丁引擎。
  Android 的平台入口明确注入传输实现，不在桌面失败后静默切换算法。
- OTG 直接通过 Android USB API 传输 ADB 二进制协议，TCP 使用同一 ADB
  协议实现。SSH 复用 Paramiko。文件读写使用 Android 授权 URI 和私有工作区。

## 完整验收清单

- [x] ARM64 APK 原生构建，运行不要求电脑；已实际安装控制手机。
- [ ] 原界面、帮助、新手指引、Toast、日志、主题、多语言和移动排版。
- [ ] OTG 授权、拔插、重枚举、ADB 身份密钥及无线 ADB。
- [ ] SSH 更新、状态读取、重启及补丁更新。
- [ ] 完整 ZIP/目录导入、流式处理、容量预检和导出。
- [ ] TF、镜像、独立分区、重装、切系统、安装后重启。
- [ ] 在线保数据缩容、明确确认后的格式化后备、两阶段续装。
- [ ] 卸载归还空间、免擦扩容、恢复记录导入导出。
- [ ] Diff Studio、旧 JSON 摘要输入、补丁导出、摘要生成。
- [ ] 前台任务、切后台/屏幕旋转、进程终止后恢复，不重复写盘。
- [ ] 两仓库适用回归；真实硬件写入按用户授权执行。

平台依据：Tauri Android 原生插件、Android USB Host、Chaquopy 内嵌 Python。
Android 入口不能执行打包的 Windows/macOS 程序，也不能把下载到 app 数据
目录的桌面可执行文件作为 Android 后端。共用的是源码、协议和业务流程。

## 构建边界

使用本机现有 `D:/Android/Sdk`、已有 NDK、Rust/Cargo 与 Gradle 缓存。
构建入口仍通过 `tools/build-release.py run` 记录并获取工作区锁；不会复制
内核、rootfs 或建立另一份 WSL 构建工作区。Android 宿主和设备固件分别构建。

## 当前实现及证据（2026-10-30）

一个 APK 内包含安装器、Diff Studio、补丁更新、版本摘要四个入口；完整包
选择决定 E5 或 MU300 适配器。控制端不使用下载的桌面 adb/python 可执行文件。
桌面执行仍走原路径，Android 必须显式安装自己的平台服务。

已接入 USB Host ADB、经典 TCP ADB、Paramiko SSH、系统文件选择器和目录
选择器、只读备份 DocumentsProvider、前台任务通知、任务期间续期的 wake
lock、进程内页面重建恢复、私有备份/安装记录导出及跨平台恢复记录重定位。
USB 授权拒绝后由“刷新设备”明确重试，后台等待不重复弹授权窗口。

共用安装事务继续负责 TF、userdata 镜像、独立分区、在线缩容、格式化后备、
续装、卸载扩容、更新和重启；没有另写一套 Android 分区算法。包导入检查
路径、重复成员、大小和剩余空间。原生传输保留字节流，不用 PTY 或 Base64。

第一轮模拟器已实际完成 JNI/Python 启动、MU300 合成发行包分析、JSON 摘要
等价分析、差异包导出（单份 OTA、单份 APK、共用桌面运行库）、四页切换。
报告在 `installer/verification/android-native-regression.json`。该轮使用初始
Python 3.12 测试宿主，不代表后续 Python 3.13 或任何真实刷写验收。

最终宿主改用 Python 3.13 和已检查的 16 KiB 对齐原生依赖；APK 校验器同时
检查 ARM64 主库及 Chaquopy 内所有扩展库，未对齐则不能产出正式交付件。
签名 ARM64 APK 初次构建约 30.3 MiB。手机端及新依赖回归尚在继续。

## 本机 Android 构建

固定输入：SDK `D:/Android/Sdk`，NDK `27.0.12077973`，已有 JDK 21
`C:/Users/kano/.gradle/jdks/eclipse_adoptium-21-amd64-windows.2`，Rust 1.90.0。
构建 Python 为 `D:/Android/Python313-portable/python.exe`，来自 Python 官方
NuGet 3.13.9，来源与散列保存在同目录 `openwrt-source.json`。不改系统 PATH。
Python 3.12 不能代替：Chaquopy 的构建解释器必须与机内 Python 3.13 匹配。

通过本仓库 `tools/build-release.py run --workspace /home/kano/openwrt-build`
调用 Windows PowerShell，再运行：

```powershell
C:/Python312/python.exe installer/build-android.py `
  --sdk D:/Android/Sdk `
  --jdk C:/Users/kano/.gradle/jdks/eclipse_adoptium-21-amd64-windows.2 `
  --build-python D:/Android/Python313-portable/python.exe `
  --signing-config C:/Users/kano/.android/openwrt-tools-signing.json
```

发布密码通过 `OPENWRT_ANDROID_STORE_PASSWORD` 与
`OPENWRT_ANDROID_KEY_PASSWORD` 传入，不放进命令参数或仓库。
本机发布密钥及其私有记录在用户 `.android` 目录，需独立保管，不能重建后
冒充原签名更新。`--debug` 用于调试宿主；`--target x86_64 --debug` 仅用于
模拟器，不能当作 ARM64 手机发布包。

产物在 `installer/artifacts/android/`，各 APK 有对应的源码、ABI、页大小及
二进制散列记录。正式交付位置仍需遵守仓库 `out/releases/` 规范。

## 后续验证边界

尚不能把共享后端回归或模拟器测试称为 OTG 真机刷写通过。控制手机的 App
安装和 UI 验证已获用户许可；目标设备的分区、NV、刷写、卸载等破坏性验收
仍需另行明确授权。现阶段不借测试之名对已连接的 F50 执行这些操作。

无线 ADB 对齐原桌面 IP:5555 流程；Android 11 的配对码/TLS 配对不在既有
桌面功能内。Android 系统仍可终止进程，恢复依赖已落盘事务检查，不能承诺
前台服务绝不会被厂商省电策略结束。用户应将恢复备份导出到手机文档；卸载
控制端 App 会删除其私有记录。


## 手机和平板布局调整（2026-10-30）

Android 使用独立布局，保留共享业务和桌面页面：

- 手机底部放安装器、Diff、更新、摘要四个工具入口；平板改为左侧导航。
- 首页使用完整操作卡片；安装步骤收成可展开的一行，主内容统一滚动。
- 文件选择、检查结果与生成操作按顺序排列；平板在空间足够时显示双列。
- 语言、主题、帮助与备份入口收进紧凑标题栏；通知显示在底部导航上方。
- 导入和处理时显示进度，任务进行中禁止切换工具，保留既有操作互斥。

本轮生产前端验证覆盖中文／英文、360×740、393×851、844×390、
800×1280、1280×800，共 40 个页面与尺寸组合。检查横向溢出、导航位置、
主内容滚动、步骤展开和摘要导出。另检查帮助页与安装表单，桌面 UI 冒烟
及 21 项 Android 宿主单元测试通过。上述页面测试使用模拟桥，不代表目标
设备的安装、分区或 NV 硬件验收。


新版签名 ARM64 APK 已覆盖安装到用户授权的一加 PHB110 控制手机。
真机完成四个工具页面切换、安装表单和折叠步骤条检查；记录在
`installer/verification/android-phone-layout-regression.json`，截图在同目录。
本轮未选择目标设备，未执行目标分区、NV 或刷写操作。
交付 APK：`out/releases/20261030-android-responsive/OpenWrt-Tools.apk`。


## 2026-10-30 文件选择和弹窗修正

真机捕获到原生返回 `No command nativeCommand found`：Tauri 将 IPC 方法名
转换为 camelCase，Android 插件原方法名仍为 `native_command`。现已改为
`nativeCommand`，并保留前端 IPC 名，所有原生工具共用这一入口。桥接层将
结构化拒绝转换为可读错误，不再显示 `[object Object]`。系统文件窗口在 UI
线程启动，启动失败会释放窗口占用状态。

已在授权控制手机上实际选择完整 ZIP，导入并通过共享包校验。用户随后确认
文件选择已可用，要求停止继续测试，已停止自动测试和手机操作。目录/导出的
自动化验证未完成，不能记为通过。此前 80 项弹窗布局检查及 3 项导出事务
单元测试通过；最后的群号水平居中和帮助按钮单行调整未再测试。

手机的帮助、引导、赞赏和关于页面使用独立固定标题/操作区与滚动正文；不再
打开帮助便自动弹出键盘。导出仅发布当前已完成操作的目标文件，失败的残留
文件不会由后续其他请求自动发布。完整导出若文档提供方写入失败，仍保留于
App 备份入口，供用户取回。


Android 安装器导航再次点击会返回本页内的主页并滚动到顶部，保留已加载的包和
日志；活动任务沿用原有互斥，不重载正在运行的事务。赞赏自动提示按 WebView
的 App 会话记录，切换工具或重新载入安装器不会重复触发；新 App 会话仍可
提示，从关于页手动打开不受此记录影响。按用户要求未继续执行自动或实机测试。


## Release 签名与记录管理（2026-10-30）

新的发布密钥按用户指定保存在
`C:/Users/kano/OneDrive/UFI-TOOLS_KEY/OpenWrt-Tools-release.jks`，别名为
`openwrt-tools`，证书 CN 为 minikano，邮箱为 kanoqwq@qq.com。密钥密码不写入
仓库；本机私有配置为 `C:/Users/kano/.android/openwrt-tools-signing.json`。
旧密钥与原配置保留，并生成签名轮换凭据。构建改用：

```
C:/Python312/python.exe installer/build-android.py --sdk D:/Android/Sdk --jdk C:/Users/kano/.gradle/jdks/eclipse_adoptium-21-amd64-windows.2 --build-python D:/Android/Python313-portable/python.exe --signing-config C:/Users/kano/.android/openwrt-tools-signing.json
```

仍需通过 `tools/build-release.py run` 执行。本机签名配置不放入发布包。轮换
使用 Android 9+ 的新证书证明链，Android 8 使用旧签名以保留兼容性；实际
覆盖安装未继续做硬件测试。

手机首页和关于页新增“安装记录管理”：列出机型、版本、状态、时间、占用
空间、记录详情；导入和导出沿用既有完整恢复 ZIP。删除需要二次确认，只
删除本机事务目录及其中备份，不能删除正在使用的记录，也不删除安装包、
ADB 私钥或目标设备内容。记录管理仅做源码接入与编译，未继续执行自动测试。


## 记录页闪退的实机日志定位

用户点击记录管理后，2026-10-30 18:41:59 与 18:42:22 的 Android 崩溃日志为
`ForegroundServiceDidNotStartInTimeException`。快速请求结束时调用 stopService，
可能取消尚未执行 startForeground 的服务启动，导致系统超时强制终止 App。
现改为以服务启动命令队列传递完成信号：每个 onStartCommand 先完成前台通知，
再通过对应 startId 停止，不取消尚未兑现的前台启动。普通 records 列表读取
不申请前台服务；长操作在工作队列内开始后才申请。此次仅读取已有崩溃日志，
未操作设备界面或执行额外测试。


## 恢复记录导入路径与暂存（源码收尾）

源码核对发现：旧流程只修正主记录的 legacy 路径，旧 JSON 中的 backup_dir
等路径仍可能指向原电脑或 Android 临时解压目录。现由共享 recovery_records
处理受原记录目录约束的路径迁移，主记录、旧 JSON 和嵌套备份路径一并更新；
备份镜像和 NV 文件不重写。导入以有归属标记的暂存目录完成后原子发布，失败
不显示半份记录；仅在成功后清理 Android 专属临时解压副本，外部原始 ZIP 不
删除。桌面源目录保留。该改动尚未运行测试，遵照用户停止测试的要求仅编译。


帮助内容也已改为平台对应的离线指南。Android 使用 installer/mobile/GUIDE.md
的中文／英文内容，桌面继续使用原 GUIDE.md，避免手机帮助仍指导用户运行
EXE、在电脑保存记录或安装 Windows 驱动。帮助组件、搜索和主题结构共用。


## 目标验收审计（2026-10-30）

源码移植和 APK 交付已经具备下列入口，但不能据此勾选所有硬件验收项。
最近交付使用 Python 3.13、新 JKS 和签名轮换凭据；每个发布目录内有源码与
产物散列记录。测试停止后新改动只经过编译及打包校验，没有补跑测试。

| 要求 | 当前实现与证据 | 验收缺口 |
| --- | --- | --- |
| 独立 Android 工具集 | mobile 宿主、签名 ARM64 APK，已安装 PHB110 控制手机 | 无构建输入缺口 |
| E5/MU300 区分与共用流程 | package.py 识别、adapter.py 分流、Provider 绑定各包入口 | Android 上两系列全流程尚未验收 |
| UI、帮助、主题、通知与布局 | React 共用页面；40 项布局和 80 项弹窗检查为此前基线，用户已反馈并确认部分修复 | 最后几轮 UI 改动未再测试 |
| OTG 与无线 ADB | UsbHost、adb-shell、密钥和二进制文件传输已接入，宿主单测和 TCP 回环有证据 | 尚未做控制手机连接真实目标的 OTG 完整操作 |
| SSH 完整更新与补丁 | Paramiko 和原共享更新器已接入，依赖随 APK 分发 | 手机原生 SSH 夹具因网络/端口受限未完成，不能记为通过 |
| 安装、缩容、续装、卸载与重启 | 复用 Service、OnlineShrink、Uninstaller 和原各包脚本 | 旧共享后端基线为 472 项（84 跳过）；不能证明当前 Android 整体刷写通过 |
| Diff、JSON、导出 | 四个工具入口，ZIP/目录/rootfs、批量摘要和补丁载荷共用；初始 Python 3.12 模拟器五轮通过 | Python 3.13 最终宿主的整轮原生管线未完成 |
| 记录管理与恢复 | CRUD、完整 ZIP、路径重定位、原子导入和暂存清理已实现 | 最后新增路径/管理功能尚未验证；用户要求不继续测试 |
| 生命周期与恢复 | 进程内 Runtime、原事务落盘、前台服务及完成信号顺序已实现，已定位并修复记录页启动超时崩溃 | 最终修正版未再主动操作手机验收 |
| 目标设备写入边界 | 本次仅安装/更新控制 App，没有执行目标分区、GPT 或 NV 验收 | 破坏性验收必须另获用户明确授权 |

当前剩余关口是验收证据，不再自动测试或操作手机。用户已要求停止继续测试，
因此目标不能宣称全项完成；后续须用户实测反馈，或明确允许继续对应验证。
源码分支仍为 feat/android-installer，未擅自提交、合并或推送。


## 2026-10-30 进度名称与按钮宽度

按用户截图修复进度卡片的长名称：优先显示 release_version，容器允许收缩，
长文本在卡片内最多显示两行。手机上的进度操作按钮改为纵向完整宽度；其他
操作/备份按钮允许内容换行和自适应高度，避免核验文字与图标被裁切。帮助
页底部短按钮保留此前的单行排版。此次只编译打包，未操作手机或正在运行的
安装任务，也未继续执行测试。

### 控制端用语与 Android 返回导航（2026-10-30）

连接、调试授权和联网说明统一使用“当前控制端”，本地安装记录统一称“本机”，同步调整英文，手机和平板不再被称为电脑。保留明确描述桌面 EXE 的帮助内容。

四个工具页面通过 Tauri 原生返回键事件处理 Android 返回操作：优先关闭当前弹窗；安装器子页面返回主页，其他工具页面回到安装器主页；仅主页空闲时关闭 Activity。进行中的任务仍由前端状态和后端 can_close 共同阻止离开，不中断设备安装。监听随页面卸载注销。

本轮只进行源码修改、APK 编译与发布校验；遵照用户要求未增加自动化或实机测试，也未覆盖安装控制端正在运行的 App。
