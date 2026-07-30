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

@Service
public class StartDeliveryDeviceParticipationService
        implements StartDeliveryDeviceParticipationPort {

    static final String TASK_TYPE = "START_DELIVERY_SESSION";
    static final String TARGET_TYPE = "DELIVERY_SESSION";
    static final int MAX_AUTO_ATTEMPTS = 1;
    static final Duration START_AUTHORIZATION_WINDOW =
            Duration.ofSeconds(60);
    /*
     * Implementation-local placeholder: the schema requires a later deadline
     * for detecting a missing final result, while the business design has not
     * frozen that duration. This slice persists it but deliberately adds no
     * recovery worker and never automatically replays a physical start.
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
        String deploymentCode =
                normalizeDeploymentCode(command.deploymentCode());
        return command.organizationUserRef()
                .withDeliverySessionUserOnce(
                        (tenantId,
                         organizationId,
                         organizationUserId) -> startWithinTransaction(
                                command,
                                deploymentCode,
                                tenantId,
                                organizationId,
                                organizationUserId));
    }

    private StartDeliveryDeviceResult startWithinTransaction(
            StartDeliveryDeviceCommand command,
            String deploymentCode,
            long tenantId,
            long organizationId,
            long organizationUserId) {
        StartDeliveryDevicePolicy.requireNoActiveSession(
                repository.lockActiveSessionIds(
                        tenantId,
                        organizationId,
                        organizationUserId));

        long assetId = repository.findAssetIdByDeploymentCode(
                        deploymentCode)
                .orElseThrow(
                        StartDeliveryDevicePolicy
                                ::deploymentUnavailable);
        StartDeliveryDeviceRepository.AssetRow asset =
                repository.lockAsset(assetId)
                        .orElseThrow(
                                StartDeliveryDevicePolicy
                                        ::deploymentUnavailable);
        StartDeliveryDeviceRepository.ActiveDeploymentRow active =
                repository.lockActiveDeployment(asset.id())
                        .orElseThrow(
                                StartDeliveryDevicePolicy
                                        ::deploymentUnavailable);
        StartDeliveryDeviceRepository.DeploymentRow deployment =
                repository.lockDeployment(active.deploymentId())
                        .orElseThrow(
                                StartDeliveryDevicePolicy
                                        ::deploymentUnavailable);
        StartDeliveryDevicePolicy.requireDeploymentAvailable(
                asset,
                active,
                deployment,
                tenantId,
                organizationId,
                deploymentCode,
                command.portNo());

        StartDeliveryDeviceRepository.DeploymentRuntimeRow runtime =
                repository.lockDeploymentRuntime(
                                tenantId,
                                organizationId,
                                deployment.id())
                        .orElseThrow(
                                StartDeliveryDevicePolicy
                                        ::deploymentUnavailable);
        StartDeliveryDevicePolicy.requireUnoccupied(
                repository.lockOccupancy(asset.id()).orElse(null));

        StartDeliveryDeviceRepository.ConfigurationRow configuration =
                repository.lockLatestConfiguration(
                                tenantId,
                                organizationId,
                                deployment.id())
                        .orElseThrow(
                                StartDeliveryDevicePolicy
                                        ::configurationNotApplied);
        StartDeliveryDeviceRepository.ConfigurationApplicationRow
                application =
                repository.lockConfigurationApplication(
                                tenantId,
                                organizationId,
                                deployment.id(),
                                configuration.id())
                        .orElseThrow(
                                StartDeliveryDevicePolicy
                                        ::configurationNotApplied);
        StartDeliveryDevicePolicy.requireExactAppliedConfiguration(
                configuration,
                application,
                runtime);

        StartDeliveryDeviceRepository.PortRow port =
                repository.lockPort(
                                tenantId,
                                organizationId,
                                deployment.id(),
                                command.portNo())
                        .orElseThrow(() ->
                                StartDeliveryDevicePolicy.portUnavailable(
                                        "指定投口不存在"));
        StartDeliveryDeviceRepository.PortConfigurationRow
                portConfiguration =
                repository.lockPortConfiguration(
                                tenantId,
                                organizationId,
                                deployment.id(),
                                configuration.id(),
                                port.id())
                        .orElseThrow(
                                StartDeliveryDevicePolicy
                                        ::configurationNotApplied);
        StartDeliveryDevicePolicy.requirePortConfigured(
                portConfiguration);
        StartDeliveryDeviceRepository.PortRuntimeRow portRuntime =
                repository.lockPortRuntime(
                                tenantId,
                                organizationId,
                                deployment.id(),
                                port.id())
                        .orElseThrow(() ->
                                StartDeliveryDevicePolicy.portUnavailable(
                                        "当前投口没有运行状态"));

        LocalDateTime eligibilityCheckedAt = repository.databaseNow();
        StartDeliveryDevicePolicy.requireTrustedDeploymentRuntime(
                runtime,
                configuration,
                eligibilityCheckedAt);
        StartDeliveryDevicePolicy.requireTrustedPortRuntime(
                portRuntime,
                portConfiguration,
                runtime);

        DeviceDeliveryPortRef devicePort = portRefFactory.issue(
                tenantId,
                organizationId,
                deployment.id(),
                port.id());
        LockedStartDeliveryBusinessFacts lockedBusiness =
                Objects.requireNonNull(
                        businessFacts.lockForStart(
                                new StartDeliveryBusinessFactsQuery(
                                        devicePort,
                                        command.deliveryRule(),
                                        portConfiguration.fullnessMode())),
                        "lockedBusiness");
        requireBagUid(lockedBusiness.bagUid());

        /*
         * Business-fact locking may have waited. Re-read database time and
         * recheck freshness before deriving the full 60-second authorization.
         */
        LocalDateTime now = repository.databaseNow();
        StartDeliveryDevicePolicy.requireTrustedDeploymentRuntime(
                runtime,
                configuration,
                now);

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
                deploymentCode,
                sessionUid,
                issuedAt,
                expiresAt,
                payload,
                payloadSha256);
        byte[] semanticEnvelopeSha256 =
                canonicalizer.payloadSha256(semanticEnvelope);
        String semanticEnvelopeJson = writeJson(semanticEnvelope);

        long sessionId = insertSession(
                lockedBusiness,
                command,
                sessionUid,
                tenantId,
                organizationId,
                organizationUserId,
                deployment.id(),
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
                deployment.id(),
                sessionId,
                now);
        long commandId = repository.insertCommand(
                new StartDeliveryDeviceRepository.CommandInsert(
                        commandUid,
                        tenantId,
                        organizationId,
                        deployment.id(),
                        sessionId,
                        semanticEnvelopeJson,
                        semanticEnvelopeSha256,
                        now));
        registerReliableTask(
                command,
                sessionUid,
                commandUid,
                deployment,
                asset,
                commandId,
                semanticEnvelopeSha256);

        return new StartDeliveryDeviceResult(
                sessionUid,
                commandUid,
                expiresAt,
                deploymentCode,
                command.portNo());
    }

    private long insertSession(
            LockedStartDeliveryBusinessFacts lockedBusiness,
            StartDeliveryDeviceCommand command,
            UUID sessionUid,
            long tenantId,
            long organizationId,
            long organizationUserId,
            long deploymentId,
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
                                    deploymentId,
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
            StartDeliveryDeviceRepository.DeploymentRow deployment,
            StartDeliveryDeviceRepository.AssetRow asset,
            long commandId,
            byte[] semanticEnvelopeSha256) {
        Map<String, Object> taskSnapshot = new LinkedHashMap<>();
        taskSnapshot.put("schemaVersion", 1);
        taskSnapshot.put("commandUid", commandUid.toString());
        taskSnapshot.put("commandType", TASK_TYPE);
        taskSnapshot.put("hardwareSn", asset.hardwareSn());
        taskSnapshot.put(
                "deploymentCode",
                deployment.publicCode());
        taskSnapshot.put(
                "target",
                Map.of(
                        "type",
                        TARGET_TYPE,
                        "uid",
                        sessionUid.toString()));
        taskSnapshot.put("payloadSchemaVersion", 1);
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
                                deployment.tenantId(),
                                deployment.organizationId(),
                                deployment.id(),
                                commandId),
                        1,
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
            String deploymentCode,
            UUID sessionUid,
            Instant issuedAt,
            Instant expiresAt,
            Map<String, Object> payload,
            byte[] payloadSha256) {
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schemaVersion", 1);
        envelope.put("commandUid", commandUid.toString());
        envelope.put("commandType", TASK_TYPE);
        envelope.put("deploymentCode", deploymentCode);
        envelope.put(
                "target",
                Map.of(
                        "type",
                        TARGET_TYPE,
                        "uid",
                        sessionUid.toString()));
        envelope.put("issuedAt", issuedAt.toString());
        envelope.put("expiresAt", expiresAt.toString());
        envelope.put("payloadSchemaVersion", 1);
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

    private static String normalizeDeploymentCode(String value) {
        String normalized = value.trim();
        if (!normalized.matches("Dp_[A-Za-z0-9_-]{6,61}")) {
            throw StartDeliveryDevicePolicy.deploymentUnavailable();
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
