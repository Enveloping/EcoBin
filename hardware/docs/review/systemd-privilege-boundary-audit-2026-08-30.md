# 首启服务权限边界审计（2026-08-30）

## 结论

v10 在 P7 本地硬件验收通过后的现场等待中，暴露出一个会阻断冷时钟主动收敛分支的确定性
权限缺口：`ecobin-cellular-uplink.service` 的真实 systemd 权限上下文无法访问 Chrony
控制套接字：

- `/run/chrony` 为 `_chrony:_chrony 0700`；
- 服务虽然声明 `User=root`，但能力边界只保留 `CAP_NET_ADMIN`；
- 在与服务相同的能力边界内执行 `chronyc online` 返回 `501 Not authorised`；
- 不裁剪能力的交互式 root 执行同一命令返回 `200 OK`。

因此，“用 `sudo` 手工执行成功”不能证明常驻服务可执行同一操作。root 用户身份、
systemd 文件系统命名空间、Linux capabilities（进程能力）、设备白名单和地址族限制必须
作为一个整体复现。

修复只给长期运行的蜂窝协调器增加 `CAP_DAC_OVERRIDE`，并保留原有
`CAP_NET_ADMIN`、`NoNewPrivileges=yes`、`ProtectSystem=strict`、地址族限制和显式可写
路径。该能力允许进程绕过普通文件访问权限，因此不会扩散给其他首启或工厂服务；自动测试
精确断言只有该单元拥有它。这里采用项目负责人已确认的长期服务方案，不增加临时 `_chrony`
辅助单元。

## 权限核对矩阵

| 被调用边界 | 谁在什么情况下调用 | 所需系统事实 | 审计结果 |
| --- | --- | --- | --- |
| Chrony 控制套接字 | 蜂窝协调器在 P7 已通过、RNDIS 的 DNS 可用但系统时间不可信时调用 `chronyc` | `AF_UNIX`；可穿越 `_chrony 0700` 目录；Chrony 服务已启动 | v10 缺少目录访问能力，已增加仅限该服务的 `CAP_DAC_OVERRIDE` |
| nftables | 早期全机锁、首启协调器和蜂窝协调器切换严格出站规则时调用 `nft` | `CAP_NET_ADMIN`、`AF_NETLINK`、跨进程锁可写 | 三个调用单元均已有对应能力/地址族；失败会恢复并验证紧急全拒绝规则 |
| NetworkManager | 首启/蜂窝协调器安装并激活固定 RNDIS profile | root 身份、system D-Bus 的 `AF_UNIX`、profile 路径可写 | 单元声明与代码调用一致，未发现第二处权限错配 |
| systemd 管理器 | 首启协调器按事实启动或补启动目标成员 | root 身份、system D-Bus 的 `AF_UNIX` | 单元允许 `AF_UNIX`；现有真机流程曾证明成员可被补启动 |
| UART/GPIO/摄像头 | P7 执行器和 UART 交接读取串口、切换 BOOT0/NRST、拍照 | root 身份、`DevicePolicy=closed` 下的逐项 `DeviceAllow`、锁目录可写 | UART5、gpiomem、mem 和仅 P7 所需的 video4linux 均逐项声明；未发现能力缺口 |
| P7 本地命令套接字 | 低权限网页向 root 验收执行器发送窄命令 | 双方允许 `AF_UNIX`；运行目录和套接字组权限匹配 | `root:ecobin-factory-web` 与 `0660` 契约一致，网页没有获得硬件或密钥权限 |
| 蜂窝结果投影 | 蜂窝协调器每轮写入本次启动的结果，首启协调器和网页只读 | root 私有运行目录、`0600` 原子 JSON、固定字段和有界大小 | 新增 `/run/ecobin/cellular-uplink/status.json` 与专用 `RuntimeDirectory`；写失败不改变网络门禁 |

本轮静态审计没有发现第二个与 Chrony 同类、已能触发的权限错配。v10 热修后的真实 systemd
上下文已证明实际进程能力为 `0x1002`，完整服务沙箱执行 `chronyc online` 返回 `200 OK`；
状态投影、网页诊断和重协调后的 OneNet MQTT 也已通过。重新取得串口时设备在热修前已经完成
注册，因此这些事实不能替代 v11 从空白冷启动验证自主注册。现场记录见
[v10 Chrony 权限与可观测性热修证据](../../image-artifacts/evidence/hil-v10-chrony-permission-hotfix-20260830-01/README.md)。

## 结果可见性与放行边界

蜂窝协调器现在把每轮稳定结果码原子写入
`/run/ecobin/cellular-uplink/status.json`，但只在结果或投影写入状态发生变化时输出一条
journal 日志：

```text
ecobin-cellular-uplink result=<稳定结果码> statusProjection=<OK|CELLULAR_STATUS_WRITE_FAILED>
```

该文件是可丢失的本次启动诊断投影，不是授权事实：

- 每轮网络、P7、封存、可信时间和注册门禁仍从原始事实重新计算；
- 文件缺失、损坏、权限错误或未知结果码时，网页退回现场探测结果；
- 只有现场探测已经是 `CELLULAR_HTTPS_UNAVAILABLE` 时，允许已知的校时结果码把这个
  泛化症状解释得更精确；
- 旧的校时结果不能遮住 RNDIS 模块缺失、DHCP、DNS 或配置错误；
- 状态写入失败不会打开网络、不会放行注册，也不会让协调器退出。

局域网页显示“网络时间可信”和“当前接入卡点”，同时保留稳定英文结果码。当前校时结果码
包括 `TIME_SYNC_PENDING`、`TIME_TRUST_QUERY_FAILED`、`CHRONY_ONLINE_FAILED`、
`CHRONY_ACTIVITY_FAILED`、`CHRONY_SOURCES_UNAVAILABLE`、`CHRONY_REFRESH_FAILED`、
`CHRONY_BURST_FAILED`、`CHRONY_WAITSYNC_FAILED` 和 `TIME_SYNC_INTERNAL_ERROR`。

## 后续镜像的验证规则

涉及外部守护进程、设备节点或管理总线的改动，必须同时完成以下检查：

1. 先确认目标路径、套接字或设备节点的所有者、组和权限；
2. 读取安装后的 unit 与 drop-in，而不是只检查仓库模板；
3. 用 `systemctl show` 核对 `User`、`Group`、`CapabilityBoundingSet`、
   `AmbientCapabilities`、可写路径、设备策略和地址族；
4. 核对实际进程的 `CapEff`，并在真实服务中执行目标操作；
5. 以 journal 的稳定结果码确认成功或失败转移，不能以交互式 `sudo` 代替；
6. 对安全相关失败确认网络或业务仍保持关闭，并验证下一协调周期可以恢复；
7. 构建镜像后再次审计镜像内安装结果，冷启动后再重复真实服务上下文验证。
