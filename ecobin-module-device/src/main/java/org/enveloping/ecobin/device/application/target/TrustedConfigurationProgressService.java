package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskProofPort;
import org.enveloping.ecobin.framework.reliability.TrustedInboxQuarantinePort;
import org.enveloping.ecobin.framework.reliability.TrustedOrganizationInboxRefFactory;
import org.enveloping.ecobin.framework.reliability.UntrustedInboxSourceException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.HexFormat;
import java.util.List;
import java.util.UUID;

@Service
public class TrustedConfigurationProgressService
{

    private static final String MESSAGE_KIND = "CONFIGURATION_PROGRESS";
    private static final String TASK_TYPE =
            "ENSURE_DEVICE_CONFIGURATION";
    private static final String TARGET_TYPE =
            "CONFIGURATION_APPLICATION";
    private static final long SAFE_INTEGER_MAX =
            9_007_199_254_740_991L;
    private static final String UUID_V4 =
            "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}"
                    + "-[89ab][0-9a-f]{3}-[0-9a-f]{12}$";
    private static final String SHA256 = "^[0-9a-f]{64}$";

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final ReliableDeviceTaskProofPort taskProofPort;
    private final ReliableEdgeConfirmationService confirmationService;
    private final TrustedInboxQuarantinePort quarantinePort;
    private final TrustedOrganizationInboxRefFactory inboxRefFactory;
    private final AutomaticDeviceActivationService activationService;

    public TrustedConfigurationProgressService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            ReliableDeviceTaskProofPort taskProofPort,
            ReliableEdgeConfirmationService confirmationService,
            TrustedInboxQuarantinePort quarantinePort,
            TrustedOrganizationInboxRefFactory inboxRefFactory,
            AutomaticDeviceActivationService activationService) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.taskProofPort = taskProofPort;
        this.confirmationService = confirmationService;
        this.quarantinePort = quarantinePort;
        this.inboxRefFactory = inboxRefFactory;
        this.activationService = activationService;
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public TrustedDeviceEventApplyResult apply(
            TrustedDeviceInboxEvent inboxEvent) {
        if (!MESSAGE_KIND.equals(inboxEvent.messageKind())
                || inboxEvent.normalizedSchemaVersion() != 2) {
            throw new IllegalArgumentException(
                    "unsupported trusted device inbox message");
        }
        ConfigurationProgress event =
                parse(inboxEvent.normalizedPayload());
        return inboxEvent.sourceInbox().use(
                (inboxKey, tenantKey, organizationKey) ->
                        applyWithinScope(
                                event,
                                inboxKey,
                                tenantKey,
                                organizationKey));
    }

    private TrustedDeviceEventApplyResult applyWithinScope(
            ConfigurationProgress event,
            long inboxKey,
            long tenantKey,
            long organizationKey) {
        lockAsset(event.hardwareSn());
        ConfigurationTarget target = loadTarget(
                event,
                tenantKey,
                organizationKey);
        verifyTarget(event, target);
        List<ExistingEvent> collisions = jdbc.query("""
                        SELECT
                            event_uid,
                            asset_id,
                            edge_event_sequence,
                            LOWER(HEX(canonical_sha256)) AS canonical_sha256,
                            source_inbox_id
                        FROM dev_edge_event
                        WHERE event_uid = ?
                           OR (
                                asset_id = ?
                                AND edge_event_sequence = ?
                           )
                        """,
                (rs, ignored) -> new ExistingEvent(
                        rs.getString("event_uid"),
                        rs.getLong("asset_id"),
                        rs.getLong("edge_event_sequence"),
                        rs.getString("canonical_sha256"),
                        rs.getLong("source_inbox_id")),
                event.eventUid(),
                target.assetId(),
                event.edgeEventSequence());
        if (!collisions.isEmpty()) {
            if (collisions.size() == 1
                    && collisions.getFirst().matches(
                            event,
                            target.assetId(),
                            inboxKey)) {
                return TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED;
            }
            UUID quarantineUid =
                    quarantinePort.quarantineIdentityConflict(
                    inboxRefFactory.issue(
                            inboxKey,
                            tenantKey,
                            organizationKey),
                    "trusted configuration event identity or sequence conflicts");
            LocalDateTime now = jdbc.queryForObject(
                    "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
            confirmationService.registerQuarantined(
                    tenantKey,
                    organizationKey,
                    target.assetId(),
                    event.eventUid(),
                    event.payloadSha256(),
                    "EVENT_IDENTITY_CONFLICT",
                    quarantineUid,
                    now);
            return TrustedDeviceEventApplyResult.QUARANTINED;
        }

        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        long edgeEventId = insertEdgeEvent(
                event,
                target,
                inboxKey,
                tenantKey,
                organizationKey,
                now);
        mergeApplication(event, target, now);
        mergeCommand(event, target, now);
        mergeRuntime(event, target, now);
        if ("APPLIED".equals(event.stage())) {
            activationService.reconcileInCurrentTransaction(
                    target.assetId(),
                    UUID.fromString(event.eventUid()));
        }
        if ("APPLIED".equals(event.stage())
                || "FAILED".equals(event.stage())) {
            taskProofPort.completeFromTrustedProof(
                    TASK_TYPE,
                    TARGET_TYPE,
                    event.applicationUid());
        }
        confirmationService.registerApplied(
                tenantKey,
                organizationKey,
                target.assetId(),
                event.eventUid(),
                event.payloadSha256(),
                "UPDATED",
                now);
        return TrustedDeviceEventApplyResult.APPLIED;
    }

    private void lockAsset(String hardwareSn) {
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
                    "authenticated device asset no longer exists");
        }
    }

    private ConfigurationTarget loadTarget(
            ConfigurationProgress event,
            long tenantKey,
            long organizationKey) {
        lockMutableTargetRows(
                event,
                tenantKey,
                organizationKey);
        List<ConfigurationTarget> rows = jdbc.query("""
                        SELECT
                            asset.id AS asset_id,
                            application.id AS application_id,
                            application.status AS application_status,
                            version.version_no,
                            LOWER(HEX(version.content_sha256))
                                AS content_sha256,
                            LOWER(HEX(version.mcu_payload_sha256))
                                AS mcu_payload_sha256,
                            command_row.id AS command_id,
                            command_row.physical_state AS command_state
                        FROM dev_device_asset asset
                        JOIN dev_config_application application
                          ON application.asset_id = asset.id
                         AND application.tenant_id = asset.tenant_id
                         AND application.organization_id =
                             asset.organization_id
                        JOIN dev_config_version version
                          ON version.id = application.config_version_id
                         AND version.asset_id = asset.id
                         AND version.tenant_id = asset.tenant_id
                         AND version.organization_id =
                             asset.organization_id
                        JOIN dev_device_command command_row
                          ON command_row.config_application_id =
                             application.id
                         AND command_row.asset_id = asset.id
                         AND command_row.tenant_id = asset.tenant_id
                         AND command_row.organization_id =
                             asset.organization_id
                        WHERE asset.hardware_sn = ?
                          AND asset.tenant_id = ?
                          AND asset.organization_id = ?
                          AND application.application_uid = ?
                          AND command_row.command_uid = ?
                          AND command_row.command_type =
                              'APPLY_CONFIGURATION'
                        """,
                (rs, ignored) -> new ConfigurationTarget(
                        rs.getLong("asset_id"),
                        rs.getLong("application_id"),
                        rs.getString("application_status"),
                        rs.getLong("version_no"),
                        rs.getString("content_sha256"),
                        rs.getString("mcu_payload_sha256"),
                        rs.getLong("command_id"),
                        rs.getString("command_state")),
                event.hardwareSn(),
                tenantKey,
                organizationKey,
                event.applicationUid(),
                event.commandUid());
        if (rows.size() != 1) {
            throw new UntrustedInboxSourceException(
                    "configuration progress target is not authoritative");
        }
        return rows.getFirst();
    }

    private void lockMutableTargetRows(
            ConfigurationProgress event,
            long tenantKey,
            long organizationKey) {
        List<Long> applicationIds = jdbc.query("""
                        SELECT id
                        FROM dev_config_application
                        WHERE application_uid = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("id"),
                event.applicationUid(),
                tenantKey,
                organizationKey);
        List<Long> commandIds = jdbc.query("""
                        SELECT id
                        FROM dev_device_command
                        WHERE command_uid = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND command_type =
                              'APPLY_CONFIGURATION'
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("id"),
                event.commandUid(),
                tenantKey,
                organizationKey);
        if (applicationIds.size() != 1
                || commandIds.size() != 1) {
            throw new UntrustedInboxSourceException(
                    "configuration progress target is not authoritative");
        }
    }

    private static void verifyTarget(
            ConfigurationProgress event,
            ConfigurationTarget target) {
        if (event.version() != target.versionNo()
                || !event.contentSha256().equals(
                        target.contentSha256())
                || !event.mcuPayloadSha256().equals(
                        target.mcuPayloadSha256())) {
            throw new UntrustedInboxSourceException(
                    "configuration progress snapshot does not match intent");
        }
    }

    private long insertEdgeEvent(
            ConfigurationProgress event,
            ConfigurationTarget target,
            long inboxKey,
            long tenantKey,
            long organizationKey,
            LocalDateTime now) {
        int inserted = jdbc.update("""
                        INSERT INTO dev_edge_event (
                            event_uid, tenant_id, organization_id,
                            asset_id, edge_event_sequence,
                            event_type, delivery_class, schema_version,
                            target_type, target_stable_key_sha256,
                            device_occurred_at, clock_quality,
                            backend_received_at, payload_sha256,
                            canonical_sha256, source_inbox_id, created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?,
                            'CONFIGURATION_PROGRESS', 'RELIABLE_FACT', 2,
                            'CONFIGURATION_APPLICATION', ?,
                            ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                event.eventUid(),
                tenantKey,
                organizationKey,
                target.assetId(),
                event.edgeEventSequence(),
                sha256(event.applicationUid()),
                event.deviceOccurredAt(),
                event.clockQuality(),
                now,
                HexFormat.of().parseHex(event.payloadSha256()),
                HexFormat.of().parseHex(event.canonicalSha256()),
                inboxKey,
                now);
        requireSingle(inserted, "insert configuration edge event");
        Long id = jdbc.queryForObject("""
                        SELECT id
                        FROM dev_edge_event
                        WHERE event_uid = ?
                        """,
                Long.class,
                event.eventUid());
        if (id == null) {
            throw new IllegalStateException(
                    "configuration edge event id is missing");
        }
        return id;
    }

    private void mergeApplication(
            ConfigurationProgress event,
            ConfigurationTarget target,
            LocalDateTime now) {
        if ("APPLIED".equals(event.stage())) {
            updateApplication("""
                            status = 'APPLIED',
                            reported_version_no = ?,
                            reported_content_sha256 = ?,
                            reported_mcu_payload_sha256 = ?,
                            edge_persisted_at =
                                COALESCE(edge_persisted_at, ?),
                            mcu_synced_at =
                                COALESCE(mcu_synced_at, ?),
                            applied_at = COALESCE(applied_at, ?)
                            """,
                    target.applicationId(),
                    now,
                    event.version(),
                    HexFormat.of().parseHex(event.contentSha256()),
                    HexFormat.of().parseHex(event.mcuPayloadSha256()),
                    now,
                    now,
                    now);
            return;
        }
        if ("EDGE_SAVED".equals(event.stage())) {
            if ("APPLIED".equals(target.applicationStatus())) {
                return;
            }
            updateApplication("""
                            status = 'EDGE_SAVED',
                            reported_version_no = ?,
                            reported_content_sha256 = ?,
                            reported_mcu_payload_sha256 = ?,
                            edge_persisted_at =
                                COALESCE(edge_persisted_at, ?),
                            mcu_synced_at = NULL,
                            applied_at = NULL
                            """,
                    target.applicationId(),
                    now,
                    event.version(),
                    HexFormat.of().parseHex(event.contentSha256()),
                    HexFormat.of().parseHex(event.mcuPayloadSha256()),
                    now);
            return;
        }
        if ("APPLIED".equals(target.applicationStatus())) {
            updateApplication("""
                            last_failure_at = ?,
                            last_failure_code = ?
                            """,
                    target.applicationId(),
                    now,
                    now,
                    event.errorCode());
            return;
        }
        updateApplication("""
                        status = 'FAILED',
                        reported_version_no = ?,
                        reported_content_sha256 = ?,
                        reported_mcu_payload_sha256 = ?,
                        mcu_synced_at = NULL,
                        applied_at = NULL,
                        last_failure_at = ?,
                        last_failure_code = ?
                        """,
                target.applicationId(),
                now,
                event.version(),
                HexFormat.of().parseHex(event.contentSha256()),
                HexFormat.of().parseHex(event.mcuPayloadSha256()),
                now,
                event.errorCode());
    }

    private void updateApplication(
            String assignments,
            long applicationId,
            LocalDateTime updatedAt,
            Object... values) {
        Object[] arguments = new Object[values.length + 2];
        System.arraycopy(values, 0, arguments, 0, values.length);
        arguments[values.length] = updatedAt;
        arguments[values.length + 1] = applicationId;
        String sql = "UPDATE dev_config_application SET "
                + assignments
                + ", lock_version = lock_version + 1, "
                + "updated_at = ? WHERE id = ?";
        int updated = jdbc.update(sql, arguments);
        requireSingle(updated, "merge configuration application");
    }

    private void mergeCommand(
            ConfigurationProgress event,
            ConfigurationTarget target,
            LocalDateTime now) {
        if ("EDGE_SAVED".equals(event.stage())
                && "QUEUED".equals(target.commandState())) {
            requireSingle(jdbc.update("""
                            UPDATE dev_device_command
                            SET physical_state = 'EDGE_ACCEPTED',
                                edge_accepted_at = ?,
                                lock_version = lock_version + 1,
                                updated_at = ?
                            WHERE id = ?
                            """,
                    now,
                    now,
                    target.commandId()),
                    "merge configuration edge acceptance");
            return;
        }
        if ("APPLIED".equals(event.stage())
                && !"PHYSICAL_SUCCEEDED".equals(
                        target.commandState())) {
            requireSingle(jdbc.update("""
                            UPDATE dev_device_command
                            SET physical_state = 'PHYSICAL_SUCCEEDED',
                                edge_accepted_at =
                                    COALESCE(edge_accepted_at, ?),
                                physical_started_at =
                                    COALESCE(physical_started_at, ?),
                                physical_ended_at = ?,
                                lock_version = lock_version + 1,
                                updated_at = ?
                            WHERE id = ?
                            """,
                    now,
                    now,
                    now,
                    now,
                    target.commandId()),
                    "merge configuration command completion");
        }
    }

    private void mergeRuntime(
            ConfigurationProgress event,
            ConfigurationTarget target,
            LocalDateTime now) {
        if ("APPLIED".equals(event.stage())) {
            int updated = jdbc.update("""
                            UPDATE dev_device_runtime_state
                            SET applied_config_version_no = ?,
                                applied_config_content_sha256 = ?,
                                applied_mcu_payload_sha256 = ?,
                                last_device_event_at = ?,
                                lock_version = lock_version + 1,
                                updated_at = ?
                            WHERE asset_id = ?
                              AND (
                                  applied_config_version_no IS NULL
                                  OR applied_config_version_no <= ?
                              )
                            """,
                    event.version(),
                    HexFormat.of().parseHex(event.contentSha256()),
                    HexFormat.of().parseHex(event.mcuPayloadSha256()),
                    now,
                    now,
                    target.assetId(),
                    event.version());
            if (updated == 1) {
                return;
            }
        }
        requireSingle(jdbc.update("""
                        UPDATE dev_device_runtime_state
                        SET last_device_event_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE asset_id = ?
                        """,
                now,
                now,
                target.assetId()),
                "merge device event runtime timestamp");
    }

    private ConfigurationProgress parse(String normalizedPayload) {
        JsonNode root = objectMapper.readTree(normalizedPayload);
        JsonNode source = requiredObject(root, "trustedSource");
        JsonNode event = requiredObject(root, "event");
        JsonNode target = requiredObject(event, "target");
        JsonNode payload = requiredObject(event, "payload");

        String hardwareSn = requiredText(
                source, "deviceName", 64);
        requiredText(source, "productId", 128);
        int schemaVersion = Math.toIntExact(
                requiredPositiveLong(event, "schemaVersion"));
        if (schemaVersion != 2
                || !"CONFIGURATION_PROGRESS".equals(
                        requiredText(event, "eventType", 48))
                || !"RELIABLE_FACT".equals(
                        requiredText(event, "deliveryClass", 24))
                || !"CONFIGURATION_APPLICATION".equals(
                        requiredText(target, "type", 40))) {
            throw new IllegalArgumentException(
                    "configuration progress envelope constants are invalid");
        }

        String eventUid = requiredPattern(
                event, "eventUid", UUID_V4);
        String applicationUid = requiredPattern(
                payload, "applicationUid", UUID_V4);
        String targetUid = requiredPattern(
                target, "uid", UUID_V4);
        if (!applicationUid.equals(targetUid)) {
            throw new IllegalArgumentException(
                    "configuration target and payload differ");
        }
        String commandUid = requiredPattern(
                event, "commandUid", UUID_V4);
        long sequence = requiredPositiveLong(
                event, "edgeEventSequence");
        String clockQuality = requiredText(
                event, "clockQuality", 16);
        LocalDateTime occurredAt =
                parseOccurredAt(event.get("occurredAt"), clockQuality);
        String stage = requiredText(payload, "stage", 16);
        String mcuCommandUid = nullablePattern(
                payload, "mcuCommandUid", UUID_V4);
        String errorCode = nullablePattern(
                payload,
                "errorCode",
                "^[A-Z][A-Z0-9_]{0,63}$");
        validateStage(stage, mcuCommandUid, errorCode);
        return new ConfigurationProgress(
                hardwareSn,
                eventUid,
                sequence,
                commandUid,
                occurredAt,
                clockQuality,
                requiredPattern(event, "payloadSha256", SHA256),
                requiredPattern(root, "eventCanonicalSha256", SHA256),
                applicationUid,
                stage,
                requiredPositiveLong(payload, "version"),
                requiredPattern(payload, "contentSha256", SHA256),
                requiredPattern(payload, "mcuPayloadSha256", SHA256),
                mcuCommandUid,
                errorCode);
    }

    private static void validateStage(
            String stage,
            String mcuCommandUid,
            String errorCode) {
        boolean valid = switch (stage) {
            case "EDGE_SAVED" ->
                    mcuCommandUid == null && errorCode == null;
            case "APPLIED" ->
                    mcuCommandUid != null && errorCode == null;
            case "FAILED" -> errorCode != null;
            default -> false;
        };
        if (!valid) {
            throw new IllegalArgumentException(
                    "configuration progress stage shape is invalid");
        }
    }

    private static LocalDateTime parseOccurredAt(
            JsonNode node,
            String clockQuality) {
        if ("SYNCED".equals(clockQuality)) {
            if (node == null || !node.isTextual()
                    || !node.asText().matches(
                    "^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
                            + "[0-9]{2}:[0-9]{2}:[0-9]{2}"
                            + "(?:\\.[0-9]{1,9})?Z$")) {
                throw new IllegalArgumentException(
                        "synced event requires a UTC occurredAt");
            }
            return LocalDateTime.ofInstant(
                    Instant.parse(node.asText()), ZoneOffset.UTC);
        }
        if (!"ESTIMATED".equals(clockQuality)
                && !"UNAVAILABLE".equals(clockQuality)) {
            throw new IllegalArgumentException(
                    "clock quality is invalid");
        }
        if (node != null && !node.isNull()) {
            throw new IllegalArgumentException(
                    "unsynced event must not carry occurredAt");
        }
        return null;
    }

    private static JsonNode requiredObject(
            JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        if (value == null || !value.isObject()) {
            throw new IllegalArgumentException(
                    field + " must be an object");
        }
        return value;
    }

    private static String requiredText(
            JsonNode node, String field, int maximumLength) {
        JsonNode value = node.get(field);
        if (value == null || !value.isTextual()
                || value.asText().isBlank()
                || value.asText().length() > maximumLength) {
            throw new IllegalArgumentException(
                    field + " must be bounded text");
        }
        return value.asText();
    }

    private static String requiredPattern(
            JsonNode node, String field, String pattern) {
        String value = requiredText(node, field, 160);
        if (!value.matches(pattern)) {
            throw new IllegalArgumentException(
                    field + " has an invalid stable format");
        }
        return value;
    }

    private static String nullablePattern(
            JsonNode node, String field, String pattern) {
        JsonNode value = node.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        if (!value.isTextual() || !value.asText().matches(pattern)) {
            throw new IllegalArgumentException(
                    field + " has an invalid nullable format");
        }
        return value.asText();
    }

    private static long requiredPositiveLong(
            JsonNode node, String field) {
        JsonNode value = node.get(field);
        if (value == null || !value.isIntegralNumber()) {
            throw new IllegalArgumentException(
                    field + " must be an integer");
        }
        long result = value.longValue();
        if (result <= 0 || result > SAFE_INTEGER_MAX) {
            throw new IllegalArgumentException(
                    field + " is outside the safe positive range");
        }
        return result;
    }

    private static byte[] sha256(String value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(
                    value.getBytes(StandardCharsets.UTF_8));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable", exception);
        }
    }

    private static void requireSingle(int updated, String operation) {
        if (updated != 1) {
            throw new IllegalStateException(
                    operation + " updated " + updated + " rows");
        }
    }

    private record ConfigurationProgress(
            String hardwareSn,
            String eventUid,
            long edgeEventSequence,
            String commandUid,
            LocalDateTime deviceOccurredAt,
            String clockQuality,
            String payloadSha256,
            String canonicalSha256,
            String applicationUid,
            String stage,
            long version,
            String contentSha256,
            String mcuPayloadSha256,
            String mcuCommandUid,
            String errorCode) {
    }

    private record ConfigurationTarget(
            long assetId,
            long applicationId,
            String applicationStatus,
            long versionNo,
            String contentSha256,
            String mcuPayloadSha256,
            long commandId,
            String commandState) {
    }

    private record ExistingEvent(
            String eventUid,
            long assetId,
            long sequence,
            String canonicalSha256,
            long sourceInboxId) {

        private boolean matches(
                ConfigurationProgress event,
                long expectedAssetId,
                long expectedInboxId) {
            return eventUid.equals(event.eventUid())
                    && assetId == expectedAssetId
                    && sequence == event.edgeEventSequence()
                    && canonicalSha256.equals(event.canonicalSha256())
                    && sourceInboxId == expectedInboxId;
        }
    }
}
