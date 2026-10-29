# 畅行60安装器更新：遗漏 UFS boot 分区

## 已确认的原因

2026-10-29 的畅行60安装器诊断中，系统、固件、设置和 NV 备份校验均通过，
最后输出 `staged offline root update` 后命令以 2 退出，没有进入 `writing boot_`。

`openwrt/device-flash-boot.sh` 仍只扫描 `mmcblk*p*`。畅行60使用 UFS，
因此查找 Linux 槽 boot 时找不到分区，脚本在写入之前退出。
离线更新请求在 boot 回读校验后才启用，本次日志未到该步骤；上传与备份已完成，
但日志不支持“已经重写系统或 boot”的判断。

## 修复

刷 boot 的公共脚本使用已有 `boot/storage.sh`，覆盖 eMMC 和 UFS。
Linux 槽仍取自运行时的动态 A/B 角色；从 misc 所在的内置磁盘查找对应 boot，
缺失、歧义或角色冲突时在写入前明确报错。不按枚举顺序选择磁盘。

脚本与 storage.sh 已由各正式构建入口打包，更新器也会一起上传。
快速刷新发布流程新增此脚本的替换和哈希登记；不修改原包，不重编未变的内核，
boot、rootfs 和 ext4 工具保持原始字节及固件版本。

## 验证及边界

`openwrt/tests/device-flash-slots.py` 执行真实脚本及存储解析，覆盖 eMMC/UFS、
Android A/B、短启动记录、冲突角色、缺失及重复 boot 标签。
成功时检查只写 Linux boot 和 misc 的32字节记录，保留 Android boot 与其余字节；
拒绝时检查未写入。
`tools/tests/refresh-release-installers.py` 检查修订脚本、全包校验和、无重复成员，
并验证 boot/rootfs/ext4 工具字节不变。

以上为本地临时文件和模拟 sysfs 的回归，不等同于畅行60实机更新验收。
