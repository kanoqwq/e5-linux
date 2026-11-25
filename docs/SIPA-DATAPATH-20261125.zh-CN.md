# E5 公共 SIPA 数据通路修正

日期：2026-11-25。根据 Pro 五项 SIPA 检查逐项核对 E5 的实际实现，
只补公共实现仍有的缺口。

## 核对结果

| 检查项 | E5 原状与本次处理 |
| --- | --- |
| 发送失败后的 skb 所有权 | 已释放的 skb 已返回 OK；本次把永久发送错误也改为计错、释放一次并返回 OK，流控错误继续 BUSY |
| 重试包头 | 已有 COW／空间检查、条件回退及校验和还原，本次保留 |
| 普通 NCM 触发旁路 | 不存在 Pro 那段误触发逻辑，不增加额外开关或判断 |
| TX 完成队列 | 已按实际前缀推进并继续回收后续项；本次给未匹配项补最多三次 DMA 重新同步 |
| RX DMA | 修正持久页映射／同步方向，补首次填充、复用及接收校验前的 CPU／设备所有权交接 |

持久页包含设备写入的数据和 CPU 写入的 skb_shared_info，故采用
DMA_BIDIRECTIONAL；单独分配的 skb 仍采用单次 FROM_DEVICE 映射。
先取得 CPU 所有权再 build_skb；软件环接纳后才把缓冲同步给设备并发布。
初始化构造失败时不会错配后续 DMA 地址；错误接收先同步／解除映射再
校验或释放，并避免重复 unmap。

发送完成的重新同步发生在 send_lock 外。未知完成记录不释放其他 skb、
不虚增额度；保留既有错误计数、后续项回收和流控通知。
没有更改省电／充电策略、AT 所有者、队列深度或引入新的周期轮询。

## 交付路径

新补丁 devices/common/sipa-datapath-dma.patch 追加在 sipa.py 的公共补丁链。
E5、T551M 直接使用此入口；M8 及其余机型通过既有准备流程使用公共入口，
覆盖 e5、t551m、m8、t512n、yuanhang41s、k1、lym10k、changxing60、y60。
没有直接修改 donor 内核来代替仓库补丁。

补丁检查覆盖干净树、第一版旧补丁、上一版完整补丁链、重复应用的结果
一致性；未知混合源码拒绝后保持原文件。现有机型配套检查仍需正常执行。

## 离线及编译验证

- 直接编译生产 C：16 种 TX 协议／包头／校验和组合，每种 50 次重试，
  永久错误释放、资源恢复和既有校验和丢失的旧代码对照。
- TX 完成：未知项后仍回收有效项，NULL 描述符只推进已消费前缀，
  错误码、空队列迟到通知及重新同步后可见的完成项。
- 8 个接收测试方法含初始化、三条补充路径、批次边界、分配／映射失败、
  所有权环满、部分提交后重试、页面引用及错误接收的同步／释放。
- 既有资源请求／释放次序、Ethernet／USB 队列通知次序全部通过。
- 在已登记的 E5 donor 与 K1 来源上分别运行完整补丁／C 测试，通过；
  ASan／UBSan 未报告错误。
- 用既有 E5 私有源码视图构建 6.18.54-e5-mobile4 的 Image 和完整模块，
  编译日志确认 sipa_skb_recv.o、sipa_skb_send.o、sipa_eth.o 实际重编。
  构建附带的 USB、扫描、充电、电池及模块配套回归通过。

日志：

- out/build-logs/20261125-090914-sipa-final-regression-8379/command.log
- out/build-logs/20261125-090952-sipa-k1-source-regression-8653/command.log
- out/build-logs/20261125-090616-sipa-e5-native-kernel-88844/command.log
- /home/kano/openwrt-build/objects/e5/build.log

| 产物 | SHA-256 |
| --- | --- |
| E5 Image | 1e65f157db5b698fee1f882487856333e6fb4a7e9f4e3a4c3013444ec3e4820b |
| E5 sipa-core.ko | 7d64119610aaccfeedd415234c3be1ba13a96d8c337e2717c080eecf44fd847a |
| E5 sipa_eth.ko | 93165595fcb24e96d6713bfadf2f8fc36cb175db06097b004d8d8873f8c45b96 |

接收测试沿用同工作区 Pro 的夹具基础并适配 E5 的 sipa_sys_eb 条件。
本轮仅实际构建 E5 代表机型；其余机型发布前仍须通过各自构建器重编匹配
内核／模块，不能拿 E5 产物代替。没有生成新 ZIP，也未部署或进行硬件验收。
