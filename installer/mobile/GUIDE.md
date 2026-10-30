# OpenWrt Tools for Android

## 中文

控制手机不需要 root。此 App 可使用 E5 系列或 MU300 系列的完整发行包；导入后自动选择对应适配器，具体机型、分区、A/B 槽位和备份核验仍由各自安装流程处理。

在手机安装 APK 后，可从底部导航（平板为左侧导航）进入安装器、Diff Studio、补丁更新、版本摘要。可导入完整 ZIP 或选择已解压目录，不需要在手机上运行 EXE、另装 Python 或 Termux。

### 选择用途

- **安装 OpenWrt**：首次安装到目标的 TF 卡或独立内置分区。先校验完整包，再检查设备、查看计划并确认。
- **更新 OpenWrt**：目标先启动 OpenWrt，手机通过它的管理地址和当前 root 密码连接 SSH；保留设置、已有容量和本机固件。以最终兼容性检查为准。
- **继续未完成的安装**：从本机保存的记录恢复；换手机后先导入完整恢复备份。
- **临时启动已安装的 OpenWrt**：目标在 Android 中开放 ADB 并提供 root 后，按原流程选择临时启动，不改变默认启动选择。
- **高级选项**：重装内置分区、实验性 userdata 镜像、卸载并归还空间。镜像方式可能损坏 Android F2FS，使用前先阅读说明。
- **安装记录管理**：查看机型、版本、状态、时间及占用空间；支持搜索、导出、导入和删除。

### TF 卡

TF 卡安装只格式化选定的目标分区，不重新划分卡。会删除选定目标内的数据，开始前核对卡和分区。可用容量、文件数及载荷大小由安装计划检查。

### 独立内置分区

先检查现有未分配空间，足够时不切割 userdata。不足时优先检查原厂 Android 是否支持在线 F2FS 保数据缩容；支持性由目标内核、文件系统和实际几何信息判断，不因为 data 加密便直接要求清空。

在线方案不可用时，会说明原因并询问是否准备清空数据的后备计划，不会自动确认格式化。核对 OpenWrt 容量、Android 保留空间和数据影响后再开始。UFS/eMMC 的设备节点和扇区大小沿用各机型探测结果。

### 加密 userdata：重置后自动续装

如果确认了需要清空数据的方案，目标首次离线准备可能持续数分钟，之后会自动重启。目标 USB 断开、Android 初始化和调试授权都是正常等待阶段。

完成目标 Android 初始设置，重新开启 USB 调试并允许这台控制手机的 ADB 密钥。已有 root 会直接使用；只有 root 不可用时才需要按目标机型恢复 Magisk。App 会等待目标重新就绪，再核对设备身份、记录和备份。

保持控制 App 的安装记录和备份。关闭后重新打开“继续未完成的安装”；不要用一个新的安装计划覆盖原等待流程。

### 更新与自动重启

完整更新使用“更新 OpenWrt”，通过 SSH 传输并接入原来的离线更新流程。输入目标管理地址和当前 root 密码，查看检查结果后确认。需要重启时按界面提供的按钮和等待提示操作，不要反复提交相同请求。

Diff Studio 的旧版输入可以是完整 ZIP 或 JSON 版本摘要，新版输入为完整 ZIP。分析后可保存差异包，导出名仍可修改。补丁更新页支持原始 OTA 或包含一份 update.ota 的 ZIP，通过 SSH 检查并更新；目标版本须符合补丁起始版本。

版本摘要页支持 ZIP、解压目录和原始 rootfs，可选 1/2/4/8 个线程处理目录集合；单版导出 JSON，批量导出 ZIP。摘要只能替代旧版输入，不能代替新版完整包或恢复备份。

### 卸载内置 OpenWrt 并归还空间

目标先回 Android，开启调试并提供 root，在高级选项选择卸载。工具会核对本机记录及目标现场；能否保留 Android 数据取决于文件系统、加密映射、原厂工具和分区几何检查。

缩容安装可以按检查结果归还空间；使用原有空闲空间安装时，通常释放为未分配空间。无法保留数据时，必须再次确认清空并重新查看计划。只删除手机里的安装记录不会卸载目标 OpenWrt，也不会归还目标空间。

### 暂停、关闭与失败恢复

记录、备份和 ADB 身份保存在本 App 私有空间。首页或“关于与备份”可打开安装记录管理。导出恢复 ZIP 会带上记录及其目录内的备份；导入时更新本机路径，不改动分区备份字节。

删除记录必须二次确认，会删除其本机备份，正在使用的记录不能删除。卸载控制 App 也会清除私有记录；换手机或卸载前先导出重要恢复备份。

前台任务通知可以返回正在进行的操作。Android 仍可能终止进程，恢复以已经落盘的事务为准。遇到错误可展开详细日志并复制或导出诊断；暂停不会撤回已经写到目标的操作。

### 连接与常见问题

**OTG 怎么连？** 控制手机需要支持 USB Host，使用有数据功能的 OTG 线。先允许控制手机访问 USB，再在目标 Android 允许这台手机的 ADB 密钥和 su。拒绝后点击刷新设备重新请求；多台设备时明确选择目标。

**无线 ADB 怎么连？** 手机和目标应网络互通，展开无线 ADB 连接，输入 IP:5555 或实际端口。此入口支持经典 TCP ADB，不包含 Android 开发者选项的配对码/TLS 配对流程。

**为什么没有设备？** 确认目标已正常启动、USB 角色正确、数据线和调试授权可用。只有充电功能的线无法传输 ADB。连接后先刷新并检查目标，不要连续点击安装。

**导入或导出在哪里？** 使用 Android 系统文件选择器选择 ZIP、目录或保存位置，云端文件需等待读取完成。“打开事务目录”是只读文档入口，不公开 ADB 私钥。目标固件包与手机 App 是不同文件，不要把 APK 当作 OpenWrt 安装包。

## English

The control phone needs no root. This app accepts full E5-family and MU300-family releases and selects their respective adapters. Model, partitions, A/B slots and recovery checks stay with each installation workflow.

Install the APK and use the phone bottom navigation or tablet sidebar for Installer, Diff Studio, Patch Updater and Release Index. Import a complete ZIP or an extracted folder. No Windows EXE, separate Python or Termux is needed on the control phone.

### Choose your purpose

- **Install OpenWrt**: first installation onto the target TF card or independent internal partition. Verify the full release, inspect the target and review the plan before confirming.
- **Update OpenWrt**: boot OpenWrt on the target and connect through its management address and current root password over SSH. Settings, existing size and device firmware are preserved, subject to the final compatibility check.
- **Continue an unfinished installation**: resume a saved record. Import a complete recovery backup first when changing control phones.
- **Boot installed OpenWrt once**: connect to the rooted target Android through ADB and follow its original one-time boot workflow without changing the default system.
- **Advanced options**: reinstall the internal partition, use an experimental userdata image, or uninstall and return space. The image method can damage Android F2FS; read its explanation first.
- **Installation records**: view model, version, status, date and storage; search, import, export or delete records.

### Storage and capacity

TF installation formats only the selected target, without repartitioning the card. It erases that target's contents; check the card and partition before starting.

Internal installation first checks existing unallocated space. Otherwise it checks whether the stock Android kernel supports online F2FS shrinking with data preservation. Encryption alone does not force an erase-data plan. Availability depends on the actual kernel, filesystem and geometry.

If preservation is unavailable, the app explains why and asks whether to prepare an erase-data alternative. Formatting is never automatically confirmed. Review OpenWrt size, retained Android space and data impact before proceeding. UFS/eMMC nodes and sector sizes follow the model's detected storage.

### Encrypted userdata and reset

When an erase-data plan has been confirmed, the first offline preparation may take several minutes and reboot automatically. Target USB disconnects, Android setup and authorization are expected waiting stages.

Complete target Android setup, enable USB debugging and authorize this control phone and su again. Existing working root is accepted; restore Magisk for that model only if root is unavailable. The app waits and then rechecks device identity, records and backups.

Keep the control app's records and backups. After reopening, use Continue an unfinished installation rather than creating a second plan over the pending workflow.

### Updates and automatic restart

Use Update OpenWrt for a full SSH update through the original offline workflow. Enter the target management address and current root password, review the result and confirm. Use the provided reboot and waiting controls instead of submitting the same operation repeatedly.

Diff Studio accepts an old full ZIP or saved JSON index and a new full ZIP. Analyze and export the patch; the suggested filename remains editable. Patch Updater accepts raw OTA or a ZIP containing one update.ota, checks the target over SSH and applies it. The target must match the patch base version.

Release Index accepts ZIPs, extracted folders and raw rootfs. Collections can use 1/2/4/8 workers; a single input exports JSON and batches export ZIP. An index replaces only the old release input, never the new release or a recovery backup.

### Remove internal OpenWrt and return space

Return the target to Android, enable debugging and root, and choose uninstall under Advanced options. The installer checks host records and target evidence. Android data preservation depends on the filesystem, encryption mapping, stock tools and partition geometry.

Space from a shrink installation can be returned when verified. An installation in pre-existing free space is normally released as unallocated space. An erase-data alternative requires another confirmation and a new plan. Deleting a control-phone record does not uninstall target OpenWrt or return target space.

### Recovery and results

Records, backups and ADB identity stay in this app's private storage. Open Installation records from Home or About & backups. A recovery ZIP contains its record and local backups. Import relocates host paths without rewriting partition-backup bytes.

Deletion requires confirmation, removes that record's local backups and is unavailable for an in-use record. Uninstalling this control app also removes its private data. Export important recovery backups before changing phones or uninstalling the app.

The foreground notification returns to active work. Android may still terminate a process; recovery relies on persisted transactions. Expand detailed logs and copy or export diagnostics when needed. Pausing cannot undo writes already performed on the target.

### Connection and troubleshooting

**OTG:** the control phone must support USB Host and use a data-capable OTG cable. Grant USB access on the control phone and authorize its ADB key and su on target Android. Refresh devices to retry a denied permission. Select the intended target explicitly when several are connected.

**Wireless ADB:** the phone and target must be reachable. Enter IP:5555 or its actual classic TCP ADB port. This entry does not implement Android developer-settings pairing-code/TLS pairing.

**Missing target:** check target boot, USB role, data cable and debugging authorization. A charging-only cable cannot carry ADB. Refresh and inspect the target before starting an installation.

**Import/export:** choose ZIPs, folders and save locations with the Android document picker. Wait for cloud files to finish loading. Open transaction folder is a read-only document entry and never exposes ADB private keys. Target firmware and this control APK are different inputs; do not select an APK as an OpenWrt release.
