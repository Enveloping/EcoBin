# HIL 工厂母镜像 v6 预检证据

本目录记录 `hil-factory-login-20260828-06` 单卡本地热修复候选镜像的预检结果。
镜像二进制包含批次共享的受保护输入，只保存在 Git 忽略的
`hardware/image-artifacts/local/factory-secret/builds/` 下，不纳入版本库。

本次候选镜像已固化真机验证过的 DNS、蜂窝防火墙、首次注册、工厂交接、
运行时门禁和远程支持权限修复。原始 v5 镜像保持不变，v6 的完整 SHA-256
见同目录校验文件。

这份证据不是正式生产发布证明。完成资格闭环仍需：

1. 在 OneNet 目标产品中导入并保存
   `contracts/onenet/generated/onenet-thing-model.candidate.json`；
2. 将 v6 镜像烧录到已确认的单张 TF 卡并完成全量回读比对；
3. 从全新状态启动，重跑蜂窝联网、一次性注册、运行时与模拟 MCU 成功验收。

预检事实和未完成闸门见
`hil-factory-login-network-enrollment-runtime-preflight-evidence.json`。
