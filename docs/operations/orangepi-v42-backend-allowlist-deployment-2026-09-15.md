# 香橙派 v42 后台认可版本部署记录（2026-09-15）

## 结论

项目负责人在v42镜像完成指定TF卡写入和完整回读后，明确要求把该版本加入后台认可列表。生产
服务器`VM-0-16-ubuntu`的`/etc/ecobin/runtime.env`已只向
`ECOBIN_DEVICE_ACCEPTANCE_SUPPORTED_EDGE_SOFTWARE_VERSIONS`原精确值末尾追加
`hardware-runtime-20260915-42`。v40、v41及全部更早的现行认可值原样保留，没有修改机器验收
结果、设备业务数据、应用发布或容器镜像，也没有开启远程业务程序下发。

服务器文件和运行中后端容器均已读取新值。生产预检、运行秘密隔离探针、后端内置健康检查、
Web回环入口、后端/Web双容器健康检查以及独立SSH复核全部通过。

## 变更前检查与脚本审查

Windows OpenSSH继续使用已固定主机指纹和`ubuntu`用户的既有RSA身份；连接后的主机名精确为
`VM-0-16-ubuntu`，远端通过免交互sudo执行内存传入的脚本。第一层只读预检确认：

- 当前应用发布仍为`20260914064458-82e72e0dca16`；
- 认可值共18个且唯一，v40和v41存在，v42不存在；
- `runtime.env`为`root:root 0600`的普通单硬链接文件，认可键只出现一次；
- systemd目标、后端/Web容器、秘密隔离、后端健康和Web回环入口正常；
- `businessReleaseRemoteDispatchEnabled=false`。

第二层预检又在与正式部署相同的独占锁、精确旧值和完整健康检查下通过。正式执行前的独立只读
复审直接检查了生成后的远端脚本：旧列表与预检值逐字节相等，新列表只在尾部增加v42；主机、
发布、文件元数据、锁、并发摘要保护、受限备份、同目录临时文件、原子替换、失败回滚和激活后
复核均完整，结论为可部署、无阻断项。

本地内存传输工具第一次试运行时，Windows路径交给`wslpath`后反斜杠被互操作参数处理丢失，
因此在生成远端脚本前退出；该次没有建立SSH执行，也没有修改服务器。修复为受约束的Windows
盘符路径直接映射到`/mnt/<drive>/...`后，两层只读预检才实际到达生产并通过。生成脚本只在本机
内存中形成，再以原始字节写入SSH标准输入，不在生产服务器落地脚本文件。

## 原子追加和验证证据

- 修改前`runtime.env` SHA-256：
  `7e47a871958d53de85abe65c7d4dd1bedb5a529663503cf377a4002b1b5a0456`；
- 修改后`runtime.env` SHA-256：
  `1a3967c8b3cf5f7b963e167a84ac1e38fc914c66bd6683da1e48e030517db839`；
- root私有备份：`/etc/ecobin/runtime.env.pre-v42-20260915T083118Z.OlQlyFct`，摘要等于修改前
  文件，元数据为`root:root 0600`、普通文件、单硬链接；
- 后端镜像保持
  `sha256:9b40cec445f90501c8306dc580db2877c6885ba8851af4442aa76e48261740d8`；
- Web镜像保持
  `sha256:d32e68aeca898979714b9560c57b7a87fb44c7873eaf120325f39097d62602da`；
- `ecobin-stage-runtime-secrets.service`与`ecobin-target-app.service`均为active；
- `ecobin-target-backend`与`ecobin-target-web`均为running/healthy；
- 容器内认可列表与服务器文件精确一致，v40=true、v41=true、v42=true；
- `businessReleaseRemoteDispatchEnabled=false`；
- 生产预检、秘密隔离探针、后端健康检查和`127.0.0.1:18080` Web回环检查通过。

正式应用只执行一次。独立核验由新的SSH进程重新读取文件、备份、容器环境、镜像身份和健康
状态，结果为`v42-allowlist-independent-verification=PASS`。本次生成的预检、部署和独立核验脚本
SHA-256分别为`5ffe93dd0bc7c2fe0e05438aca5e9c8094cbb17713617a392a4462790a9746a8`、
`4af0da8240a557a581d21ee75c9429b95973a70e61ee6fa8b40cccbe236a6f05`和
`355ea51d78e3cf995577e0bd2a3980cc7eac66f1b9f759ef35f1f273182a693d`。

## 与TF卡及现场验收的关系

后台认可现在允许服务器接受设备真实上报的`hardware-runtime-20260915-42`，不表示新卡已经
启动，也不证明热点、UART、HMI、RS485、称重、机构、OneNet注册或反向SSH正常。本次没有伪造
厂家验收证据，没有发起投递或清运，没有生成订单、余额或提现。装卡通电后仍须逐项取得真实
现场证据，尤其要确认首次扫描厂家袋之前，设备入口URL已经通过UART rc.26应用并在串口屏显示。
