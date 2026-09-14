# EcoBin 香橙派量产写卡与真机验收清单

> **rc.26限制**：本清单的现行P7项只接受`uart-v2`原生身份、配置、设备事实、一次START和
> 结果保管确认。F3/F1、AA/DD、EE/EF及`factory_sim`只属历史兼容测试，不能用于P7放行；
> 真实HMI、RS485与机构HIL仍是未完成门槛。详见
> [rc.26收口记录](../../hardware/docs/review/uart2-rc26-capability-hmi-factory-projection-terminal-closure-2026-09-14.md)。

> 本清单必须与受控发布目录、逐台验收报告和后端当前验收代次一起归档。自动测试不能替代真机项目。

## 写卡工位

- [ ] 从工位外可信介质安装 root 所有、发布目录不可修改的 `trusted-flash-entry`、外部 `LOCKED` 策略和当前发布公钥，并核对 `imageSigningKeyId`。
- [ ] target-media 资格证据与本次批准模式一致：默认模式为两张以上同批次 32 GB TF 卡；项目负责人明确接受单卡时，证据必须标记 `SINGLE_CARD_PROJECT_OWNER_ACCEPTED` 且只覆盖该卡。证据摘要和最小值与锁定布局一致；外部策略中的两个构建者身份/域/公钥、rootfs 资格证据摘要和三类发布角色公钥均经同行复核；两个 build receipt 的 invocation UID 不同，聚合 build attestation 和 seal evidence 验签通过。
- [ ] `/proc/swaps` 只有表头，没有 swapfile、交换分区或 zram；没有在活动交换空间存在时处理含 K1 的封存镜像。
- [ ] 可信入口先验证 `release-checksums.sig` 和精确 19 项发布清单，再把镜像、manifest、SBOM、包清单、证明、target-media/rootfs 资格证据、schema 和写卡程序完整快照到 `root:root 0700` 目录；没有直接运行发布目录中的脚本。
- [ ] 目标是明确选择且重复确认的可返工 32 GB TF 卡，不是系统盘；真实字节数不小于已签名 manifest 的 `minimumQualifiedMediaBytes`。
- [ ] 写入后已完整复读原始镜像有效范围，SHA-256 与 manifest 一致。

## 首次启动与离线验收

- [ ] 根分区和 ext4 文件系统扩容完成；内核分区扇区数真实增大、文件系统覆盖到分区尾部，重启后仍正常。
- [ ] 当前启动已重新建立安全 GPIO、EdgeStore 结构和首启门禁，不是只复用旧 `state.json` 阶段。
- [ ] 未插 SIM 时手机可连接验收热点；客户端不能访问设备 SSH、蜂窝或以太网 WAN。
- [ ] UART5`/dev/ttyS5`以`uart-v2 / 115200 / 单投口`实测正确；验收开始时已如实选择本机是否安装MCU远程升级线。
- [ ] `QUERY_DEVICE_IDENTITY`取得当前启动编号、固件身份、最高命令序号和真实能力位；能力包含`0x8100`，没有未知位、身份冲突或旧启动回复。
- [ ] 原生配置已应用；`QUERY_DEVICE_FACTS`核对同一当前启动、配置版本/摘要、空闲作业、投递门最近有效控制为关闭、清运锁断电及真实称重事实。满溢距离/阈值、烟感和缺测状态均照实展示。
- [ ] 当前ECOBIN_UART远程更新能力为不支持；没有另行批准且实装的BOOT0/NRST链路时，报告为`NOT_APPLICABLE`且能力文件为`false`，未发送历史F2帧。
- [ ] DECXIN 外部摄像头和 icspring 内部摄像头路径稳定、画面角色人工确认正确。
- [ ] 热点页填写并归档本轮参考重量；空载、加载、取下的全部样本、稳定值、差值、允许范围和判定可见。自定义参考值只代表本轮流程标准，不被误写为称重模块已经校准。
- [ ] 离线投递只写出一次`START_DELIVERY_SESSION`；MCU自主完成首重、屏幕按钮、开关门和末重；同一`WORK_RESULT`已可靠保存并完成`RESULT_SAVED`回复，随后操作员重新确认投递机构及周围区域安全。
- [ ] 离线清运只写出一次`START_CLEAN_OPERATION`；MCU自主完成屏幕按钮、锁控制和前后称重；同一`WORK_RESULT`已可靠保存并完成`RESULT_SAVED`回复，随后操作员确认清运门完全关闭。
- [ ] 页面、初始化、实时重量和二维码完整指令组进入UART3软件发送队列即按设计视为显示；队列不足是零字节发布并重试。真机逐页观察正确；没有用USART完成、屏幕回执或读回来替代该信任边界。
- [ ] 任一原生START不确定窗口均已完成安全恢复，没有`RECOVERY_REQUIRED`锁；重启后只查询原命令和原结果，没有构造第二条START，也没有据按钮、过程重量或动作片段重建成功。
- [ ] 投递和清运测试未在生产EdgeStore、正式照片目录或网络出口产生业务事实。

## 联网、注册与封存

- [ ] Air780E RNDIS、SIM/APN、DHCP、DNS、可信校时和后端 HTTPS 探针通过。
- [ ] 注册正式凭证已可靠落盘，K1 和一次性注册实现已从文件系统逻辑删除；报废卡按秘密介质处理，没有把闪存“删除文件”等同于物理擦除。
- [ ] OneNet、COS、远程维护本地代理和普通硬件运行时健康。
- [ ] 厂家初始袋真实扫码完成，后端当前机器验收代次为 `PASSED`。
- [ ] 当前代次 `AUTHORIZE_FACTORY_SEAL` 已处理，操作员本地确认后形成 `SEALED`；本地授权终态和唯一 `FACTORY_SEAL_COMPLETED` 事件已在同一事务提交。
- [ ] 后端已可信应用同一授权和验收代次的 `FACTORY_SEAL_COMPLETED`；在此之前资产没有被分配给租户或机构。
- [ ] 热点密码、临时照片和活动状态已清理；冷启动不再开放验收热点。
- [ ] `sealed.json`、本地报告摘要、当前镜像发布号和后端验收代次相互一致。

验收设备编号：________________  镜像发布号：________________

操作员：________________  复核人：________________  日期：________________
