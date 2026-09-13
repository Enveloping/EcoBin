package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.port.TrustedPlatformDeviceAssetFactPort;
import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedPlatformDeviceAssetFactEvent;
import org.enveloping.ecobin.device.application.firmware.McuFirmwareRolloutService;
import org.enveloping.ecobin.device.application.delivery.DeliveryRecoveryQuarantineService;
import org.enveloping.ecobin.device.application.delivery.NativeDeliveryIssueService;
import org.enveloping.ecobin.device.application.remote.RemoteSupportSessionService;
import org.enveloping.ecobin.device.application.software.DeviceSoftwareCompatibilityService;
import org.enveloping.ecobin.device.application.software.BusinessReleaseControlPlaneService;
import org.enveloping.ecobin.framework.reliability.UntrustedInboxSourceException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Set;

/**
 * 完成永久机构分配前产生的可靠设备资产事实。
 *
 * <p>规范化消息本身保留在平台收件箱；没有机构运行表或告警可写，因此这里只确认设备
 * 事实已被平台安全接收。机构分配及配置应用后，设备会自动发送新的机构运行快照。</p>
 */
@Service
public class TrustedPlatformDeviceAssetFactService
        implements TrustedPlatformDeviceAssetFactPort {

    private static final Set<String> SUPPORTED = Set.of(
            "DEVICE_FAULT_OBSERVED",
            "DEVICE_FAULT_RECOVERED",
            "SAFETY_SENSOR_STATE_CHANGED",
            "DEVICE_COMMAND_OBSERVED",
            "FACTORY_SEAL_COMPLETED",
            "REMOTE_SUPPORT_TUNNEL_STATUS",
            DeliveryRecoveryQuarantineService.EVENT_TYPE,
            NativeDeliveryIssueService.ARCHIVE_EVENT,
            NativeDeliveryIssueService.EVIDENCE_EVENT,
            DeviceSoftwareCompatibilityService.EVENT_TYPE,
            BusinessReleaseControlPlaneService.EVENT_TYPE,
            BusinessReleaseControlPlaneService.CANCEL_EVENT_TYPE,
            McuFirmwareRolloutService.EVENT_TYPE);
    private static final String UUID_V4 =
            "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}"
                    + "-[89ab][0-9a-f]{3}-[0-9a-f]{12}$";
    private static final String SHA256 = "^[0-9a-f]{64}$";

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final ReliablePlatformEdgeConfirmationService confirmationService;
    private final RemoteSupportSessionService remoteSupportSessions;
    private final McuFirmwareRolloutService firmwareRollouts;
    private final FactorySealAuthorizationService factorySealAuthorizations;
    private final DeviceSoftwareCompatibilityService softwareCompatibility;
    private final BusinessReleaseControlPlaneService businessReleases;
    private final DeliveryRecoveryQuarantineService
            deliveryRecoveryQuarantines;
    private final NativeDeliveryIssueService nativeDeliveryIssues;

    public TrustedPlatformDeviceAssetFactService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            ReliablePlatformEdgeConfirmationService confirmationService,
            RemoteSupportSessionService remoteSupportSessions,
            McuFirmwareRolloutService firmwareRollouts,
            FactorySealAuthorizationService factorySealAuthorizations,
            DeviceSoftwareCompatibilityService softwareCompatibility) {
        this(
                jdbc,
                objectMapper,
                confirmationService,
                remoteSupportSessions,
                firmwareRollouts,
                factorySealAuthorizations,
                softwareCompatibility,
                null,
                null);
    }

    public TrustedPlatformDeviceAssetFactService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            ReliablePlatformEdgeConfirmationService confirmationService,
            RemoteSupportSessionService remoteSupportSessions,
            McuFirmwareRolloutService firmwareRollouts,
            FactorySealAuthorizationService factorySealAuthorizations,
            DeviceSoftwareCompatibilityService softwareCompatibility,
            BusinessReleaseControlPlaneService businessReleases) {
        this(
                jdbc,
                objectMapper,
                confirmationService,
                remoteSupportSessions,
                firmwareRollouts,
                factorySealAuthorizations,
                softwareCompatibility,
                businessReleases,
                null);
    }

    public TrustedPlatformDeviceAssetFactService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            ReliablePlatformEdgeConfirmationService confirmationService,
            RemoteSupportSessionService remoteSupportSessions,
            McuFirmwareRolloutService firmwareRollouts,
            FactorySealAuthorizationService factorySealAuthorizations,
            DeviceSoftwareCompatibilityService softwareCompatibility,
            BusinessReleaseControlPlaneService businessReleases,
            DeliveryRecoveryQuarantineService
                    deliveryRecoveryQuarantines) {
        this(jdbc, objectMapper, confirmationService, remoteSupportSessions,
                firmwareRollouts, factorySealAuthorizations, softwareCompatibility,
                businessReleases, deliveryRecoveryQuarantines, null);
    }

    @Autowired
    public TrustedPlatformDeviceAssetFactService(JdbcTemplate jdbc, ObjectMapper objectMapper,
            ReliablePlatformEdgeConfirmationService confirmationService,
            RemoteSupportSessionService remoteSupportSessions, McuFirmwareRolloutService firmwareRollouts,
            FactorySealAuthorizationService factorySealAuthorizations,
            DeviceSoftwareCompatibilityService softwareCompatibility,
            BusinessReleaseControlPlaneService businessReleases,
            DeliveryRecoveryQuarantineService deliveryRecoveryQuarantines,
            NativeDeliveryIssueService nativeDeliveryIssues) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.confirmationService = confirmationService;
        this.remoteSupportSessions = remoteSupportSessions;
        this.firmwareRollouts = firmwareRollouts;
        this.factorySealAuthorizations = factorySealAuthorizations;
        this.softwareCompatibility = softwareCompatibility;
        this.businessReleases = businessReleases;
        this.deliveryRecoveryQuarantines =
                deliveryRecoveryQuarantines;
        this.nativeDeliveryIssues = nativeDeliveryIssues;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public TrustedDeviceEventApplyResult apply(
            TrustedPlatformDeviceAssetFactEvent inboxEvent) {
        if (!SUPPORTED.contains(inboxEvent.messageKind())) {
            throw new IllegalArgumentException(
                    "unsupported platform device asset fact");
        }
        if (McuFirmwareRolloutService.EVENT_TYPE.equals(
                inboxEvent.messageKind())) {
            return firmwareRollouts.applyProgress(inboxEvent);
        }
        if (BusinessReleaseControlPlaneService.EVENT_TYPE.equals(
                inboxEvent.messageKind())) {
            if (businessReleases == null) {
                throw new IllegalStateException(
                        "business runtime progress handler is unavailable");
            }
            return businessReleases.applyProgress(inboxEvent);
        }
        if (BusinessReleaseControlPlaneService.CANCEL_EVENT_TYPE.equals(
                inboxEvent.messageKind())) {
            if (businessReleases == null) {
                throw new IllegalStateException(
                        "business runtime cancellation handler is unavailable");
            }
            return businessReleases.applyCancellationResult(inboxEvent);
        }
        return inboxEvent.sourceInbox().use(sourceInboxId -> {
            JsonNode normalized = objectMapper.readTree(
                    inboxEvent.normalizedPayload());
            JsonNode source = requiredObject(normalized, "trustedSource");
            JsonNode event = requiredObject(normalized, "event");
            JsonNode target = requiredObject(event, "target");
            String hardwareSn = requiredText(source, "deviceName", 64);
            requireTextEquals(
                    event, "eventType", inboxEvent.messageKind());
            requireIntegerEquals(event, "schemaVersion", 2);
            String eventUid = requiredPattern(
                    event, "eventUid", UUID_V4);
            String payloadSha256 = requiredPattern(
                    event, "payloadSha256", SHA256);
            requiredPattern(
                    normalized, "eventCanonicalSha256", SHA256);

            LocalDateTime now = jdbc.queryForObject(
                    "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
            if (NativeDeliveryIssueService.ARCHIVE_EVENT.equals(inboxEvent.messageKind())
                    || NativeDeliveryIssueService.EVIDENCE_EVENT.equals(inboxEvent.messageKind())) {
                if (nativeDeliveryIssues == null) throw new IllegalStateException("native delivery issue handler is unavailable");
                NativeDeliveryIssueService.ApplyResult result = nativeDeliveryIssues.applyTrusted(sourceInboxId, normalized, now);
                confirmationService.ensureApplied(result.assetId(), hardwareSn, eventUid, payloadSha256,
                        result.changed() ? "UPDATED" : "NO_ACTION_REQUIRED", now);
                return result.changed() ? TrustedDeviceEventApplyResult.APPLIED : TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED;
            }
            if ("DEVICE_COMMAND_OBSERVED".equals(
                    inboxEvent.messageKind())) {
                FactorySealAuthorizationService.ObservationResult observed =
                        factorySealAuthorizations.applyTrustedObservation(
                                hardwareSn, event, now);
                confirmationService.ensureApplied(
                        observed.assetId(),
                        hardwareSn,
                        eventUid,
                        payloadSha256,
                        observed.changed()
                                ? "UPDATED" : "NO_ACTION_REQUIRED",
                        now);
                return observed.changed()
                        ? TrustedDeviceEventApplyResult.APPLIED
                        : TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED;
            }
            if ("FACTORY_SEAL_COMPLETED".equals(
                    inboxEvent.messageKind())) {
                FactorySealAuthorizationService.CompletionResult completed =
                        factorySealAuthorizations.applyTrustedCompletion(
                                hardwareSn, event, now);
                confirmationService.ensureApplied(
                        completed.assetId(),
                        hardwareSn,
                        eventUid,
                        payloadSha256,
                        completed.changed()
                                ? "UPDATED" : "NO_ACTION_REQUIRED",
                        now);
                return completed.changed()
                        ? TrustedDeviceEventApplyResult.APPLIED
                        : TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED;
            }
            if (DeliveryRecoveryQuarantineService.EVENT_TYPE.equals(
                    inboxEvent.messageKind())) {
                if (deliveryRecoveryQuarantines == null) {
                    throw new IllegalStateException(
                            "delivery recovery quarantine handler is unavailable");
                }
                DeliveryRecoveryQuarantineService.ApplyResult applied =
                        deliveryRecoveryQuarantines.applyTrusted(
                                sourceInboxId, normalized, now);
                confirmationService.ensureApplied(
                        applied.assetId(),
                        hardwareSn,
                        eventUid,
                        payloadSha256,
                        applied.changed()
                                ? "UPDATED" : "NO_ACTION_REQUIRED",
                        now);
                return applied.changed()
                        ? TrustedDeviceEventApplyResult.APPLIED
                        : TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED;
            }

            requireTextEquals(target, "type", "DEVICE_ASSET");
            requireTextEquals(target, "uid", hardwareSn);

            List<Long> assetIds = jdbc.query("""
                            SELECT id
                            FROM dev_device_asset
                            WHERE hardware_sn = ?
                            FOR UPDATE
                            """,
                    (rs, ignored) -> rs.getLong("id"),
                    hardwareSn);
            if (assetIds.size() != 1) {
                throw new UntrustedInboxSourceException(
                        "platform device fact target is not registered");
            }
            if (DeviceSoftwareCompatibilityService.EVENT_TYPE.equals(
                    inboxEvent.messageKind())) {
                DeviceSoftwareCompatibilityService.ApplyResult applied =
                        softwareCompatibility.apply(
                                sourceInboxId,
                                assetIds.getFirst(),
                                normalized,
                                event,
                                now);
                confirmationService.ensureApplied(
                        assetIds.getFirst(),
                        hardwareSn,
                        eventUid,
                        payloadSha256,
                        applied.changed()
                                ? "UPDATED" : "NO_ACTION_REQUIRED",
                        now);
                return applied.changed()
                        ? TrustedDeviceEventApplyResult.APPLIED
                        : TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED;
            }
            boolean remoteSupportChanged = false;
            if ("REMOTE_SUPPORT_TUNNEL_STATUS".equals(
                    inboxEvent.messageKind())) {
                remoteSupportChanged = remoteSupportSessions.applyStatus(
                        sourceInboxId, normalized);
            }
            confirmationService.ensureApplied(
                    assetIds.getFirst(),
                    hardwareSn,
                    eventUid,
                    payloadSha256,
                    "NO_ACTION_REQUIRED",
                    now);
            return remoteSupportChanged
                    ? TrustedDeviceEventApplyResult.APPLIED
                    : TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED;
        });
    }

    private static JsonNode requiredObject(JsonNode parent, String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isObject()) {
            throw new IllegalArgumentException(field + " must be an object");
        }
        return value;
    }

    private static String requiredText(
            JsonNode parent,
            String field,
            int maximumLength) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isTextual()
                || value.asText().isBlank()
                || value.asText().length() > maximumLength) {
            throw new IllegalArgumentException(
                    field + " must be bounded text");
        }
        return value.asText();
    }

    private static String requiredPattern(
            JsonNode parent,
            String field,
            String pattern) {
        String value = requiredText(parent, field, 160);
        if (!value.matches(pattern)) {
            throw new IllegalArgumentException(
                    field + " has an invalid stable format");
        }
        return value;
    }

    private static void requireTextEquals(
            JsonNode parent,
            String field,
            String expected) {
        if (!expected.equals(requiredText(parent, field, 160))) {
            throw new IllegalArgumentException(
                    field + " differs from the trusted target");
        }
    }

    private static void requireIntegerEquals(
            JsonNode parent,
            String field,
            int expected) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.canConvertToInt()
                || value.asInt() != expected) {
            throw new IllegalArgumentException(
                    field + " differs from the supported schema");
        }
    }
}
