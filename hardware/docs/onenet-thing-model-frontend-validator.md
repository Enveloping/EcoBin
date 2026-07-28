# OneNet 物模型前端校验逻辑快照

关联文件：
[`onenet(物模型格式判断).js`](onenet(物模型格式判断).js)

## 来源与用途

- 来源：项目负责人从 OneNet 控制台前端页面取得的 Webpack JavaScript chunk。
- 入库日期：2026-07-28。
- 大小：2,360,852 bytes。
- SHA-256：`6D669E2E403CC6A2544C4F6329EA4B87741EB30C75D3FA60355B4AF0B475DCB9`。
- 用途：离线查阅 OneNet 控制台实际使用的物模型格式校验条件，定位控制台只返回单个
  导入错误而未说明全部原因的问题。

## 使用边界

- 这是 OneNet 控制台前端实现的原始快照，不是 EcoBin 自行定义的业务契约，也不是
  稳定的 OneNet 公共 API。
- 文件保持抓取时的原始字节，不直接修改、格式化或重新打包；需要补充结论时写入本说明
  或 EcoBin 契约生成/校验代码。
- OneNet 控制台前端可能更新。若当前控制台行为与快照不一致，以当前非生产控制台的
  实际导入结果为准，并重新抓取、记录日期和 SHA-256。
- EcoBin 的权威机器来源仍是 `contracts/onenet/`；此快照用于解释和补强平台兼容校验，
  不能反向削弱已经冻结的业务字段与语义。
- 入库前已检查常见私钥、JWT、AWS/Tencent SecretId 特征，未发现对应凭证模式；后续
  替换快照时必须重新执行秘密扫描。

## 2026-07-28 首轮对照结果（修复前）

用本快照对照当前
`contracts/onenet/generated/onenet-thing-model.candidate.json` 后，确认下列问题会绕过
EcoBin 现有契约校验，但可能被 OneNet 控制台前端拒绝：

1. 前端把导入文件大小限制为小于 `262144` bytes。修复前候选文件的 Git LF 内容为
   `255444` bytes，但 Windows 工作区的 CRLF 文件为 `264820` bytes；直接选择工作区
   文件导入会先触发文件过大。生成器和交付流程需要确保导入件使用 LF。
2. 前端要求枚举项说明为 1–20 个字符，并且只包含中英文、数字、下划线或连字符。
   修复前候选文件共有 46 处、9 种英文枚举说明超过 20 个字符：

   - `INSUFFICIENT_VALID_SAMPLES_CLEAR_FALLBACK`
   - `COMMAND_SUPERSEDED_BEFORE_DISPATCH`
   - `COALESCED_WITH_EXISTING_CLOSE`
   - `SAFETY_SENSOR_STATE_CHANGED`
   - `FULLNESS_SENSOR_DIAGNOSTIC`
   - `DELIV_DOOR_HIL_NOT_QUAL`
   - `DELIV_DOOR_OUT_REJECTED`
   - `AVAILABLE_SAMPLES_MEAN`
   - `NO_ECHO_CLEAR_FALLBACK`

3. 当前候选文件的功能点总数、标识符、名称以及事件/服务参数数量未触发快照中的上限。
4. 修复前 `contracts/tools/validate_contracts.py` 没有校验枚举说明格式和最终导入文件字节数，
   因此本地校验全绿不能代替 OneNet 前端兼容性校验。

以上是基于本快照得出的修复前静态结论。

## 与桌面旧导入件的修复前体积对照

项目负责人提供的
`C:\Users\24217\Desktop\model-tB6NlBWW0V.json` 为 OneNet 导出的较早版本：

- 文件为 LF 换行，大小 `228409` bytes，包含 12 个事件和 9 个服务。
- 其核心功能集合对应仓库历史提交 `436f6b4` 附近的首版候选模型；OneNet 导出又补充了
  `profile`、`combs` 和 `functionMode` 等平台元数据。它不包含当前新增的
  `safetySensorStateChanged` 事件及后来扩展的若干事件参数。
- 旧文件没有超过 20 字符的枚举显示说明；修复前候选文件有 46 处、9 种超长说明。

因此当前 Windows 工作区候选文件比桌面旧文件大 `36411` bytes，不是单一原因：

1. 当前 Git LF 内容为 `255444` bytes，因新增一个事件并扩展
   `deviceRuntimeSnapshot`、`deliveryComplete`、`cleanComplete`、
   `fullnessSampleComplete` 等参数，实际模型内容比旧文件大 `27035` bytes。
2. Git 系统配置 `core.autocrlf=true` 把候选文件检出为 CRLF，9376 个换行各增加一个
   `CR` 字节，再增加 `9376` bytes，得到工作区的 `264820` bytes。
3. 当前生成器把部分较长的规范枚举值直接作为 OneNet 前端显示说明；旧导入件使用较短
   的显示说明，所以既增加体积，也触发前端 20 字符限制。

不能直接回退到桌面旧文件，否则会丢失当前事件和参数。

## 2026-07-28 修复结果

- 保留现行 13 个事件、9 个服务和全部业务参数，不回退协议版本。
- 仅对控制台导入候选使用一层 JSON 缩进，并通过 `.gitattributes` 强制 LF；生成文件
  为 `189175` bytes，距离严格 `<262144` bytes 上限剩余 `72969` bytes。
- 为上述 9 种规范枚举值补充 1～20 字符的 `enumDisplay` 短名称。这个映射只影响
  OneNet 控制台和线级整数枚举的显示说明；香橙派还原后的规范枚举符号不变。
- `validate_contracts.py` 和契约单元测试现在检查最终文件字节数、LF 换行、枚举说明
  长度及允许字符；以后生成内容再次超限会直接使本地验证失败。

自动验证不能证明 OneNet 控制台版本没有变化，仍应在非生产 OneNet 产品执行一次真实
导入；但文件大小和已知枚举说明限制现在已成为仓库内可重复执行的门禁。
