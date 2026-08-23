# 香橙派量产镜像、写卡与整机验收手册

> 适用硬件：Orange Pi Zero 3 v1.2（H618）+ STM32F103C8T6 +
> `ECOBIN_MAINBOARD_V1.1`。目标系统为 Debian 12、Linux 6.1、Python 3.11，标称
> 32 GB TF 卡。设计依据见
> [量产镜像与首次启动计划](../planning/orangepi-production-image-first-boot-plan.md)。
>
> 当前状态：仓库内构建、封存、首启、离线验收和云端完成闭环的软件入口已实现并完成首轮
> 自动回归；`tools/orangepi-image/image-layout.json` 的目标介质资格和独立的 formal-release
> policy（正式发布策略）仍为 `UNLOCKED`/`UNQUALIFIED`。Air780E、板载 Wi-Fi AP、
> UART/GPIO、双摄、称重和动作流程尚未形成首台真机签认记录。因此当前脚本应拒绝正式构建、
> 封存或发布；不得通过填写猜测值、关闭检查或使用 `--allow-dirty` 生成量产卡。

## 1. 先理解四类制品

同一发布过程会出现四类不同安全级别的文件，不能混放：

| 制品 | 是否含厂家 K1/热点密码 | 用途 | 保存位置 |
|---|---|---|---|
| 官方基础镜像 | 否 | 只读构建输入 | 受控输入库 |
| 软件负载 | 否 | Python 3.11 运行环境、程序、配置和信任公钥的锁定输入 | 内部构建库 |
| 无秘密候选镜像 | 否 | 静态审计和两次独立构建比较 | 临时受控构建区 |
| 封存发布镜像 | 是 | 签名、压缩并写入 TF 卡 | 最小权限内部制品库 |

镜像发布私钥、MCU 固件发布私钥、维护 CA 私钥、厂家 K1、热点密码和设备正式凭证是不同的
秘密，禁止复用。仓库只保存公钥、schema、脚本和示例，不保存任何真实秘密或正式发布实例。

## 2. 人员和工位分离

至少划分以下职责：

- 构建员：只能读取仓库、官方基础镜像和无秘密软件输入，生成候选镜像；
- 发布员：在隔离 Linux 工位读取 K1、热点密码和镜像发布私钥，生成签名发布目录；
- 写卡员：持有发布目录外的受信发布公钥和签名发布目录，不持有任何私钥；由于发布目录中的
  镜像含明文 K1，写卡员及其工位技术上能够提取 K1，必须纳入 K1 授权、审计和保密范围；
- 验收员：用手机网页和工装执行逐台硬件、联网、注册和封存检查；
- 复核人：核对发布号、设备、后端验收代次和冷启动结果后签字放行。

发布私钥必须是 root 所有、仅所有者可读的独立 Ed25519 PKCS#8 文件。受信发布公钥必须从
发布目录之外的可信渠道交给写卡工位；不能把发布目录中自带的公钥当信任根。

## 3. 任何一项不满足就停止

开始正式构建前执行：

```bash
python3 tools/orangepi-image/lib/validate_inputs.py \
  --config-dir tools/orangepi-image \
  --target-media-qualification-evidence \
  /controlled/evidence/target-media-qualification-evidence.json
bash tools/orangepi-image/build-image.sh --validate-only \
  --target-media-qualification-evidence \
  /controlled/evidence/target-media-qualification-evidence.json
```

必须同时看到 source、builder、apt、layout 均为 `LOCKED`。当前 layout 尚未锁定时，命令失败
是预期安全结果。以下行为一律禁止：

- 把 `minimumQualifiedMediaBytes`、分区号、UUID 或镜像大小填成推测值；
- 在脏工作树上制作正式发布，或把 `--allow-dirty` 产物送入签名流程；
- 在 x86_64 主机直接拼装 ARM64 Python 虚拟环境；
- 从滚动 apt/PyPI 地址在线安装未锁定版本；
- 用已启动过的 TF 卡反向制作母镜像；
- 跳过第二次独立构建、签名验证、写后复读或真机验收。

## 4. 首次锁定 32 GB 介质和布局

这一阶段只需在首次选卡或更换 TF 卡型号、基础镜像、内核、分区方式时执行。

1. 准备同一采购批次中容量最小的多张 32 GB 卡，逐张读取实际字节数；记录最小值，不使用
   包装上的“32 GB”替代测量值。
2. 对锁定的官方镜像只读检查分区表、最终根分区、ext4 UUID 和 PARTUUID。
3. 证明固定原始镜像大小不超过
   `minimumQualifiedMediaBytes - tailSafetyBytes`，且根分区是最后一个分区。
4. 将实测值和介质资格记录摘要写入 `image-layout.json`，同行复核后才把 `targetMedia` 改为
   `QUALIFIED`、把顶层 `lockState` 改为 `LOCKED`；不能只改顶层布尔值。
5. 写一张可返工卡，启动后确认 `ecobin-expand-rootfs.service` 只扩展最终根分区和 ext4，成功
   后再次运行不会重复破坏布局。
6. 在扩分区前、扩分区后、扩文件系统后分别断电一次，确认重启只会继续原步骤；扩分区后必须
   证明内核看到的 sysfs 扇区数严格增大，扩文件系统后 ext4 尾部与分区尾部相差小于一个块；
   任何不确定
   状态必须阻止注册和正式业务。
7. 由独立复核人形成符合 `rootfs-qualification-evidence.schema.json` 的确定性 ext4 资格证据，
   记录其 SHA-256，并把实际文件摘要固定进仓库外正式发布策略；介质资格证据与 rootfs 资格证据
   是两个不同事实，缺少任一个都不能正式发布。

介质测量是只读操作，但必须先用 `/dev/disk/by-id/` 唯一识别整卡，不能把分区或系统盘当成
样本。下面以同批次两张卡为最小示例；实际抽样可以增加到 256 张。先替换两个 by-id 路径和批次
编号，再在隔离 Linux 工位执行：

```bash
card_a='/dev/disk/by-id/REPLACE_WITH_CARD_A_ID'
card_b='/dev/disk/by-id/REPLACE_WITH_CARD_B_ID'
for card in "${card_a}" "${card_b}"; do
  test -b "${card}"
  test "$(lsblk -ndo TYPE "${card}")" = disk
  test "$(sudo blockdev --getss "${card}")" = 512
done

card_a_bytes="$(sudo blockdev --getsize64 "${card_a}")"
card_b_bytes="$(sudo blockdev --getsize64 "${card_b}")"
measured_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

python3 - /controlled/evidence/target-media-qualification-evidence.json \
  batch-20260823-a "${measured_at}" \
  card-a "${card_a_bytes}" card-b "${card_b_bytes}" <<'PY'
import json
from pathlib import Path
import sys

output = Path(sys.argv[1])
batch_id, measured_at = sys.argv[2:4]
sample_arguments = sys.argv[4:]
if len(sample_arguments) < 4 or len(sample_arguments) % 2:
    raise SystemExit("at least two sample-id/byte-count pairs are required")
measurements = [
    {
        "sampleId": sample_arguments[index],
        "measuredBytes": int(sample_arguments[index + 1]),
        "logicalSectorBytes": 512,
        "wholeDevice": True,
    }
    for index in range(0, len(sample_arguments), 2)
]
evidence = {
    "$schema": "./schemas/target-media-qualification-evidence.schema.json",
    "schemaVersion": 1,
    "artifactClass": "TARGET_MEDIA_BATCH_CAPACITY_QUALIFICATION",
    "qualificationState": "QUALIFIED",
    "method": "BLOCK_DEVICE_CAPACITY_SAMPLE_MINIMUM_V1",
    "deviceClass": "TF_CARD",
    "marketedCapacityGB": 32,
    "marketedCapacityBytes": 32000000000,
    "batchId": batch_id,
    "measuredAt": measured_at,
    "measurementTool": "blockdev --getsize64",
    "sampleSelection": "SAME_PROCUREMENT_BATCH_MULTIPLE_CARDS",
    "minimumQualifiedMediaBytes": min(item["measuredBytes"] for item in measurements),
    "measurements": measurements,
}
output.parent.mkdir(parents=True, exist_ok=True)
if output.exists():
    raise SystemExit(f"refusing to overwrite {output}")
output.write_text(
    json.dumps(evidence, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY

sha256sum /controlled/evidence/target-media-qualification-evidence.json
```

同行逐项核对样本属于同一批次、路径确为整卡、实测值和记录一致后，把证据中的最小值写入
`targetMedia.minimumQualifiedMediaBytes`，把最后得到的摘要写入 `targetMedia.evidenceSha256`，再把
介质和顶层锁状态改成 `QUALIFIED`/`LOCKED`。随后必须让工具复算文件，而不是只校验布局中的文本：

```bash
python3 tools/orangepi-image/lib/validate_inputs.py \
  --config-dir tools/orangepi-image \
  --target-media-qualification-evidence \
  /controlled/evidence/target-media-qualification-evidence.json
```

少于两张卡、重复样本 ID、非整卡、非 512 字节逻辑扇区、虚假 32 GB 容量、证据最小值与样本或
布局不一致，以及证据原始字节摘要不一致，都会失败关闭。

变更布局锁后必须重新做完整真机试点，不能沿用旧卡型的验收记录。

只有独立复核已经实际得到下面六项通过事实后，才可用当前锁文件生成确定性 rootfs 资格证据；
命令中的 `true` 是对已完成测试结果的记录，不是用来替代测试的开关：

```bash
python3 - /controlled/evidence/rootfs-qualification-evidence.json <<'PY'
import hashlib
import json
from pathlib import Path
import sys

config = Path("tools/orangepi-image")
layout_payload = (config / "image-layout.json").read_bytes()
layout = json.loads(layout_payload)
builder = json.loads((config / "builder.lock").read_bytes())
output = Path(sys.argv[1])
output.parent.mkdir(parents=True, exist_ok=True)
if output.exists():
    raise SystemExit(f"refusing to overwrite {output}")

evidence = {
    "schemaVersion": 1,
    "artifactClass": "DETERMINISTIC_EXT4_ROOTFS_QUALIFICATION",
    "method": "DETERMINISTIC_EXT4_REBUILD_V1",
    "imageLayoutSha256": hashlib.sha256(layout_payload).hexdigest(),
    "sourceGeometry": layout["sourceGeometry"],
    "builderDigest": builder["container"]["digest"],
    "e2fsprogsVersion": builder["tools"]["e2fsprogs"],
    "verification": {
        "differentSourceCtime": True,
        "semanticInventoryMatch": True,
        "byteIdenticalAfterNormalization": True,
        "exactExt4Profile": True,
        "readOnlyE2fsckClean": True,
        "fullPartitionCoverage": True,
    },
}
output.write_text(
    json.dumps(evidence, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY

python3 -m json.tool \
  /controlled/evidence/rootfs-qualification-evidence.json >/dev/null
sha256sum /controlled/evidence/rootfs-qualification-evidence.json
```

把最后一条命令得到的摘要交给正式策略复核人，固定到仓库外
`formal-release-policy.json.rootfsQualification.evidenceSha256`。后续聚合证明和发布命令必须使用
同一个文件；工具会复算原始文件摘要，并逐项比对布局、构建器、e2fsprogs 版本与六个事实。

## 5. 准备无秘密软件负载

软件负载必须由仓库提供的受锁定 ARM64 构建入口生成，包含：

- 普通硬件运行时及其签名验证证据；
- 一次性注册、独立远程维护、出厂验收各自的 Python 3.11 虚拟环境；
- 首次启动编排器、systemd 单元、NetworkManager/nftables/hostapd/dnsmasq 配置；
- 生产 `hardware.env`、已通过真机批准的 Air780E 批次配置；
- MCU 固件发布公钥和普通运行时发布公钥，不含任何私钥；
- `software-payload.lock.json`，逐文件记录类型、权限、长度和 SHA-256。

Air780E 配置 schema v2 不把 USB VID/PID 作为人工配置或准入值。设备必须自动找到唯一的、
具有可验证 USB 父设备且由 `rndis_host` 驱动的 RNDIS 网卡；VID/PID 只进入脱敏诊断。零个、
多个候选或错误驱动都必须失败关闭。`ECOBIN_CELLULAR_HIL_APPROVED=true` 只表示当前载板批次
已经完成真实 DHCP、默认路由、DNS 和 HTTPS 验证，不是操作员逐台勾选项。

生成锁后，先单独校验负载；目录内容在校验后不得再改变。具体命令以
`tools/orangepi-image/README.md` 中当前版本的“软件负载”章节为准。若仓库尚未提供正式负载
构建入口，或负载仍包含 `HIL_REQUIRED`，立即停止，不能人工拼目录后继续。

## 6. 两次独立构建候选镜像

正式发布要求干净且已提交的 Git 工作树。同一提交、相同基础镜像、相同负载、相同锁文件和
相同 release ID 分别在两个全新临时输出目录中构建：

```bash
tools/orangepi-image/run-builder.sh \
  --source /controlled/input/Orangepizero3_1.0.4_debian_bookworm_server_linux6.1.31.7z \
  --output-dir /controlled/build-a/ecobin-1.0.0 \
  --release-id ecobin-orangepi-1.0.0 \
  --version 1.0.0 \
  --software-payload /controlled/input/software-payload \
  --software-payload-sha256 <software-payload.lock.json 的 SHA-256> \
  --target-media-qualification-evidence \
  /controlled/evidence/target-media-qualification-evidence.json

tools/orangepi-image/run-builder.sh \
  --source /controlled/input/Orangepizero3_1.0.4_debian_bookworm_server_linux6.1.31.7z \
  --output-dir /controlled/build-b/ecobin-1.0.0 \
  --release-id ecobin-orangepi-1.0.0 \
  --version 1.0.0 \
  --software-payload /controlled/input/software-payload \
  --software-payload-sha256 <同一个 SHA-256> \
  --target-media-qualification-evidence \
  /controlled/evidence/target-media-qualification-evidence.json
```

构建器必须自行验证官方压缩包的文件名、长度、SHA-256、内部成员、解压后长度、ARM64 容器
digest、Debian snapshot 和精确包版本。候选审计必须证明：

- UART5 overlay 只启用一次，冲突 PWM overlay 未启用；
- BOOT0 为 wPi 2 低电平安全态，NRST 开漏 Gate 为 wPi 5 低电平安全态；
- 只有安全 GPIO、早期出站锁和首启编排需要开机启用，其他阶段服务不能各自绕过状态机；
- machine-id、SSH Host Key、随机种子、DHCP/Wi-Fi 历史、日志、缓存和设备唯一状态不存在；
- 本地 `root`、`orangepi` 密码已锁定；
- 正式程序、四类职责环境、systemd、生产配置和信任公钥均与软件负载锁一致；
- 候选中没有 K1、热点密码、设备凭证或镜像签名私钥。

两次候选的原始镜像必须字节级一致。两个正式策略分别固定身份、构建域和外部 Ed25519 公钥的
构建者，先各自生成并签署一个 build receipt（单次构建回执）。以候选 A 为例：

```bash
python3 tools/orangepi-image/lib/generate_build_receipt.py \
  --config-dir tools/orangepi-image \
  --candidate /controlled/build-a/ecobin-1.0.0/ecobin-orangepi-zero3-1.0.0.img \
  --manifest /controlled/build-a/ecobin-1.0.0/image-manifest.json \
  --builder-identity builder-a --builder-domain factory-domain-a \
  --signing-key-id builder_receipt_a_2026 \
  --invocation-uid <本次构建唯一 UUIDv4> \
  --completed-at <UTC 时间，例如 2026-08-23T12:00:00Z> \
  --target-media-qualification-evidence \
  /controlled/evidence/target-media-qualification-evidence.json \
  --output /controlled/proofs/build-receipt-a.json

sudo python3 tools/orangepi-image/lib/release_trust.py sign \
  --trust-policy /etc/ecobin-image-factory/formal-release-policy.json \
  --role builderReceipt \
  --private-key /controlled/keys/build-receipt-a-private.pem \
  --key-id builder_receipt_a_2026 \
  --payload /controlled/proofs/build-receipt-a.json \
  --output /controlled/proofs/build-receipt-a.sig
```

候选 B 必须由策略中的另一个身份、构建域和公钥执行，使用不同 invocation UID；不能复制 A 的
回执后换文件名。禁止绕过这个入口直接调用 OpenSSL：该入口会先关闭 core dump、拒绝任何活动
swap（包括 zram）、从 `LOCKED` 策略按 key ID 唯一定位构建者，再核对回执中的构建者身份、域和
签名 Key ID，最后用受保护快照完成签名且拒绝覆盖已有输出。聚合证明命令必须完整提供两个回执、
签名、公钥和实际 target-media/rootfs 资格证据：

```bash
sudo tools/orangepi-image/attest-candidates.sh \
  --candidate-a /controlled/build-a/ecobin-1.0.0/ecobin-orangepi-zero3-1.0.0.img \
  --manifest-a /controlled/build-a/ecobin-1.0.0/image-manifest.json \
  --candidate-b /controlled/build-b/ecobin-1.0.0/ecobin-orangepi-zero3-1.0.0.img \
  --manifest-b /controlled/build-b/ecobin-1.0.0/image-manifest.json \
  --software-payload /controlled/input/software-payload \
  --software-payload-sha256 <software-payload.lock.json 的 SHA-256> \
  --release-id ecobin-orangepi-1.0.0 --version 1.0.0 \
  --git-commit <40 位 Git commit> \
  --trust-policy /etc/ecobin-image-factory/formal-release-policy.json \
  --target-media-qualification-evidence \
  /controlled/evidence/target-media-qualification-evidence.json \
  --rootfs-qualification-evidence /controlled/evidence/rootfs-qualification-evidence.json \
  --receipt-a /controlled/proofs/build-receipt-a.json \
  --receipt-signature-a /controlled/proofs/build-receipt-a.sig \
  --receipt-public-key-a /controlled/keys/build-receipt-a-public.pem \
  --receipt-b /controlled/proofs/build-receipt-b.json \
  --receipt-signature-b /controlled/proofs/build-receipt-b.sig \
  --receipt-public-key-b /controlled/keys/build-receipt-b-public.pem \
  --signing-private-key /controlled/keys/build-attestation-private.pem \
  --signing-public-key /controlled/keys/build-attestation-public.pem \
  --signing-key-id build_attestation_2026 \
  --output-attestation /controlled/proofs/build-attestation.json \
  --output-signature /controlled/proofs/build-attestation.sig
```

回执和聚合证明共同绑定候选 raw/manifest 摘要、软件负载锁、Git 提交、release ID/version 和
全部输入锁；发布端不得信任候选 manifest 中可编辑的 `releaseEligible` 等布尔字段。若 ext4
时间、日志、随机目录名或 journal 导致候选差异，发布必须失败；不能只比较“文件内容看起来
相同”。

## 7. 在隔离工位注入 K1 和热点密码

K1 和热点密码分别保存在两个 root 所有、`0600`、非符号链接、非硬链接的输入文件中。命令
参数只传文件路径，绝不把明文放进 argv、环境变量、终端历史或日志。

先确认第 6 节已经验签两个构建者回执、生成有效聚合 build attestation，并证明无秘密 raw
完全一致，再从其中一份候选执行一次受控封存。
`seal-image.sh` 必须接收镜像外期望的构建身份和证明，输出由独立封存证据密钥签署的
seal evidence；不得只读取镜像内部 manifest 自证。正式 CLI 以脚本 `--help` 为准，流程形态为：

```bash
sudo tools/orangepi-image/seal-image.sh \
  --candidate /controlled/build-a/ecobin-1.0.0/ecobin-orangepi-zero3-1.0.0.img \
  --output /controlled/sealed/release.img \
  --build-attestation <候选 A 的已签名构建证明> \
  --build-attestation-signature <候选 A 构建证明签名> \
  --build-attestation-public-key <构建证明公钥> \
  --software-payload-sha256 <software-payload.lock.json 的 SHA-256> \
  --release-id ecobin-orangepi-1.0.0 \
  --version 1.0.0 \
  --git-commit <40 位 Git commit> \
  --trust-policy /etc/ecobin-image-factory/formal-release-policy.json \
  --target-media-qualification-evidence \
  /controlled/evidence/target-media-qualification-evidence.json \
  --enrollment-key-file /run/secrets/ecobin-k1 \
  --setup-ap-key-file /run/secrets/ecobin-setup-ap \
  --sealing-private-key /run/secrets/image-sealing.key \
  --sealing-public-key /controlled/keys/image-sealing.pub \
  --sealing-key-id image-sealing-2026 \
  --evidence /controlled/sealed/release.seal-evidence.json \
  --evidence-signature /controlled/sealed/release.seal-evidence.sig
```

执行前还必须确认 `/proc/swaps` 只有表头、没有 swapfile、交换分区或 zram。封存脚本和注册服务
都会失败关闭；不得通过临时改脚本或关闭检查继续使用 K1。上例只展示参数关系，路径、release
身份、公钥及 key ID 必须来自本批次已复核的外部构建记录；完整参数仍以
`tools/orangepi-image/seal-image.sh --help` 为准。

seal evidence 只允许记录“已注入”的布尔事实及候选/封存镜像身份，不得记录秘密值或秘密
摘要。封存审计必须同时证明 K1 和热点密码存在、权限为 `root:root 0600`，且设备唯一状态仍
不存在。秘密输入文件和输出目录都必须经过属主、权限、普通文件、单硬链接和防符号链接竞态检查。

## 8. 生成签名发布目录

发布入口必须：

1. 用正式发布策略固定的构建证明公钥验签聚合 attestation，并核对其中两个构建回执引用、
   两份无秘密候选 raw、target-media/rootfs 资格证据和被认证输入；两个回执的原始签名已在第 6 节生成聚合
   attestation 前验证，发布包不把它们冒充为需要分发的运行制品；
2. 验证单张封存镜像及其签名 seal evidence 与其中一份候选严格绑定；不再要求两次读写注入
   秘密后的 ext4 raw 字节相同；
3. 使用锁定 zstd 参数生成确定性 `.img.zst`；
4. 生成 release manifest、apt/Python 清单和 SPDX SBOM；
5. 用外部 Ed25519 私钥签署完整发布校验清单；该签名必须覆盖压缩镜像、manifest、SBOM、
   apt/Python 清单和需要随包分发的 schema，不能只签镜像而让元数据处于未认证状态；
6. 再次验签发布闭包；
7. 只把压缩镜像、签名、校验清单、公开元数据、写卡工具和清单移入发布目录，不复制原始
   含秘密镜像或私钥。

```bash
sudo tools/orangepi-image/release-image.sh \
  --sealed-image /controlled/sealed/release.img \
  --seal-evidence /controlled/sealed/release.seal-evidence.json \
  --seal-evidence-signature /controlled/sealed/release.seal-evidence.sig \
  --seal-evidence-public-key /controlled/keys/image-sealing.pub \
  --build-attestation /controlled/proofs/build-attestation.json \
  --build-attestation-signature /controlled/proofs/build-attestation.sig \
  --build-attestation-public-key /controlled/keys/build-attestation-public.pem \
  --target-media-qualification-evidence \
  /controlled/evidence/target-media-qualification-evidence.json \
  --rootfs-qualification-evidence /controlled/evidence/rootfs-qualification-evidence.json \
  --candidate-manifest /controlled/build-a/ecobin-1.0.0/image-manifest.json \
  --trust-policy /etc/ecobin-image-factory/formal-release-policy.json \
  --signing-private-key /run/secrets/image-release-signing.key \
  --signing-public-key /controlled/keys/image-release-signing.pub \
  --signing-key-id image_release_2026 \
  --output-dir /controlled/releases/ecobin-orangepi-zero3-1.0.0
```

正式命令及参数以 `release-image.sh --help` 为准。只要正式发布策略仍为 `UNLOCKED`，或脚本
无法验签外部构建证明、seal evidence、manifest/SBOM 和完整发布闭包，就不得建立正式发布。

发布目录进入最小权限内部制品库。另行保存发布审批记录：Git commit、release ID、签名 key
ID、公钥指纹、两次构建工位、构建员、发布员和时间。记录中不得出现 K1 或热点密码摘要。

## 9. 写卡前验签、写入和完整复读

先用系统工具列出磁盘，人工确认 TF 卡对应的 WSL/Linux 整盘设备。不得选择分区、mapper、
已挂载设备、非 removable 设备或承载当前根文件系统的磁盘。设备路径必须由同一操作员输入
两次且完全一致。

写卡工具必须按以下顺序自动执行，任一步失败都判该卡不可装机：

1. 用安装在发布目录之外、受控只读的可信写卡入口和固定公钥指纹验证完整发布校验清单签名；
2. 校验 manifest、SBOM、apt/Python 清单和压缩镜像；
3. 完整解压到哈希流，核对原始镜像长度和 SHA-256；
4. 再次检查目标为未挂载的 removable 整盘，并从已签名 manifest 读取
   `qualificationState=QUALIFIED` 和 `minimumQualifiedMediaBytes`；真实块设备容量小于门槛时必须在
   任何 `dd` 写入前停止；
5. 写入 TF 卡并 flush；
6. 从 TF 卡复读原始镜像的完整有效范围并核对 SHA-256。

不得在验证前直接执行发布目录内的 `.sh` 或 `.ps1`：包内脚本不能用自己证明自己的可信性。
PowerShell 可信入口只负责先验签，再把已人工映射到 WSL 的块设备和显式文件路径转交给已验签
的 Linux 校验器，不会猜测 Windows PhysicalDrive。具体参数以仓库外受控写卡入口为准。

每张卡保存写卡结果：发布号、卡序列或工位追踪号、实际容量、写前签名结果、写后复读摘要、
操作员和时间。卡序列不得替代设备正式身份。

## 10. 装机和首次通电

断电状态下检查：

- MCU 已由线下烧录器预装 revision 2 固件；香橙派不负责全新 MCU 首刷；
- UART5 使用物理 8/10 号针并共地；
- BOOT0 接物理 7 号针，MCU 侧约 10 kΩ 下拉；BOOT1/PB2 固定下拉；
- NRST 接物理 11 号针经 2N7002 开漏：PC6 经 1 kΩ 到 Gate，Gate 100 kΩ 下拉，Source
  共地，Drain 接 NRST，NRST 由 MCU 3.3 V 经 10 kΩ 上拉并保留 10～100 nF 对地电容；
- DECXIN 和 icspring 摄像头插在批准的 USB 位置；
- Air780E 载板供电、天线、SIM、PWRKEY 和 USB 数据连接完好；
- 设备舱体、门锁、电机和清运工装处于可安全动作状态。

首次通电后不要 SSH 登录。手机查找 `EcoBin-Factory-XXXXXXXX`，使用受控共用密码连接，访问
`http://10.42.0.1/`。即使未插 SIM 或蜂窝离线，热点也应出现。页面不可访问时，该设备留在
返工区，不得临时开放 SSH、以太网默认路由或通用 Wi-Fi 配置来绕过。

## 11. 离线硬件验收顺序

本阶段必须保持整机出站锁；普通硬件进程、MQTT、OneNet、COS、注册和 NTP 均不得运行。
验收程序不打开生产 `/var/lib/ecobin/hardware/edge.db`，临时照片只位于
`/run/ecobin/factory-test/photos/`。

按网页顺序执行：

1. 系统检查：根分区扩容完成、磁盘可写且空间正常、machine-id 已首次生成、安全 GPIO 和
   出厂网络隔离健康；
2. MCU 身份：F3 必须成功、`statusCode=0`、固定帧 revision 为 2、硬件兼容标识和固件身份
   符合当前批次；
3. MCU 自检：在限定时间内采集稳定 F1 样本，重量、红外、烟感等字段有效；
4. Bootloader 线路：F2 后只读探测 STM32 ROM Device ID，必须精确为 `0x0410`；随后 BOOT0
   回低、应用复位，重新验证原 F3 身份和健康 F1，不写 MCU Flash；
5. 双摄：分别拍摄当前临时画面，再由操作员根据本次 nonce 确认 DECXIN 为箱外、icspring
   为箱内；不能沿用上一次启动或换线前的确认；
6. 称重：空载稳定采样，放置 500 g 砝码后稳定采样，增量必须为 490～510 g；取下砝码后
   再次稳定采样并确认回零；
7. 离线投递：确认门体安全后仅执行一次测试 BB/AA，观察屏幕、按钮、门锁/电机、红外和最终
   DD；按网页提示确认测试物已经处理；
8. 离线清运：仅执行一次 EE，观察屏幕、按钮、清运锁和最终 EF；动作完成后先检查门体，再在
   网页执行独立的“门已关闭”确认；
9. 安全交接：受控复位 MCU、清理串口输入、等待静默窗口、重新验证 F3/F1，释放 UART/GPIO/
   摄像头后才允许首次启动编排进入联网阶段。

AA、EE 或 F2 只要“可能已经发送”，程序就必须先可靠保存恢复锁。浏览器刷新、手机断开或设备
断电都不能创建第二次动作。重启后页面只允许查看同一动作和执行受控恢复；原身份/F1 或门体
安全无法重新证明时保持 `RECOVERY_REQUIRED`，禁止注册、正式业务和离厂。

离线阶段通过后，保存最终本地报告摘要，删除临时照片。再比较生产 EdgeStore 和正式照片目录：
它们必须保持未创建或字节级不变，抓包也必须证明没有 DNS、HTTP(S)、MQTT、COS 或其他 WAN
请求。

## 12. Air780E 联网、注册和正式运行时

本地报告为当前 image release、硬件配置摘要和 MCU 身份的有效 `PASSED` 且无恢复锁后，首启
编排器才允许切换防火墙并激活 Air780E RNDIS profile。验收以下事实：

- 自动发现唯一 RNDIS 网卡，不依赖接口名恰好为 `usb0`；
- DHCP 地址、默认路由、DNS、可信校时和后端 HTTPS 均从该接口通过；
- 同时连接以太网时也不能把生产探针或注册流量切到以太网；
- 正常过程不执行 `AT+SETUSB`、`AT+RNDISCALL`、PPP 拨号或模组固件更新；
- 断线后由持续运行的蜂窝协调服务重连，不重新开放出厂动作。

随后一次性注册服务使用镜像内 K1 请求身份。注册成功的判据不是“HTTP 返回成功”，而是正式
设备凭证已经以安全权限可靠落盘并可重新读取。之后系统必须幂等删除 K1 和一次性注册实现；
断电发生在响应解密、凭证落盘或 K1 删除之间时，重启应依据凭证事实继续清理，不能创建第二个
资产。

普通硬件、OneNet、COS 和独立远程维护代理启动后，完成真实厂家初始袋扫描以及后端机器验收。
离线测试报告不能替代平台验收，也不能生成用户订单、清运、袋、钱包或返现记录。

## 13. 云端授权和单向封存

后端只有在当前设备机器验收已经为 `PASSED` 时，才能创建可靠命令
`AUTHORIZE_FACTORY_SEAL`。命令绑定 hardwareSn、当前验收代次、验收证据、厂家袋修订号和
有序袋集合摘要。该命令的 `expiresAt` 是设备首次可靠受理截止时间，不是后台消费者执行
截止时间：首次投递必须在期限内完成全部校验并写入 `command_inbox`；一旦可靠受理，排队、
重启恢复和完全相同的同 UID 重投只使用数据库保存的原始受理时间继续收敛。已经过期才首次
到达的命令仍拒绝，命令载荷或本地工具不能补传/伪造受理时间；该例外不适用于开门等物理
命令、COS 临时凭证或远程维护期限。设备必须在一个 EdgeStore 事务中完成：

- 校验命令目标及本地当前报告/配置/MCU 身份；
- 保存封存授权事实；
- 把命令置为终态；
- 写入可靠的 `DEVICE_COMMAND_OBSERVED` 接受或拒绝事件。

旧代次、袋集合变化、报告摘要变化、错误设备、重复但载荷不同的命令都不能开放封存按钮。
完全相同的重复命令只能返回原结果，不能创建第二个授权。

页面显示“可以结束出厂模式”后，操作员再次检查门体、安全交接、正式运行时和当前后端代次，
再点击确认。系统必须先以“临时文件写入 → 文件 fsync → 原子替换 → 目录 fsync”形成
`sealed.json`，然后才幂等执行：

1. 删除热点密码、K1 残留、一次性注册代码和临时验收照片；
2. 停止出厂热点、门户和验收执行器；
3. 加载正式生产防火墙；
4. 在同一个 EdgeStore 事务中把本地授权从 `SEALING` 置为 `SEALED`，并写入唯一、可重试的
   `FACTORY_SEAL_COMPLETED`（物理封存完成）可靠事件；该事件绑定当前设备、授权命令、验收
   代次/证据、厂家袋集合、本地镜像与报告、操作员确认及实际清理完成时间；
5. 将首启阶段收敛为 `COMPLETE`。

任何一步断电，只要 `sealed.json` 目录项已经存在，重启就绝不重新开放热点，只继续清理。
本地 `SEALED` 和完成事件必须一起提交：不能出现“设备已经放开正式业务但平台永远收不到完成
事实”的窗口。事务提交前断电会重做清理；提交后断电会复用同一个事件 UID 和同一份载荷继续
上报，不能生成第二条。损坏或不完整的 seal 也必须失败关闭，不能把设备退回出厂模式。

在上述完成事务提交前，香橙派只接受注册、验收、事件确认、封存授权和远程支持等控制命令；
投递、清运、正式取样、空袋基准和云端 MCU 升级统一以 `FACTORY_NOT_SEALED` 拒绝。平台在可信
收到当前代次的 `FACTORY_SEAL_COMPLETED` 前，也不能把资产分配给租户或机构。这样既不会触发
真实执行器，也不会提前生成正式订单、袋、清运或钱包事实。

## 14. 封存后冷启动和离厂门禁

封存完成后断电，等待电源完全释放，再冷启动一次。逐项核对：

- `sealed.json` 绑定当前发布、本地报告和后端验收代次，首启阶段为 `COMPLETE`；
- K1/热点密码文件路径、一次性注册实现、临时照片和活动恢复状态不存在；该项是逻辑删除
  门禁，不声称 TF 闪存旧物理页已经完成可验证擦除；
- 手机不再看到该设备的出厂热点；
- UART5/MCU F3/F1、普通硬件、OneNet、COS 和远程维护代理健康；
- Air780E 离线再恢复不会重新进入出厂模式；
- 后端仍显示当前机器验收 `PASSED`，当前封存命令已可靠确认，并已可信应用同一代次的
  `FACTORY_SEAL_COMPLETED`；资产在该事实到达前始终不能分配给租户或机构；
- 真实初始袋记录完整；没有由离线动作测试产生的业务订单、清运或钱包记录。

使用 [工厂清单](../../tools/orangepi-image/factory-checklist.md) 记录设备、发布号、操作员、复核人
和日期。首台试点还必须附 HIL 抓包、串口/电平、500 g 原始稳定样本、双摄角色、断电注入、
写卡复读及后端代次证据。只有项目负责人签认首台试点后才能扩大写卡。

## 15. 故障、返工和回退

### 15.1 可以直接重写卡的情况

只有设备尚未注册、尚未封存，且不存在 AA、EE、F2“可能已发送”或其他恢复锁时，才可用上一
个已签名发布重新写卡。重写前仍需先验签，重写后重新完成全部验收。

### 15.2 禁止直接重写的情况

- AA、EE 或 F2 可能已经发送：先完成原动作的 MCU 复位、F3/F1 复验和人工门体确认；
- 注册状态文件已存在：优先继续同一次注册，不能删状态反复创建资产；
- 正式凭证已落盘：必须保留原设备身份，不能复制另一台卡或干净母镜像冒充；
- 已形成任何 `sealed.json`：只允许继续单向清理或走专门的身份保留维修流程；
- SQLite 已前向迁移：代码回退前先确认数据库兼容，不能仅切旧 `/opt` 版本。

### 15.3 现场失败的处置

把设备留在受控返工区，记录稳定错误码、发布号、阶段和是否存在恢复锁。网页不得提供 shell
或日志全文；需要深入诊断时使用既有按需反向 SSH 授权。不要为“先让它通过”而翻转 GPIO
极性、改摄像头角色、放宽 500 g 范围、切换 PPP/QMI、开放热点转发或删除状态文件。

修复后从失败阶段依据权威事实继续；不要手工把 JSON 中的状态改成 `PASSED`、`SEALED` 或
`COMPLETE`。

## 16. 变更后必须重做哪些验证

| 变更 | 最低重新验证范围 |
|---|---|
| 官方基础镜像、内核、分区或 TF 卡型号 | 布局锁、扩容、UART/GPIO、Wi-Fi AP、完整首台试点 |
| WiringOP、overlay、BOOT0/NRST 电路 | 电平、ROM `0x0410`、应用身份恢复、断电注入 |
| MCU revision 2 固件 | F3/F1、Bootloader 探测、投递/清运及远程升级模拟和真机 |
| 摄像头型号或 USB 口 | 多次冷启动稳定路径、本次画面 nonce 和人工角色确认 |
| 称重模块或结构件 | 空载/500 g/取下稳定采样和 490～510 g 门槛 |
| Air780E 载板、SIM 批次或网络策略 | 唯一 RNDIS、DHCP、路由、DNS、校时、HTTPS、OneNet/COS、断线重连 |
| 首启、验收、授权或 seal schema | 全部断电恢复、旧/重复命令、封存前后冷启动 |
| Python/apt 软件负载 | 两次候选构建、SBOM、全部自动回归和完整试点 |
| 镜像签名密钥或发布工具 | 完整签名闭包、篡改拒绝、写卡复读和信任根轮换记录 |

任何真实硬件、网络或秘密输入尚未就绪时，正确结果是保持门禁关闭和记录待验事实，不是降低
检查标准。
