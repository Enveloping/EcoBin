# v10 Chrony 权限与可观测性热修证据（2026-08-30）

## 结论

提交 `58c885dcc5cec2f4e16dcef0de1fadf969807d63` 已原子部署到指定单卡当前 v10
版本化安装目录。真机证明 `ecobin-cellular-uplink.service` 的安装声明和实际进程都只保留
`CAP_DAC_OVERRIDE + CAP_NET_ADMIN`；复制完整服务沙箱的临时 systemd 单元以相同
`CapEff=0x1002` 执行 `chronyc online` 返回 `200 OK`，原来的 `501 Not authorised`
权限缺口已经关闭。

蜂窝协调器同时成功写入 root 私有的本次启动状态投影，journal 只记录一次结果转移；局域网
网页实际返回“网络时间可信”和“当前接入卡点”字段及精确校时结果映射。可信时间、OneNet
MQTT、正式硬件和远程维护服务在受控重协调后全部稳定。

本次热修不是 v11 空白冷启动证据。重新取得串口时，当前 v10 已经在热修前达到
`ENROLLMENT_COMPLETE`，正式凭据、UART 交接事实、正式运行和 MQTT 均已存在；因此不能把
本次已有注册写成热修造成。此次真机证据证明的是权限修复和诊断链本身，v11 仍须从空白状态
验证“P7 后无需人工系统命令即可校时、注册并接入 OneNet”。

## 热修前事实

- 镜像身份为 `single-card-hil-20260830-10`，源码提交为 `6c278433...`；
- P7 为 `PASSED`，首启阶段为 `ENROLLMENT_COMPLETE`，可信时间为 `true`；设备未封存，
  因此出厂热点和网页继续存在符合当前 P8 状态；
- 旧 unit 的能力边界和实际进程均只有 `CAP_NET_ADMIN`，`CapEff=0x1000`；
- 受限上下文执行 `chronyc online` 返回 `501 Not authorised`，具有完整能力的 root 执行
  同一命令返回 `200 OK`；
- `/run/chrony` 为 `_chrony:_chrony 0700`。这证明旧镜像在需要主动把时间源切为在线的
  冷启动分支中存在确定性权限缺口，即使本次设备后来已经取得可信时间。

## 部署内容

目标机先在 `/tmp/ecobin-chrony-hotfix-58c885dc` 校验每个候选文件摘要，再使用目标
Python 3.11 编译四个 Python 文件，并由目标机 `systemd-analyze verify` 校验候选 unit。
旧文件暂存于 `/run/ecobin/codex-chrony-hotfix-58c885dc`；安装脚本带失败自动回滚，并对
每个目标使用同目录临时文件和原子 `rename`。

| 安装内容 | SHA-256 |
| --- | --- |
| `first_boot/cellular_service.py` | `c7ce3db6c3acbf4f15733592b930f40365370c5033a4a9f11186f2a3ab3988e5` |
| `first_boot/cellular_status.py` | `2222cf0c896c72ea494cd407daff56efbd69427a29bf1e144928b5fc1051cdfc` |
| `first_boot/facts.py` | `44ede44cd4f8e6cacbffda76365c97f05f167e0f3ac342582c21db7d27fea65a` |
| `first_boot/time_sync.py` | `280333d4c2c94d0286de2fea4f247a05ae1b8c8a54b898a59d7ccdb6a22ca6d5` |
| `factory/web/app.js` | `bb77cbe9f6400c03bf79a76b114a9d7ec67bfff42173e1856817a325089d4277` |
| `factory/web/index.html` | `3d59d111f1c2ca453c9615cc26b522e42865761bf521f0206ba60d4146a2ac5a` |
| `ecobin-cellular-uplink.service` | `273fa5de1cb2071e333ef9c006cfc389d88a75ff4d173b1bac07acf89052faae` |

## 真机结果

- 安装后 systemd 声明为
  `CapabilityBoundingSet=cap_dac_override cap_net_admin` 和相同的 ambient capabilities；
- 实际长期进程的 `CapInh/CapPrm/CapEff/CapBnd/CapAmb` 均为 `0x1002`；
- 复制 unit 用户、能力、文件系统保护、可写路径和地址族的临时单元执行
  `chronyc online` 返回 `200 OK`，执行后由 `--collect` 自动删除；
- `/run/ecobin/cellular-uplink` 为 `root:root 0700`，`status.json` 为 `root:root 0600`，
  内容严格为 `schemaVersion=1 / resultCode=NONE`；
- journal 记录一次
  `ecobin-cellular-uplink result=NONE statusProjection=OK`，没有周期性重复刷屏；
- 门户 HTTP 实际返回新增的 `time-trusted`、`last-error` 元素和精确 Chrony 中文映射；
- `timedatectl NTPSynchronized=yes`，首启公开状态保持
  `PASSED / ENROLLMENT_COMPLETE / lastErrorCode=NONE`；
- 10:02:23、10:02:39、10:02:56 UTC 三次稳定采样中，首次启动、蜂窝、门户、硬件和
  远程维护进程 PID 均不变，五项 `NRestarts=0`，MQTT 建立连接数始终为 1。

为了让常驻门户重新加载静态资源，本轮显式重启了门户。编排器在门户短暂不可用时按设计
失败关闭，于 10:00:47 UTC 正常停止蜂窝、硬件和远程维护服务；门户于 10:00:51 恢复，
蜂窝于 10:01:06 恢复，MQTT、UART 自检和正式硬件于 10:01:16 前全部自动恢复。journal
只有正常 SIGTERM 和正常 MQTT 断开，没有崩溃；恢复后的服务 `NRestarts=0`。运行中单独
更新网页资产时必须预期这次短暂重协调；完整镜像冷启动不会额外执行该人工门户重启。

## 清理与边界

验证完成后，先分别解析并精确比对 `/run/ecobin/codex-chrony-hotfix-58c885dc` 和
`/tmp/ecobin-chrony-hotfix-58c885dc` 的规范绝对路径，再删除两处临时目录；临时 systemd
证明单元也已不存在。这些易失副本不能从原路径恢复，旧版本仍可从 v10 镜像和 Git 历史重建。

本证据不包含 K1、设备密钥、热点密码、数据库口令或服务器凭据，不代表 v11 镜像已经构建，
也不替代 v11 写卡后的空白冷启动全流程验收。结构化脱敏结果见
[verification.json](verification.json)。
