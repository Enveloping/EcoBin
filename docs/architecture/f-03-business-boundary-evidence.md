# F-03｜业务模块边界搬迁实施证据

> 验证日期：2026-07-25
> 任务：[F-03 funds、device、recycling、operations 边界搬迁](../planning/tasks/p0-controlled-loop/f-03-business-module-boundary-migration.md)

## 1. 实施结果

- 根 Maven reactor 已收口为 common、framework、identity、device、funds、recycling、
  operations、integration、bootstrap 九个目标模块；旧 `system` 和 `business` 均不再
  作为 Maven 模块存在。
- 旧钱包和提现行为迁入 funds；投递及其审核、清运记录和袋行为迁入 recycling；统计聚合迁入
  operations；设备查询和统计由 device 公开窄端口提供。
- OneNet 入站分发只依赖 recycling 的不可变公开命令和事件端口，不再导入业务内部
  Service、Entity 或 Mapper。
- operations 的跨域统计只组合 identity、device、funds、recycling 的公开查询端口；
  目标业务模块之间的生产代码导入只允许经过 `.api`。
- common 已收紧为五个 JDK-only 类型，不再依赖 Spring、MyBatis、Lombok 或外部 SDK；
  MyBatis 持久化基类迁入 framework。
- bootstrap 的生产源码只保留启动与组装，业务 Controller、Service、Repository 和
  Mapper 均位于各自所有权模块。

## 2. 自动门禁

`ModuleBoundaryTest` 固定以下结构约束：

1. 根 POM 精确声明九个目标模块，旧 system/business POM 和依赖声明不得重新出现；
2. 九个目标模块的内部 Maven 依赖必须精确匹配冻结 DAG；
3. identity、device、funds、recycling、operations 之间只允许导入对方 `.api`；
4. common POM 不得声明依赖，生产源码只能导入 JDK 类型；
5. bootstrap 生产源码不得出现业务 Service、Controller、Repository、Mapper 或领域包。

现有回归测试同时覆盖登录鉴权、租户隔离、投递两阶段、清运、钱包提现、统计和 OneNet
事件分发，证明本次搬迁保持旧行为，而不是顺带实现目标新业务。

## 3. 复验证据

使用 Java 21.0.10 执行：

```powershell
mvn.cmd test
mvn.cmd install -DskipTests
git diff --check
```

结果：

- reactor：根项目加 9 个目标模块，共 10 个 reactor project，全部成功；
- Surefire：23 份报告、82 项测试，`failures=0`、`errors=0`、`skipped=0`；
- 制品安装：10 个 reactor project 全部成功；
- 边界门禁：九模块清单、冻结 Maven DAG、跨模块 `.api`、common 纯 Java、bootstrap
  只组装全部通过。

## 4. 范围边界

F-03 只完成旧行为等价的物理搬迁和依赖收口，不等于目标 V-01～V-11 已实现。目标数据库
仍未接管旧应用，新的投递/清运状态机、资金双账本、可靠任务、客户端和设备闭环继续由
F-06～F-12 与纵向业务任务实施。
