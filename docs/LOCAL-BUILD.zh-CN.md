# 本地构建与打包规范

本文件是本地多机型构建的入口；`docs/RELEASE.md` 另行说明 GitHub Actions。
不再用 `work/` 下日期命名的 worker、复制旧 worker 或字符串替换执行旧脚本。
本规范不启动构建、不迁移目录、不清除历史文件，也不改变设备安装流程。

## 固定位置

| 内容 | 位置与规则 |
| --- | --- |
| 仓库源码 | 当前 E5 Git 工作区，不再额外复制整个仓库 |
| Linux 构建工作区 | 本机既有 `/home/kano/openwrt-build`，不迁往 C 盘 |
| 内核源码、对象 | 复用工作区的既有缓存；本次不重建或迁移各机型缓存 |
| 干净公共 rootfs | 工作区 `base/e5` 一份；不得使用含用户配置的实机 root |
| 内核配套交付件 | `tools/build-layout.json` 中各机型的 `kernel` 路径 |
| 打包临时根目录 | 工作区 `package-work/e5-release/<profile>/assembly` |
| 通用临时文件 | 工作区 `tmp/e5-release-*`，每步结束自动删除；不创建日期命名源码/root 缓存 |
| 任务日志、状态 | 仓库 `out/build-logs/<task>/`，最近任务 `out/build-state/latest.json` |
| 正式产物 | 仓库 `out/releases/<task>/`，全部机型校验通过才成为正式目录 |
| Windows/macOS 安装器 | `installer/artifacts/windows-x64`、`installer/artifacts/macos-arm64` |
| 排查资料、设备备份 | 仍在既有 `work/`、`dumps/`，不参与自动清理 |

`tools/build-layout.json` 是受版本控制的路径登记表，支持 `{repo}` 与 `{workspace}`。
路径对应现有输入，不把某台电脑的盘符硬编码进执行器。另一环境可显式传入
`--config /absolute/path/build-layout.local.json`，禁止随机搜索并挑一个旧包代替缺失输入。
内核交付件可以暂留旧位置；规范化流程不等于复制一份缓存到“新标准目录”。

## 分阶段执行

所有下列 Linux 命令在仓库根目录执行；本机先进入 `wsl -d Ubuntu-22.04`，
然后 `cd /mnt/d/Git/e5-linux`。工具默认工作区为当前 Linux 用户的
`~/openwrt-build`；使用 root/sudo 时明确指定 `--workspace /home/kano/openwrt-build`。

### 1. 只看计划和验证输入

```sh
python3 tools/build-release.py plan --profiles all
python3 tools/build-release.py check --profiles e5 t551m m8 t512n yuanhang41s k1
```

两者不创建构建目录，不复制 rootfs、不下载、不编译、不连接设备。
`check` 核验输入存在、软件包源码/哈希、两个安装器的源码指纹与文件哈希、
各机型内核和完整模块配套。缺失或过期即停止，不自动选另一份缓存。
复用内核仍需确认内核相关源码没有变更：已有 `container-support.json` 只能
证明交付件配套，不能证明当前源码已经编译进去。

### 2. 仅在相应源码变化时编译

编译命令通过固定入口执行，复用现有原生构建器，统一互斥、日志和状态：

```sh
python3 tools/build-release.py run --label modem -- bash openwrt/build-modemmanager.sh
python3 tools/build-release.py run --label wifi -- bash openwrt/build-hostapd.sh
python3 tools/build-release.py run --label block-mount -- bash openwrt/build-block-mount.sh
```

内核同样用 `run --label <model>-kernel -- <该机型原有命令>`。对应入口为：

| 机型 | 现有构建入口 |
| --- | --- |
| E5 | `devices/e5/build-kernel.sh` |
| T551M | `devices/t551m/build-kernel.sh`，发行构建设置 `T551M_RELEASE=1` |
| T512N | `devices/t512n/build-kernel.sh`，发行构建设置 `T512N_RELEASE=1` |
| M8 | `devices/m8/build-probe.sh`，完整配套由 `devices/m8/package-kernel.py` 生成 |
| 远航 41S | `devices/yuanhang41s/build-kernel.py` |
| K1 | `devices/k1/build-kernel.py` |

各构建器原有的 source、objects、baseline、output 参数仍需显式设置，指向
已经核对过的缓存；`run` 负责调度、日志，不会替你猜基线或重置源码。
某个共享对象目录目前属于哪个机型，必须先核对其 `.config` 和
`include/config/kernel.release`。源码打过其他机型补丁时也不能直接改配置硬编。
目前没有自动、安全切换所有模型源码基线的实现，禁止冒充“一键全量重编”。

Windows x64 与 Apple Silicon 安装器继续在对应原生环境运行
`python installer/build.py`。两端构建各一次，所有机型合包共用这两份 artifact；
不逐机型重复编译 GUI，不在 Linux 上伪造 macOS 构建。保留原有源码指纹校验。

### 3. 组装发布包

```sh
sudo python3 tools/build-release.py package --workspace /home/kano/openwrt-build --profiles all
```

也可用 `--profiles e5 t551m` 选择机型。该命令：

1. 先验证所有选中机型及 Windows/macOS 输入，发现问题在复制 rootfs 前停止。
2. 取得工作区互斥锁；按机型串行组装，同一时刻只新增一个打包 root。
3. 调用既有 `openwrt/build-profile-tf.py`，保留设备、隐私、模块和安装包校验。
4. 再核对 ZIP CRC、重复文件、每个文件 SHA-256，确保一份 boot 和一份系统镜像。
5. 每个机型成功后删除本任务持有的 staging，不累计每个日期的一份 rootfs。
6. 全部机型通过、源码指纹未变后，将 `.building-<task>` 原子改名为正式目录。
   内附 `BUILD-RUN.json`，记录源码、任务、日志、文件大小和 SHA-256。

组装过程中仍会编译原有的少量静态辅助程序；不会重编内核、ModemManager、
hostapd 或 GUI。正式目录本身包含唯一 ZIP，不另保留整份解压发布目录。

### 4. 查看状态与处理失败

```sh
python3 tools/build-release.py status
sudo python3 tools/build-release.py clean-staging --workspace /home/kano/openwrt-build --profiles t551m
```

每一步的命令、起止时间、状态与日志在 `status.json`。运行时打印日志路径，
可以用 `tail -f <日志>` 查看进度。失败明确记录为 failed，不宣称产物齐全。
保留的 `.building-*` 是未发布现场，不可提供给用户。

失败后先看日志；确认不需要现场再运行 `clean-staging`。它只删除固定目录内
带匹配所有权标记的打包 staging，拒绝符号链接、越界路径及仍有挂载的目录。
源码、对象、下载缓存、安装备份、其他工具旧 staging 和历史发布包都不在其范围。
需要整理旧目录时单独列清单处理，不能把 `docker prune` 或删除缓存当日常步骤。

`run`、`package`、`clean-staging` 使用同一把锁，防止清理与构建并行。旧脚本直接
执行无法被这把锁管住，所以本地构建必须经过此入口；CI 在独立 runner 继续原流程。
