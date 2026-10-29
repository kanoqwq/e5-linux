# 畅行60与一加11的多客户端吞吐调查

## 范围与版本

调查对象是热点中一加11约200 Mbps、另外两台客户端约300 Mbps的差异。
本次没有改生产代码，没有重启热点或重刷驱动，没有修改手机设置、NV或IMEI。

- 热点：畅行60，`changxing60-r20261029.183025-g55b71b07-sd49785bc`，
  内核 `6.18.54-changxing60-bringup4`。
- 原厂资料：`ZTE/CN_P720S20/P720S20:13/TP1A.220624.014/20240919.212928:user/release-keys`。
- 手机：一加11 PHB110，`PHB110_14.0.0.813(CN01)`；使用USB ADB读取状态。
- 热点运行在149信道、VHT80；SDIO实际为208 MHz、4位SDR104。
- WCN：`MARLIN3_20A_RLS2_W24.29.5~sc2355_marlin3_lite~07-19-2024 09:58:12`。
  固件SHA-256：`ea906d728ba4aa0806fd164a298bc14cb02f011f497f108d27ef7b4df99825b9`，
  与本机原厂转储相同。
- 运行Wi-Fi模块build-id：`8abc1d3e265a8e4b9e33fddc240d3f899f49d72b`，
  已核对与本次构建文件相同，包含新的公共BA实现。

## 已观测结果

| 测试 | TCP算法 | 有效吞吐 | RTT | 采样期间新增TCP重传 |
| --- | --- | --- | --- | --- |
| 一加11，原iperf3单流，首轮 | Cubic | 192.5 Mbps | 122–146 ms | 0 |
| 一加11，原iperf3单流，复测 | Cubic | 186.5 Mbps，稳态约192 | 稳态128–153 ms | 0 |
| 快客户端，相同iperf3单流 | Cubic | 318.1 Mbps | 20–25 ms | 0 |
| 一加11，原生工具单流接收 | BBR | 191.6 Mbps | 15–23 ms | 0 |
| 一加11，原生工具四流接收 | BBR | 合计171.6 Mbps | 5–77 ms | 各流均为0 |
| 一加11，原iperf3单流，USB同步读取 | BBR | 206.4 Mbps / 48秒 | 11–31 ms | 0 |

原生工具使用手机已有的Toybox netcat，将收到的数据直接丢弃到`/dev/null`，
热点端发送内存中的零数据；没有在手机安装或保存工具/数据。
这类对照不能完全消除测试程序和TCP套接字差异，但表明低速不只出现在PingTools中。
四流对照中热点平均CPU利用率约5.7%，最忙核心约12.3%，未见持续CPU满载。

最后一轮iperf3测试同时确认：

- 手机处于`Awake`，信号为−37至−39 dBm；25次接口读数均报告Rx 433 Mbps。
- 没有持有Wi-Fi high-performance/low-latency锁；这本身不是故障证据。
- TCP发送缓冲受限、接收窗口受限累计时间在采样内均未增加。
- 采样开始前已有一次TCP重传，采样期间没有增加；不能表述为整条连接从未重传。

Android接口数据存在缓存，不能把433 Mbps读数当成逐帧MCS、带宽或聚合深度的证明。
零TCP重传也不能排除802.11层重试。

## BA与原厂核对

使用独立tracefs实例观察慢速命令和peer状态，不对逐包热路径加探针：

- 三台客户端的驱动peer状态均为HT/VHT启用。
- 一加11慢速时TID 0的BA位持续存在；另一个TID的ADDBA命令返回transport=0、
  status=0、响应长度13，仍未恢复到300 Mbps。
- 这些只能证明软件状态及固件命令接受，不能证明每个空口聚合包的实际窗口和深度。
- BA事件type 2在实际源码中是BAR，不是DELBA；不能把反复BAR当成会话反复拆除。
- 复核本机原厂`sprd_wlan_combo.ko`（SHA-256
  `e59e6b1536f64eb8e685a01cbcb5efbc16f1dfbf9be64c38f286e77939dafc88`），
  其ADDBA同样请求64帧窗口、A-MSDU许可0。不能以“对齐原厂”为名改成A-MSDU许可1。

现有AP驱动`get_station()`没有提供逐客户端信号/速率统计。hostapd返回的空速率、
HT/VHT false和计数0不能用来判断真实能力；本次因此读取手机自身状态作补充。
LUT索引可复用，实际期间发生过重连，不能用LUT编号直接推断第一/第二台接入。

## 临时对照及恢复

早先曾短时将热点发送缓冲上限从4 MiB提高到16 MiB，结束已恢复4 MiB。
用户在该轮期间将全局算法从Cubic切到BBR，因此那轮不是纯粹的缓冲A/B实验；
不能把RTT降低全部归因于缓冲变化。保留用户选择的BBR，没有改回Cubic。

所有自建tracefs探针、实例、临时监听和发送进程均已退出/清理。
未停止用户原有`iperf3 -s`服务。调查文件保存在
`work/cx60-wifi-multi-client-20261030/`，主要数据为：

- `ba-trace-r4.json`、`ba-trace-fast.json`、`ba-trace-slow-r2.json`。
- `sendbuf-trial.json`。
- `phone-native-downlink.json`、`phone-native-parallel.json`。
- `phone-usb-iperf-active.json`、`phone-active-power.json`。

一次息屏下的原生上行探索测试约19.2 Mbps；其方向、接收端、手机唤醒状态均与
上述下行对照不同，没有匹配条件下的快客户端对照，不作为根因证据。

## 当前结论与缺口

尚未确认根因，也没有据此加入固件参数修改。
现有结果不支持“第二台接入必然降到VHT20”或“未加载新版BA修复”的判断；
也没有发现测速App、单条TCP流、持续CPU满载或TCP窗口限制能单独解释该差异。

疑点仍包括SC2355与该客户端的实际空口聚合、调度及省电协作。
要进一步区分，需要可信的逐peer固件统计或空口ADDBA/BlockAck/聚合记录。
当前软件BA成功位和Android缓存速率都不能替代这些证据。
还没有完成同一手机单独/首个接入与后接入的受控对照，也没有原厂Android热点的同条件测速；
因此不能把问题定性成一加11缺陷、固定接入顺序缺陷或某个确定的固件bug。
