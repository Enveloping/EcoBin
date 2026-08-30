# HIL 工厂母镜像 v9 完整重建离线预检证据

本目录记录 `hil-factory-login-20260830-09` 单卡开发/HIL 镜像的完整重建和离线预检
结果。镜像二进制包含批次共用 K1、设置热点密钥和开发登录密码，只保存在 Git 忽略的
`hardware/image-artifacts/local/factory-secret/builds/hil-factory-login-20260830-09-full-rebuild/`
下，不纳入版本库。

v9 不再沿用 v7/v8 的旧软件负载。它从锁定的 Orange Pi Debian 1.0.4 原始镜像、当前
包锁和干净提交 `e3455c848ecd9b6471b16143351219e439d3997e` 完整重建，安装版本化
运行时、首次启动、注册、工厂验收和远程维护组件。无密钥候选根文件系统的确定性重建
资格为通过；随后只从已验收的 v8 逐字节继承 K1、设置热点密钥和 `orangepi` 密码散列，
没有在日志或公开证据中记录这些值或单独摘要。root 密码登录保持锁定，开发阶段的
`orangepi` 默认密码登录保持启用。

本版包含 v8 之后已经提交的运行目标成员自动恢复等设备侧修复，并把当前两路摄像头
固化为 HSK/UNIQUESKY 外摄和 icSpring 内摄。离线只读验收确认：原始分区布局、ext4
文件系统、当前软件负载、systemd 单元、首次启动空白状态、摄像头配置、开发登录策略
和受保护输入继承均符合预期；设备凭据、注册状态、远程维护状态、业务数据库、SSH 主机
密钥、machine-id、NetworkManager 和 chrony 运行状态均为空。

待写卡镜像大小为 `2571108352` 字节，SHA-256 为
`d5b9bac39d2d1e130656efeaf49ffbb0678719241b06fb00d36ca2301dbac52f`。对应无密钥候选
SHA-256 为 `d9972cbe34f36e175173cbcbef72b8a13fbf07324a8a5d9104efd49015958e78`。
Windows/Python 3.11 设备侧回归为 `994 passed, 46 skipped, 5 subtests passed`；Windows
镜像工具回归为 `53 passed, 8 skipped, 24 subtests passed`；Linux/root 镜像工具回归为
`60 tests, 1 skipped`。构建中发现的运行时完成标记与虚拟环境权限审计顺序问题已由失败
测试复现，并在提交 `e3455c84` 修复后以原始失败负载重新验证通过。

这是单卡开发/HIL 母镜像，不是正式量产发布：它有意携带共享秘密和默认开发密码，且
没有执行两候选独立构建一致性证明、正式发布签名和完整真实硬件资格。下一步须由项目
负责人再次确认目标磁盘和介质序列号后写卡并完整回读，再从空白状态重新执行局域网页
验收、自动注册、机器验收/封存、租户及机构分配和业务准入流程。本次构建没有写入 TF 卡。

结构化边界和验证事实见
`hil-factory-login-full-rebuild-preflight-evidence.json`。
