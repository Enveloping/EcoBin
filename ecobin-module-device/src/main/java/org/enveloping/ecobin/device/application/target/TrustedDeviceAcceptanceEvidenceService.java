package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.port.TrustedDeviceAcceptanceEvidencePort;
import org.enveloping.ecobin.device.api.port.TrustedDeviceAcceptanceChallengePort;
import org.enveloping.ecobin.device.api.result.DeviceAcceptanceEvidenceApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceAcceptanceEvent;
import org.enveloping.ecobin.framework.reliability.UntrustedInboxSourceException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.security.MessageDigest;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;

/**
 * 用可信 OneNet 身份和设备自检证据自动计算机器验收结果。
 *
 * <p>本服务没有“人工通过”分支。已经通过的历史验收不会被后来一次运行故障抹掉；
 * 后续故障由实时准入条件阻止新业务。</p>
 */
@Service
public class TrustedDeviceAcceptanceEvidenceService
        implements TrustedDeviceAcceptanceEvidencePort {

    private static final String SHA256 = "^[0-9a-f]{64}$";
    private static final String UUID_V4 =
            "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}"
                    + "-[89ab][0-9a-f]{3}-[0-9a-f]{12}$";
    private static final String ZERO_SHA256 = "0".repeat(64);

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final DeviceEntryUrlFactory deviceEntryUrlFactory;
    private final TrustedDeviceAcceptanceChallengePort challengePort;
    private final ReliablePlatformEdgeConfirmationService confirmationService;
    private final FactorySealAuthorizationService factorySealAuthorizations;
    private final Set<String> supportedSoftwareVersions;
    private final Duration maximumEvidenceAge;

    public TrustedDeviceAcceptanceEvidenceService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            DeviceEntryUrlFactory deviceEntryUrlFactory,
            TrustedDeviceAcceptanceChallengePort challengePort,
            ReliablePlatformEdgeConfirmationService confirmationService,
            FactorySealAuthorizationService factorySealAuthorizations,
            @Value("${ecobin.device.acceptance.supported-edge-software-versions:0.1.0}")
            String supportedSoftwareVersions,
            @Value("${ecobin.device.acceptance.maximum-evidence-age:PT10M}")
            Duration maximumEvidenceAge) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.deviceEntryUrlFactory = deviceEntryUrlFactory;
        this.challengePort = challengePort;
        this.confirmationService = confirmationService;
        this.factorySealAuthorizations = factorySealAuthorizations;
        this.supportedSoftwareVersions = parseVersions(
                supportedSoftwareVersions);
        if (maximumEvidenceAge == null
                || maximumEvidenceAge.isNegative()
                || maximumEvidenceAge.isZero()) {
            throw new IllegalArgumentException(
                    "maximum acceptance evidence age must be positive");
        }
        this.maximumEvidenceAge = maximumEvidenceAge;
    }

    @Override
    @Transactional(isolation = Isolation.READ_COMMITTED)
    public DeviceAcceptanceEvidenceApplyResult apply(
            TrustedDeviceAcceptanceEvent acceptanceEvent) {
        return acceptanceEvent.sourceInbox().use(inboxId -> {
            JsonNode normalized = objectMapper.readTree(
                    acceptanceEvent.normalizedPayload());
            JsonNode source = requiredObject(normalized, "trustedSource");
            JsonNode event = requiredObject(normalized, "event");
            JsonNode target = requiredObject(event, "target");
            JsonNode payload = requiredObject(event, "payload");
            String hardwareSn = requiredText(source, "deviceName", 64);
            requireTextEquals(event, "eventType", "DEVICE_ACCEPTANCE_EVIDENCE");
            requireIntegerEquals(event, "schemaVersion", 2);
            requireTextEquals(target, "type", "DEVICE_ASSET");
            requireTextEquals(target, "uid", hardwareSn);
            requireIntegerEquals(payload, "evidenceSchemaVersion", 3);
            UUID commandUid = UUID.fromString(
                    requiredPattern(event, "commandUid", UUID_V4));
            UUID challengeUid = UUID.fromString(
                    requiredPattern(payload, "challengeUid", UUID_V4));
            Evidence facts = evidence(payload);

            AssetState asset = lockAsset(hardwareSn);
            LocalDateTime receivedAt = databaseNow();
            LocalDateTime observedAt = timestamp(event, "occurredAt");
            if (observedAt.isAfter(receivedAt)) {
                throw new IllegalArgumentException(
                        "acceptance evidence occurredAt is in the future");
            }

            String eventUid = requiredText(event, "eventUid", 36);
            String payloadSha256 = requiredPattern(
                    event, "payloadSha256", SHA256);
            byte[] evidenceSha256 = HexFormat.of().parseHex(payloadSha256);
            ExistingEvidence existing = existingEvidence(
                    asset.id(), eventUid, evidenceSha256);
            if (existing != null) {
                confirmationService.ensureApplied(
                        asset.id(),
                        hardwareSn,
                        eventUid,
                        payloadSha256,
                        "NO_ACTION_REQUIRED",
                        receivedAt);
                return new DeviceAcceptanceEvidenceApplyResult(
                        asset.id(), asset.acceptanceStatus(), false);
            }

            if (facts.factoryBagRevision() < asset.factoryBagRevision()) {
                confirmationService.ensureApplied(
                        asset.id(),
                        hardwareSn,
                        eventUid,
                        payloadSha256,
                        "NO_ACTION_REQUIRED",
                        receivedAt);
                return new DeviceAcceptanceEvidenceApplyResult(
                        asset.id(), asset.acceptanceStatus(), false);
            }

            if (!matchesFactoryBagGeneration(asset, facts)) {
                throw new IllegalArgumentException(
                        "acceptance evidence uses a stale factory bag generation");
            }

            challengePort.consume(
                    asset.id(), commandUid, challengeUid,
                    facts.factoryBagRevision(),
                    asset.factoryBagSetSha256(),
                    receivedAt);

            List<String> failures = failures(
                    asset,
                    facts,
                    observedAt,
                    receivedAt);
            String evaluationStatus = failures.isEmpty()
                    ? "PASSED" : "FAILED";
            String failureJson = objectMapper.writeValueAsString(failures);
            String evidenceJson = objectMapper.writeValueAsString(payload);

            int inserted = jdbc.update("""
                            INSERT INTO dev_device_acceptance_evidence (
                                evidence_uid, asset_id,
                                challenge_uid, command_uid,
                                factory_bag_revision,
                                factory_bag_set_sha256,
                                evidence_schema_version,
                                edge_store_instance_uid,
                                edge_software_version,
                                edge_protocol_version,
                                mcu_firmware_version,
                                onenet_online,
                                persistent_store_healthy,
                                trusted_time_healthy,
                                configuration_persistence_healthy,
                                mcu_communication_healthy,
                                sensors_healthy,
                                cameras_capture_healthy,
                                camera_upload_healthy,
                                device_entry_url_stored,
                                device_entry_url_sha256,
                                mcu_simulated, cameras_simulated,
                                evaluation_status,
                                failure_reasons_json, evidence_json,
                                evidence_sha256,
                                observed_at, received_at, created_at
                            ) VALUES (
                                ?, ?, ?, ?, ?, ?, 3, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                ?, CAST(? AS JSON), CAST(? AS JSON), ?, ?, ?, ?
                            )
                            """,
                    eventUid,
                    asset.id(),
                    challengeUid.toString(),
                    commandUid.toString(),
                    facts.factoryBagRevision(),
                    HexFormat.of().parseHex(
                            facts.factoryBagSetSha256()),
                    facts.edgeStoreInstanceUid(),
                    facts.edgeSoftwareVersion(),
                    facts.edgeProtocolVersion(),
                    facts.mcuFirmwareVersion(),
                    asset.oneNetOnline(),
                    facts.persistentStoreHealthy(),
                    facts.trustedTimeHealthy(),
                    facts.configurationPersistenceHealthy(),
                    facts.mcuCommunicationHealthy(),
                    facts.sensorsHealthy(),
                    facts.camerasCaptureHealthy(),
                    facts.cameraUploadHealthy(),
                    facts.deviceEntryUrlStored(),
                    HexFormat.of().parseHex(
                            facts.deviceEntryUrlSha256()),
                    facts.mcuSimulated(),
                    facts.camerasSimulated(),
                    evaluationStatus,
                    failureJson,
                    evidenceJson,
                    evidenceSha256,
                    observedAt,
                    receivedAt,
                    receivedAt);
            requireSingle(inserted, "insert acceptance evidence");

            boolean newlyPassed = "PASSED".equals(evaluationStatus)
                    && !"PASSED".equals(asset.acceptanceStatus());
            if (newlyPassed) {
                factorySealAuthorizations.requireAcceptanceSnapshotMutable(
                        asset.id());
                requireSingle(jdbc.update("""
                                UPDATE dev_device_asset
                                SET acceptance_status = 'PASSED',
                                    acceptance_generation =
                                        acceptance_generation + 1,
                                    accepted_at = COALESCE(accepted_at, ?),
                                    acceptance_evidence_sha256 = ?,
                                    last_acceptance_evaluated_at = ?,
                                    acceptance_failure_json = NULL,
                                    control_version = control_version + 1,
                                    updated_at = ?
                                WHERE id = ?
                                """,
                        receivedAt,
                        evidenceSha256,
                        receivedAt,
                        receivedAt,
                        asset.id()), "pass device acceptance");
                factorySealAuthorizations.ensureForAcceptedAsset(asset.id());
            } else if ("PASSED".equals(evaluationStatus)) {
                requireSingle(jdbc.update("""
                                UPDATE dev_device_asset
                                SET last_acceptance_evaluated_at = ?,
                                    updated_at = ?
                                WHERE id = ?
                                """,
                        receivedAt,
                        receivedAt,
                        asset.id()), "review accepted device evidence");
            } else if (!"PASSED".equals(asset.acceptanceStatus())) {
                requireSingle(jdbc.update("""
                                UPDATE dev_device_asset
                                SET acceptance_status = 'FAILED',
                                    accepted_at = NULL,
                                    acceptance_evidence_sha256 = ?,
                                    last_acceptance_evaluated_at = ?,
                                    acceptance_failure_json = CAST(? AS JSON),
                                    control_version = control_version + 1,
                                    updated_at = ?
                                WHERE id = ?
                                """,
                        evidenceSha256,
                        receivedAt,
                        failureJson,
                        receivedAt,
                        asset.id()), "fail device acceptance");
            } else {
                requireSingle(jdbc.update("""
                                UPDATE dev_device_asset
                                SET last_acceptance_evaluated_at = ?,
                                    updated_at = ?
                                WHERE id = ?
                                """,
                        receivedAt,
                        receivedAt,
                        asset.id()), "review accepted device evidence");
            }
            confirmationService.ensureApplied(
                    asset.id(),
                    hardwareSn,
                    eventUid,
                    payloadSha256,
                    "UPDATED",
                    receivedAt);
            return new DeviceAcceptanceEvidenceApplyResult(
                    asset.id(),
                    "PASSED".equals(asset.acceptanceStatus())
                            ? "PASSED" : evaluationStatus,
                    true);
        });
    }

    private AssetState lockAsset(String hardwareSn) {
        List<AssetState> rows = jdbc.query("""
                        SELECT asset.id, asset.device_public_code,
                               asset.expected_port_count,
                               asset.factory_bag_revision,
                               asset.factory_bag_set_sha256,
                               asset.acceptance_status,
                               asset.acceptance_generation,
                               transport.onenet_connection_status,
                               (
                                   SELECT COUNT(*)
                                   FROM dev_factory_installed_bag bag
                                   WHERE bag.asset_id = asset.id
                                     AND (
                                         bag.installation_source =
                                             'LEGACY_GRANDFATHERED'
                                         OR (
                                             bag.installation_source =
                                                 'FACTORY_MINIAPP'
                                             AND bag.label_item_id IS NOT NULL
                                             AND bag.installed_by_factory_operator_id
                                                 IS NOT NULL
                                             AND EXISTS (
                                                 SELECT 1
                                                 FROM rec_bag_label_claim claim
                                                 WHERE claim.label_item_id =
                                                       bag.label_item_id
                                                   AND claim.asset_id =
                                                       bag.asset_id
                                                   AND claim.port_no =
                                                       bag.port_no
                                                   AND claim.released_at IS NULL
                                             )
                                         )
                                     )
                               ) AS installed_bag_count
                        FROM dev_device_asset asset
                        JOIN dev_device_transport_state transport
                          ON transport.asset_id = asset.id
                        WHERE asset.hardware_sn = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new AssetState(
                        rs.getLong("id"),
                        rs.getString("device_public_code"),
                        rs.getInt("expected_port_count"),
                        rs.getLong("factory_bag_revision"),
                        rs.getBytes("factory_bag_set_sha256"),
                        rs.getString("acceptance_status"),
                        rs.getLong("acceptance_generation"),
                        "ONLINE".equals(rs.getString(
                                "onenet_connection_status")),
                        rs.getInt("installed_bag_count")
                                == rs.getInt("expected_port_count")),
                hardwareSn);
        if (rows.size() != 1) {
            throw new UntrustedInboxSourceException(
                    "authenticated acceptance device is not registered");
        }
        return rows.getFirst();
    }

    private ExistingEvidence existingEvidence(
            long assetId,
            String evidenceUid,
            byte[] digest) {
        return jdbc.query("""
                        SELECT evidence_uid, evidence_sha256
                        FROM dev_device_acceptance_evidence
                        WHERE evidence_uid = ?
                           OR (asset_id = ? AND evidence_sha256 = ?)
                        LIMIT 1
                        """,
                (rs, ignored) -> new ExistingEvidence(
                        rs.getString("evidence_uid")),
                evidenceUid,
                assetId,
                digest).stream().findFirst().orElse(null);
    }

    private Evidence evidence(JsonNode payload) {
        return new Evidence(
                requiredPattern(payload, "challengeUid", UUID_V4),
                requiredLong(payload, "factoryBagRevision", 0),
                requiredPattern(payload, "factoryBagSetSha256", SHA256),
                requiredText(payload, "edgeSoftwareVersion", 64),
                requiredText(payload, "edgeProtocolVersion", 32),
                requiredPattern(payload, "edgeStoreInstanceUid", UUID_V4),
                requiredText(payload, "mcuFirmwareVersion", 64),
                requiredBoolean(payload, "persistentStoreHealthy"),
                requiredBoolean(payload, "trustedTimeHealthy"),
                requiredBoolean(payload, "configurationPersistenceHealthy"),
                requiredBoolean(payload, "mcuCommunicationHealthy"),
                requiredBoolean(payload, "sensorsHealthy"),
                requiredBoolean(payload, "camerasCaptureHealthy"),
                requiredBoolean(payload, "cameraUploadHealthy"),
                requiredBoolean(payload, "deviceEntryUrlStored"),
                requiredPattern(payload, "deviceEntryUrlSha256", SHA256),
                requiredBoolean(payload, "mcuSimulated"),
                requiredBoolean(payload, "camerasSimulated"),
                requiredInteger(payload, "verifiedPortCount", 1, 6),
                requiredInteger(payload, "verifiedCameraCount", 0, 16),
                requiredPattern(payload, "sensorSampleSha256", SHA256),
                requiredPattern(payload, "cameraCaptureSha256", SHA256),
                requiredPattern(payload, "cameraUploadSha256", SHA256));
    }

    static boolean matchesFactoryBagGeneration(
            AssetState asset,
            Evidence evidence) {
        return asset.factoryBagSetSha256() != null
                && evidence.factoryBagRevision()
                    == asset.factoryBagRevision()
                && MessageDigest.isEqual(
                        HexFormat.of().parseHex(
                                evidence.factoryBagSetSha256()),
                        asset.factoryBagSetSha256());
    }

    List<String> failures(
            AssetState asset,
            Evidence evidence,
            LocalDateTime observedAt,
            LocalDateTime receivedAt) {
        List<String> result = new ArrayList<>();
        addUnless(result, asset.oneNetOnline(), "ONENET_NOT_ONLINE");
        addUnless(result, asset.factoryBagsComplete(),
                "FACTORY_BAGS_INCOMPLETE");
        addUnless(result,
                Duration.between(
                        observedAt.toInstant(ZoneOffset.UTC),
                        receivedAt.toInstant(ZoneOffset.UTC))
                        .compareTo(maximumEvidenceAge) <= 0,
                "EVIDENCE_STALE");
        addUnless(result,
                supportedSoftwareVersions.contains(
                        evidence.edgeSoftwareVersion()),
                "UNSUPPORTED_EDGE_SOFTWARE");
        addUnless(result,
                "2".equals(evidence.edgeProtocolVersion()),
                "UNSUPPORTED_EDGE_PROTOCOL");
        addUnless(result, evidence.persistentStoreHealthy(),
                "PERSISTENT_STORE_UNHEALTHY");
        addUnless(result, evidence.trustedTimeHealthy(),
                "TRUSTED_TIME_UNHEALTHY");
        addUnless(result, evidence.configurationPersistenceHealthy(),
                "CONFIGURATION_PERSISTENCE_UNHEALTHY");
        addUnless(result, evidence.mcuCommunicationHealthy(),
                "MCU_COMMUNICATION_UNHEALTHY");
        addUnless(result, evidence.sensorsHealthy(),
                "SENSOR_SELF_TEST_FAILED");
        addUnless(result, evidence.camerasCaptureHealthy(),
                "CAMERA_CAPTURE_FAILED");
        addUnless(result, evidence.cameraUploadHealthy(),
                "CAMERA_UPLOAD_READBACK_FAILED");
        DeviceEntryUrlFactory.Entry expectedEntryUrl =
                deviceEntryUrlFactory.create(asset.devicePublicCode());
        addUnless(result, evidence.deviceEntryUrlStored(),
                "DEVICE_ENTRY_URL_NOT_STORED");
        addUnless(result,
                expectedEntryUrl.sha256Hex().equals(
                        evidence.deviceEntryUrlSha256()),
                "DEVICE_ENTRY_URL_SHA256_MISMATCH");
        addUnless(result,
                evidence.verifiedPortCount() == asset.expectedPortCount(),
                "PORT_COUNT_MISMATCH");
        addUnless(result,
                evidence.verifiedCameraCount() >= 2,
                "CAMERA_COUNT_INSUFFICIENT");
        addUnless(result,
                !ZERO_SHA256.equals(evidence.sensorSampleSha256()),
                "SENSOR_EVIDENCE_DIGEST_EMPTY");
        addUnless(result,
                !ZERO_SHA256.equals(evidence.cameraCaptureSha256()),
                "CAMERA_CAPTURE_DIGEST_EMPTY");
        addUnless(result,
                !ZERO_SHA256.equals(evidence.cameraUploadSha256()),
                "CAMERA_UPLOAD_DIGEST_EMPTY");
        return List.copyOf(result);
    }

    private static void addUnless(
            List<String> failures,
            boolean condition,
            String code) {
        if (!condition) {
            failures.add(code);
        }
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
    }

    private static Set<String> parseVersions(String value) {
        LinkedHashSet<String> result = new LinkedHashSet<>();
        Arrays.stream(value == null ? new String[0] : value.split(","))
                .map(String::trim)
                .filter(item -> !item.isEmpty())
                .forEach(result::add);
        if (result.isEmpty()) {
            throw new IllegalArgumentException(
                    "at least one edge software version must be supported");
        }
        return Set.copyOf(result);
    }

    private static JsonNode requiredObject(JsonNode parent, String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isObject()) {
            throw new IllegalArgumentException(field + " must be an object");
        }
        return value;
    }

    private static String requiredText(
            JsonNode parent, String field, int maximumLength) {
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
            JsonNode parent, String field, String pattern) {
        String value = requiredText(parent, field, 128);
        if (!value.matches(pattern)) {
            throw new IllegalArgumentException(
                    field + " has an invalid format");
        }
        return value;
    }

    private static boolean requiredBoolean(JsonNode parent, String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isBoolean()) {
            throw new IllegalArgumentException(field + " must be boolean");
        }
        return value.booleanValue();
    }

    private static int requiredInteger(
            JsonNode parent, String field, int minimum, int maximum) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isIntegralNumber()
                || value.longValue() < minimum
                || value.longValue() > maximum) {
            throw new IllegalArgumentException(
                    field + " is outside the supported range");
        }
        return Math.toIntExact(value.longValue());
    }

    private static long requiredLong(
            JsonNode parent,
            String field,
            long minimum) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isIntegralNumber()
                || value.longValue() < minimum) {
            throw new IllegalArgumentException(
                    field + " is outside the supported range");
        }
        return value.longValue();
    }

    private static void requireTextEquals(
            JsonNode parent, String field, String expected) {
        if (!expected.equals(requiredText(parent, field, 64))) {
            throw new IllegalArgumentException(field + " is unsupported");
        }
    }

    private static void requireIntegerEquals(
            JsonNode parent, String field, int expected) {
        if (requiredInteger(parent, field, expected, expected) != expected) {
            throw new IllegalArgumentException(field + " is unsupported");
        }
    }

    private static LocalDateTime timestamp(JsonNode parent, String field) {
        try {
            return LocalDateTime.ofInstant(
                    Instant.parse(requiredText(parent, field, 40)),
                    ZoneOffset.UTC);
        } catch (RuntimeException exception) {
            throw new IllegalArgumentException(
                    field + " must be a UTC RFC3339 timestamp",
                    exception);
        }
    }

    private static void requireSingle(int rows, String action) {
        if (rows != 1) {
            throw new IllegalStateException(
                    action + " affected " + rows + " rows");
        }
    }

    record AssetState(
            long id,
            String devicePublicCode,
            int expectedPortCount,
            long factoryBagRevision,
            byte[] factoryBagSetSha256,
            String acceptanceStatus,
            long acceptanceGeneration,
            boolean oneNetOnline,
            boolean factoryBagsComplete) {
    }

    private record ExistingEvidence(String evidenceUid) {
    }

    record Evidence(
            String challengeUid,
            long factoryBagRevision,
            String factoryBagSetSha256,
            String edgeSoftwareVersion,
            String edgeProtocolVersion,
            String edgeStoreInstanceUid,
            String mcuFirmwareVersion,
            boolean persistentStoreHealthy,
            boolean trustedTimeHealthy,
            boolean configurationPersistenceHealthy,
            boolean mcuCommunicationHealthy,
            boolean sensorsHealthy,
            boolean camerasCaptureHealthy,
            boolean cameraUploadHealthy,
            boolean deviceEntryUrlStored,
            String deviceEntryUrlSha256,
            boolean mcuSimulated,
            boolean camerasSimulated,
            int verifiedPortCount,
            int verifiedCameraCount,
            String sensorSampleSha256,
            String cameraCaptureSha256,
            String cameraUploadSha256) {
    }
}
