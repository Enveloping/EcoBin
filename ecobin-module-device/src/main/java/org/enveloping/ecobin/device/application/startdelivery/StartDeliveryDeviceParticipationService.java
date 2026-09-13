package org.enveloping.ecobin.device.application.startdelivery;

import org.enveloping.ecobin.device.api.command.StartDeliveryDeviceCommand;
import org.enveloping.ecobin.device.api.persistence.DeliverySessionBusinessFactsRef;
import org.enveloping.ecobin.device.api.persistence.DeviceDeliveryPortRef;
import org.enveloping.ecobin.device.api.port.StartDeliveryBusinessFactsPort;
import org.enveloping.ecobin.device.api.port.StartDeliveryDeviceParticipationPort;
import org.enveloping.ecobin.device.api.query.StartDeliveryBusinessFactsQuery;
import org.enveloping.ecobin.device.api.result.LockedStartDeliveryBusinessFacts;
import org.enveloping.ecobin.device.api.result.StartDeliveryDeviceResult;
import org.enveloping.ecobin.device.application.target.DeviceConfigurationCanonicalizer;
import org.enveloping.ecobin.framework.reliability.DeviceCommandTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskRegistrationPort;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;

/**
 * 在 recycling 开启的事务中建立设备侧投递事实。
 *
 * <p>device 是投递会话、整机占位、设备命令的唯一写入者。本服务只保存“允许设备尝试
 * 开始”的意图，不把命令入库等同于门已打开；真正的现场准入和物理执行仍由香橙派完成。</p>
 */
@Service
public class StartDeliveryDeviceParticipationService
        implements StartDeliveryDeviceParticipationPort {

    static final String TASK_TYPE = "START_DELIVERY_SESSION";
    static final String TARGET_TYPE = "DELIVERY_SESSION";
    static final int MAX_AUTO_ATTEMPTS = 1;
    static final Duration START_AUTHORIZATION_WINDOW =
            Duration.ofSeconds(60);
    /*
     * 当前实现占位值：表结构要求保存“最终结果最迟恢复时间”，但业务尚未冻结具体时长。
     * 这里只落库，不启动恢复 worker，更不会为了恢复结果而自动重放物理开门命令。
     */
    static final Duration RESULT_RECOVERY_WINDOW =
            Duration.ofHours(24);
    private static final long UINT32_MAX = 4_294_967_295L;

    private final StartDeliveryDeviceRepository repository;
    private final StartDeliveryBusinessFactsPort businessFacts;
    private final DeviceDeliveryPortRefFactory portRefFactory;
    private final ReliableDeviceTaskRegistrationPort taskRegistration;
    private final DeviceCommandTaskRefFactory taskRefFactory;
    private final DeviceConfigurationCanonicalizer canonicalizer;
    private final ObjectMapper objectMapper;

    StartDeliveryDeviceParticipationService(
            StartDeliveryDeviceRepository repository,
            StartDeliveryBusinessFactsPort businessFacts,
            DeviceDeliveryPortRefFactory portRefFactory,
            ReliableDeviceTaskRegistrationPort taskRegistration,
            DeviceCommandTaskRefFactory taskRefFactory,
            DeviceConfigurationCanonicalizer canonicalizer,
            ObjectMapper objectMapper) {
        this.repository = repository;
        this.businessFacts = businessFacts;
        this.portRefFactory = portRefFactory;
        this.taskRegistration = taskRegistration;
        this.taskRefFactory = taskRefFactory;
        this.canonicalizer = canonicalizer;
        this.objectMapper = objectMapper;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public StartDeliveryDeviceResult start(
            StartDeliveryDeviceCommand command) {
        requireActiveTransaction();
        Objects.requireNonNull(command, "command");
        String deviceCode =
                normalizeDeviceCode(command.deviceCode());
        return command.organizationUserRef()
                .withDeliverySessionUserOnce(
                        (tenantId,
                         organizationId,
                         organizationUserId) -> startWithinTransaction(
                                command,
                                deviceCode,
                                tenantId,
                                organizationId,
                                organizationUserId));
    }

    private StartDeliveryDeviceResult startWithinTransaction(
            StartDeliveryDeviceCommand command,
            String deviceCode,
            long tenantId,
            long organizationId,
            long organizationUserId) {
        // 下列 FOR UPDATE 顺序是设备并发协议：先排除同一用户旧会话，再锁永久资产、
        // 租户、机构、OneNet 在线事实、整机占位、配置和投口。
        StartDeliveryDevicePolicy.requireNoActiveSession(
                repository.lockActiveSessionIds(
                        tenantId,
                        organizationId,
                        organizationUserId));

        StartDeliveryDeviceRepository.AssetRow asset =
                repository.lockAssetByDeviceCode(deviceCode)
                        .orElseThrow(
                                StartDeliveryDevicePolicy
                                        ::assetUnavailable);
        if (asset.tenantId() == null || asset.organizationId() == null) {
            throw StartDeliveryDevicePolicy.assetUnavailable();
        }
        StartDeliveryDeviceRepository.SubjectStatusRow tenant =
                repository.lockTenant(asset.tenantId())
                        .orElseThrow(
                                StartDeliveryDevicePolicy
                                        ::assetUnavailable);
        StartDeliveryDeviceRepository.SubjectStatusRow organization =
                repository.lockOrganization(
                                asset.tenantId(),
                                asset.organizationId())
                        .orElseThrow(
                                StartDeliveryDevicePolicy
                                        ::assetUnavailable);
        StartDeliveryDevicePolicy.requireAssetAvailable(
                asset,
                tenant,
                organization,
                tenantId,
                organizationId,
                deviceCode,
                command.portNo());
        StartDeliveryDevicePolicy.requireSoftwareAdmission(asset);
        StartDeliveryDevicePolicy.requireOnenetOnline(
                repository.lockTransportPresence(asset.id())
                        .orElse(null));
        StartDeliveryDevicePolicy.requireUnoccupied(
                repository.lockOccupancy(asset.id()).orElse(null));
        StartDeliveryDevicePolicy.requireNoReleasedPendingWork(
                repository.lockReleasedPendingDeliveryIds(asset.id()));

        StartDeliveryDeviceRepository.ConfigurationRow configuration =
                repository.lockLatestConfiguration(
                                 tenantId,
                                 organizationId,
                                asset.id())
                        .orElseThrow(
                                StartDeliveryDevicePolicy
                                        ::configurationNotApplied);
        StartDeliveryDevicePolicy.requireConfigurationApplied(configuration);
        StartDeliveryDeviceRepository.PortRow port =
                repository.lockPort(
                                tenantId,
                                organizationId,
                                asset.id(),
                                command.portNo())
                        .orElseThrow(() ->
                                StartDeliveryDevicePolicy.portUnavailable(
                                        "指定投口不存在"));
        StartDeliveryDeviceRepository.PortConfigurationRow
                portConfiguration =
                repository.lockPortConfiguration(
                                tenantId,
                                organizationId,
                                asset.id(),
                                configuration.id(),
                                port.id())
                        .orElseThrow(
                                StartDeliveryDevicePolicy
                                        ::configurationNotApplied);
        StartDeliveryDevicePolicy.requirePortConfigured(
                portConfiguration);
        DeviceDeliveryPortRef devicePort = portRefFactory.issue(
                tenantId,
                organizationId,
                asset.id(),
                port.id());
        // 通过受限端口让 recycling 锁定当前袋等业务事实。device 只拿一次性引用，
        // 不跨模块读取 recycling 私表，也不能把内部 BIGINT 外键带出当前事务。
        LockedStartDeliveryBusinessFacts lockedBusiness =
                Objects.requireNonNull(
                        businessFacts.lockForStart(
                                new StartDeliveryBusinessFactsQuery(
                                        devicePort,
                                        command.deliveryRule(),
                                        portConfiguration.fullnessMode())),
                        "lockedBusiness");
        requireBagUid(lockedBusiness.bagUid());

        LocalDateTime now = repository.databaseNow();

        UUID sessionUid = UUID.randomUUID();
        UUID commandUid = UUID.randomUUID();
        LocalDateTime authorizationExpiresAt =
                now.plus(START_AUTHORIZATION_WINDOW);
        LocalDateTime resultRecoveryDeadlineAt =
                authorizationExpiresAt.plus(
                        RESULT_RECOVERY_WINDOW);
        Instant issuedAt = now.toInstant(ZoneOffset.UTC);
        Instant expiresAt =
                authorizationExpiresAt.toInstant(ZoneOffset.UTC);
        long unitPriceTenThousandths =
                unitPriceTenThousandths(
                        portConfiguration.unitPriceYuanPerKg());

        // 会话开始时冻结价格、袋、配置和阈值；后台稍后改价或改配置不能回写本次物理作业。
        Map<String, Object> payload = deliveryPayload(
                sessionUid,
                command.portNo(),
                lockedBusiness.bagUid(),
                configuration,
                unitPriceTenThousandths);
        byte[] payloadSha256 =
                canonicalizer.payloadSha256(payload);
        Map<String, Object> semanticEnvelope = semanticEnvelope(
                commandUid,
                asset.hardwareSn(),
                sessionUid,
                issuedAt,
                expiresAt,
                payload,
                payloadSha256);
        byte[] semanticEnvelopeSha256 =
                canonicalizer.payloadSha256(semanticEnvelope);
        String semanticEnvelopeJson = writeJson(semanticEnvelope);

        // 会话、整机占位、命令和可靠任务必须在同一数据库事务中提交。
        // 因此不会出现“HTTP 已受理但命令丢失”，也不会留下没有会话的孤立占位。
        long sessionId = insertSession(
                lockedBusiness,
                command,
                sessionUid,
                tenantId,
                organizationId,
                organizationUserId,
                asset.id(),
                port.id(),
                configuration,
                portConfiguration,
                authorizationExpiresAt,
                resultRecoveryDeadlineAt,
                now);
        repository.insertOccupancy(
                asset.id(),
                tenantId,
                organizationId,
                sessionId,
                now);
        long commandId = repository.insertCommand(
                new StartDeliveryDeviceRepository.CommandInsert(
                        commandUid,
                        tenantId,
                        organizationId,
                        asset.id(),
                        sessionId,
                        semanticEnvelopeJson,
                        semanticEnvelopeSha256,
                        now));
        registerReliableTask(
                command,
                sessionUid,
                commandUid,
                asset,
                commandId,
                semanticEnvelopeSha256);

        return new StartDeliveryDeviceResult(
                sessionUid,
                commandUid,
                expiresAt,
                deviceCode,
                command.portNo());
    }

    private long insertSession(
            LockedStartDeliveryBusinessFacts lockedBusiness,
            StartDeliveryDeviceCommand command,
            UUID sessionUid,
            long tenantId,
            long organizationId,
            long organizationUserId,
            long assetId,
            long portId,
            StartDeliveryDeviceRepository.ConfigurationRow configuration,
            StartDeliveryDeviceRepository.PortConfigurationRow
                    portConfiguration,
            LocalDateTime authorizationExpiresAt,
            LocalDateTime resultRecoveryDeadlineAt,
            LocalDateTime now) {
        byte[] deliveryConfigurationDigest =
                HexFormat.of().parseHex(
                        command.deliveryRule().contentSha256());
        return lockedBusiness.persistenceRef()
                .withBusinessForeignKeysOnce(keys -> {
                    requireBusinessScope(
                            keys,
                            tenantId,
                            organizationId);
                    return repository.insertSession(
                            new StartDeliveryDeviceRepository.SessionInsert(
                                    sessionUid,
                                    tenantId,
                                    organizationId,
                                    assetId,
                                    portId,
                                    organizationUserId,
                                    configuration.id(),
                                    configuration.version(),
                                    configuration.contentSha256(),
                                    configuration.mcuPayloadSha256(),
                                    portConfiguration.id(),
                                    keys.deliveryConfigurationKey(),
                                    deliveryConfigurationDigest,
                                    keys.bagKey(),
                                    lockedBusiness.bagUid(),
                                    lockedBusiness.bagCode(),
                                    portConfiguration
                                            .unitPriceYuanPerKg(),
                                    command.deliveryRule()
                                            .openBalanceFloorCent(),
                                    command.deliveryRule()
                                            .maxReviewAbsWeightGrams(),
                                    configuration
                                            .negativeWeightThresholdGrams(),
                                    configuration
                                            .continueDeliveryWaitMs(),
                                    authorizationExpiresAt,
                                    resultRecoveryDeadlineAt,
                                    now));
                });
    }

    private void registerReliableTask(
            StartDeliveryDeviceCommand command,
            UUID sessionUid,
            UUID commandUid,
            StartDeliveryDeviceRepository.AssetRow asset,
            long commandId,
            byte[] semanticEnvelopeSha256) {
        // 可靠任务只引用已经冻结的命令摘要；worker 重试的是同一命令身份，
        // 不是重新执行一次开始投递业务判断或制造新的 sessionUid。
        Map<String, Object> taskSnapshot = new LinkedHashMap<>();
        taskSnapshot.put("schemaVersion", 2);
        taskSnapshot.put("commandUid", commandUid.toString());
        taskSnapshot.put("commandType", TASK_TYPE);
        taskSnapshot.put("hardwareSn", asset.hardwareSn());
        taskSnapshot.put("targetDeviceName", asset.hardwareSn());
        taskSnapshot.put(
                "target",
                Map.of(
                        "type",
                        TARGET_TYPE,
                        "uid",
                        sessionUid.toString()));
        taskSnapshot.put("payloadSchemaVersion", 2);
        taskSnapshot.put(
                "semanticPayloadSha256",
                canonicalizer.hex(semanticEnvelopeSha256));
        taskRegistration.register(
                new ReliableDeviceTaskRegistration(
                        TASK_TYPE,
                        TASK_TYPE + ":"
                                + sessionUid.toString()
                                .toUpperCase(Locale.ROOT),
                        TARGET_TYPE,
                        sessionUid.toString(),
                        taskRefFactory.issue(
                                asset.tenantId(),
                                asset.organizationId(),
                                asset.id(),
                                commandId),
                        2,
                        writeJson(taskSnapshot),
                        semanticEnvelopeSha256,
                        command.operationUid(),
                        null,
                        MAX_AUTO_ATTEMPTS,
                        false,
                        null));
    }

    private Map<String, Object> deliveryPayload(
            UUID sessionUid,
            int portNo,
            UUID bagUid,
            StartDeliveryDeviceRepository.ConfigurationRow configuration,
            long unitPriceTenThousandths) {
        Map<String, Object> config = new LinkedHashMap<>();
        config.put("version", configuration.version());
        config.put(
                "contentSha256",
                canonicalizer.hex(configuration.contentSha256()));
        config.put(
                "mcuPayloadSha256",
                canonicalizer.hex(configuration.mcuPayloadSha256()));

        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("sessionUid", sessionUid.toString());
        payload.put("portNo", portNo);
        payload.put("bagUid", bagUid.toString());
        payload.put("config", config);
        payload.put(
                "unitPriceTenThousandths",
                unitPriceTenThousandths);
        payload.put(
                "continueDeliveryWaitMs",
                configuration.continueDeliveryWaitMs());
        payload.put(
                "negativeWeightThresholdGrams",
                configuration.negativeWeightThresholdGrams());
        payload.put(
                "deliveryAutoCloseMs",
                configuration.deliveryAutoCloseMs());
        return payload;
    }

    private Map<String, Object> semanticEnvelope(
            UUID commandUid,
            String targetDeviceName,
            UUID sessionUid,
            Instant issuedAt,
            Instant expiresAt,
            Map<String, Object> payload,
            byte[] payloadSha256) {
        // 临时 COS 凭证不属于稳定业务语义，发送时才附加；否则凭证续期会改变命令摘要，
        // 破坏后端、OneNet 和香橙派之间的幂等比对。
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schemaVersion", 2);
        envelope.put("commandUid", commandUid.toString());
        envelope.put("commandType", TASK_TYPE);
        envelope.put("targetDeviceName", targetDeviceName);
        envelope.put(
                "target",
                Map.of(
                        "type",
                        TARGET_TYPE,
                        "uid",
                        sessionUid.toString()));
        envelope.put("issuedAt", issuedAt.toString());
        envelope.put("expiresAt", expiresAt.toString());
        envelope.put("payloadSchemaVersion", 2);
        envelope.put(
                "payloadSha256",
                canonicalizer.hex(payloadSha256));
        envelope.put("payload", payload);
        envelope.put("cosGrant", null);
        return envelope;
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "start-delivery command JSON cannot be encoded",
                    exception);
        }
    }

    private static String normalizeDeviceCode(String value) {
        String normalized = value.trim();
        if (!normalized.matches("Dv_[A-Za-z0-9_-]{24,61}")) {
            throw StartDeliveryDevicePolicy.assetUnavailable();
        }
        return normalized;
    }

    private static long unitPriceTenThousandths(BigDecimal value) {
        try {
            long result = value.setScale(
                            4,
                            RoundingMode.UNNECESSARY)
                    .movePointRight(4)
                    .longValueExact();
            if (result < 1 || result > UINT32_MAX) {
                throw StartDeliveryDevicePolicy.portUnavailable(
                        "当前投口单价超出设备协议范围");
            }
            return result;
        } catch (ArithmeticException exception) {
            throw StartDeliveryDevicePolicy.portUnavailable(
                    "当前投口单价无法按四位小数下发");
        }
    }

    private static void requireBusinessScope(
            DeliverySessionBusinessFactsRef.BusinessForeignKeys keys,
            long tenantId,
            long organizationId) {
        if (keys.tenantKey() != tenantId
                || keys.organizationKey() != organizationId) {
            throw new IllegalStateException(
                    "locked delivery business facts changed scope");
        }
    }

    private static void requireBagUid(UUID bagUid) {
        if (bagUid.version() != 4 || bagUid.variant() != 2) {
            throw new IllegalStateException(
                    "locked delivery bag must have a UUIDv4 identity");
        }
    }

    private static void requireActiveTransaction() {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()
                || !TransactionSynchronizationManager
                .isSynchronizationActive()) {
            throw new IllegalStateException(
                    "start-delivery device participation "
                            + "requires an existing transaction");
        }
    }
}
