# 后端与数据库 V84 生产部署记录（2026-09-16）

## 结论

后端、Web 和数据库 V84 已部署到生产。当前应用发布为
`20260916025440-863bcc8cca74`，源码提交为
`863bcc8cca74064af8e4f75ce22bebc4ce96d4cc`；数据库为 138 张领域表、84 条成功
Flyway 前向迁移。后端和 Web 容器均为 `running/healthy`，重启次数为 0。

V84 只替换 `dev_config_version.delivery_door_travel_wait_ms` 的检查约束，并把新配置默认值与
合法下限改为 3000 毫秒；不更新既有配置行。新后端还包含 v44/v45 厂家验收结果可靠返回和
重量格式修复。

## 构建与制品

正式构建在干净工作区和 Java 21 上执行。Maven 全量构建成功；`ecobin-bootstrap` 汇总为
299 项、0 失败、181 项环境跳过，其他已执行模块同样为 0 失败。Web 的 `npm ci` 与生产构建
成功。发布归档 SHA-256 为
`c266c23b20b8ba6b8cb952671fd1d8da507ce6508d0e174edf2c063fc59f15f4`。

服务器安装器校验归档后生成：

- 后端镜像：`sha256:b1c4610787f369079004afd2ed1659d2e50f5efbeaa278e9fc48d6eec6ee1a82`；
- Web 镜像：`sha256:1fd6824a1943c2ea77f49644199dc0ca6080b43b9e5b3a72e66fd0801227b99d`。

安装器只安装并更新部署参数，没有在数据库迁移前重启应用。

## 停写与 V84 迁移

部署前和停写后均确认：活动投递 0、活动清运 0、活动设备软件部署 0。生产应用停止时数据库
精确为 `83:83`，schema owner 处于锁定状态。

项目负责人在迁移前明确要求“不备份数据”，因此本轮没有生成数据库备份，执行结果明确记录
为 `backup=SKIPPED_BY_OPERATOR`，最终独立检查的 `pre-v84` 备份文件数为 0。这个决定意味着
迁移异常只能保留现场并前向修复，不能依赖本轮备份回退。

迁移仅在停写窗口临时解锁 schema owner，通过受信任 SSH 隧道运行仓库中的 Flyway V84，结束
后立即重新锁定。独立核对结果为：

- 迁移历史 `84:84`；
- 领域表 138 张；
- schema owner 与 trigger definer 两个账号均已锁定；
- 投递门行程等待列默认值为 3000 毫秒，检查约束范围为 3000～45000 毫秒。

没有执行 Flyway `repair`、降版本、删除历史或改写既有配置记录。

## 激活与最终核验

激活前重新暂存运行秘密并通过生产预检。新应用启动后，后端内置健康检查、秘密隔离探针和
Web 回环入口均通过；持久日志目录仍为 UID/GID `10001:10001`、权限 `0750`。

独立 SSH 会话最终确认：

- 当前发布、Git 提交和两张运行镜像标签相互一致；
- 后端与 Web 容器健康且重启次数均为 0；
- 活动投递、清运和设备软件部署仍全部为 0；
- 最近 15 分钟 `evidenceSchemaVersion` 与 `weightMeasurementUid` 两类旧格式隔离新增数为 0；
- `/etc/ecobin/runtime.env` SHA-256 仍为
  `203c65bb009ebcae265003b6fc7294bd15187c9c02a8fbb5cf254ce7ed29d489`；
- `hardware-runtime-20260916-45` 仍在设备软件允许列表中；
- `businessReleaseRemoteDispatchEnabled=false`，没有开启远程业务程序下发。

本轮只完成云端后端、Web 与数据库部署，没有替代 v45 装卡冷启动、真实 HMI、UART、RS485、
机构、摄像头、COS、OneNet 或投递/清运 HIL。
