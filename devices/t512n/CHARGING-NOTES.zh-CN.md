# T512N 首轮充电功率核对

2026-10-18，原厂固件512NCA05，原始证据位置及哈希见本目录README。

## 确认的配置

- 原厂 simple-battery 中 `charge-dcp-current-microamp=<1000000 1000000>`，
  CDP/unknown也是1A；SDP为500mA。DCP JEITA正常温区也为1A。
  因此首次直接复用原厂表时，普通DCP路径本就有1A的请求值。
- 原厂 `pd@e00` 为okay，compatible为`sprd,sc27xx-pd`/`sprd,ump9620-pd`，
  `op-sink-microwatt=10000000`，使用本机的校准nvmem、extcon和VBUS供电节点。
- 原始 `sink-pdos=<0x0401912c 0xc0dc213c>`，按Linux `include/linux/usb/pd.h`
  的协议字段解析，分别声明固定5V/3A和PPS 3.3–11V/3A。
  这是固件声明的协商能力及10W工作目标，不能当作已经测得15W或33W。
- 首轮OpenWrt内核没有SC27xx PD驱动，充电检测沿用已有移植的BC1.2路径。
  接电脑时确认为SDP，输入及充电请求500mA，REG00=04、REG02=88。
- 用户反馈充电器约5W，OTG正常。尚未记录该次充电器PDO、线材、起始SOC及温度，
  尚不能把它全部归因于DCP表，也不能据此证明当前硬件可持续10W以上。

## 后续处理

1. 对照本机原厂SC27xx PD模块和厂商充电程序，核对协商成功后电压、电流请求及JEITA联动。
2. 与当前Type-C/BC1.2/OTG职责整合，避免两个角色控制器同时驱动CC、VBUS或破坏USB管理连接。
3. 用同一充电器、线材和电量/温度条件比较原厂与OpenWrt。
   按实际协商及硬件限制再调整该机型的充电策略。
4. 回归电脑SDP、普通充电器、C-C正反插、快速插拔、OTG转充电以及满电终止/温控。

当前没有强制提高输入电流、恒压上限或温度阈值；10W以上尚未实现或实测。
该项保留为独立的板级适配任务，不修改E5/T551M/M8的充电策略。
