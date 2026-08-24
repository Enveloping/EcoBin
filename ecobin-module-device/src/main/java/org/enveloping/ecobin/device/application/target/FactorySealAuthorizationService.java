package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.port.FactorySealDispatchAuthorizationPort;
import org.enveloping.ecobin.device.api.port.FactorySealReliableTaskPort;
import org.enveloping.ecobin.device.api.result.FactorySealDispatchDecision;
import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistrationPort;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.security.MessageDigest;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeParseException;
import java.time.format.DateTimeFormatter;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * Creates and applies the one narrow cloud authorization that may end factory
 * mode.  The authorization is a snapshot, never a derived online/time flag.
 */
@Service
public class FactorySealAuthorizationService
        implements FactorySealDispatchAuthorizationPort {

    public static final String COMMAND_TYPE = "AUTHORIZE_FACTORY_SEAL";
    static final String EVIDENCE_NOT_LATEST =
            "ACCEPTANCE_EVIDENCE_NOT_LATEST";
    private static final String TARGET_TYPE = "DEVICE_ASSET";
    private static final String SHA256 = "^[0-9a-f]{64}$";
    private static final String UUID_V4 =
            "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}"
                    + "-[89ab][0-9a-f]{3}-[0-9a-f]{12}$";
    private static final Set<String> COMPLETION_V1_FIELDS = Set.of(
            "sealCompletionSchemaVersion",
            "hardwareSn",
            "authorizationCommandUid",
            "acceptanceGeneration",
            "acceptanceEvidenceUid",
            "acceptanceEvidenceSha256",
            "acceptanceChallengeUid",
            "factoryBagRevision",
            "factoryBagSetSha256",
            "imageReleaseId",
            "imageReleaseSha256",
            "factoryReportSha256",
            "authorizationBindingSha256",
            "operatorConfirmationUid",
            "sealedAt",
            "cleanupCompletedAt");
    private static final Set<String> COMPLETION_V2_FIELDS = Set.of(
            "sealCompletionSchemaVersion",
            "hardwareSn",
            "authorizationCommandUid",
            "acceptanceGeneration",
            "acceptanceEvidenceUid",
            "acceptanceEvidenceSha256",
            "acceptanceChallengeUid",
            "factoryBagRevision",
            "factoryBagSetSha256",
            "imageReleaseId",
            "imageReleaseSha256",
            "factoryReportSha256",
            "authorizationBindingSha256",
            "operatorConfirmationUid",
            "completionClockQuality",
            "sealedAt",
            "cleanupCompletedAt");

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final DeviceConfigurationCanonicalizer canonicalizer;
    private final PlatformDeviceAssetTaskRefFactory taskRefFactory;
    private final ReliablePlatformDeviceControlTaskRegistrationPort tasks;
    private final FactorySealReliableTaskPort reliableTasks;
    private final Duration commandLifetime;

    public FactorySealAuthorizationService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            DeviceConfigurationCanonicalizer canonicalizer,
            PlatformDeviceAssetTaskRefFactory taskRefFactory,
            ReliablePlatformDeviceControlTaskRegistrationPort tasks,
            FactorySealReliableTaskPort reliableTasks,
            @Value("${ecobin.device.factory-seal.command-lifetime:P365D}")
            Duration commandLifetime) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.canonicalizer = canonicalizer;
        this.taskRefFactory = taskRefFactory;
        this.tasks = tasks;
        this.reliableTasks = reliableTasks;
        if (commandLifetime == null
                || commandLifetime.isZero()
                || commandLifetime.isNegative()) {
            throw new IllegalArgumentException(
                    "factory seal command lifetime must be positive");
        }
        this.commandLifetime = commandLifetime;
    }

    /**
     * Idempotently creates the task for the current PASSED generation.
     * The caller and this method share one transaction with the acceptance
     * state change, so PASSED can never commit without its reliable task.
     */
    @Transactional
    public boolean ensureForAcceptedAsset(long assetId) {
        AssetSnapshot asset = lockAsset(assetId);
        if (!"PASSED".equals(asset.acceptanceStatus())
                || asset.acceptanceGeneration() <= 0
                || asset.acceptanceEvidenceSha256() == null
                || asset.factoryBagSetSha256() == null) {
            return false;
        }
        Integer existing = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_factory_seal_authorization
                        WHERE asset_id = ?
                          AND acceptance_generation = ?
                        """,
                Integer.class,
                asset.id(),
                asset.acceptanceGeneration());
        if (existing != null && existing > 0) {
            return false;
        }

        AcceptanceEvidence evidence = acceptedEvidence(asset);
        if (evidence == null) {
            // Legacy PASSED rows without a V52 generation-bound evidence row
            // are deliberately not granted factory seal authority.
            return false;
        }
        LocalDateTime now = databaseNow();
        UUID commandUid = UUID.randomUUID();
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("sealAuthorizationSchemaVersion", 1);
        payload.put("hardwareSn", asset.hardwareSn());
        payload.put("acceptanceGeneration", asset.acceptanceGeneration());
        payload.put("acceptanceEvidenceUid", evidence.evidenceUid());
        payload.put("acceptanceChallengeUid", evidence.challengeUid());
        payload.put(
                "acceptanceEvidenceSha256",
                HexFormat.of().formatHex(asset.acceptanceEvidenceSha256()));
        payload.put("factoryBagRevision", asset.factoryBagRevision());
        payload.put(
                "factoryBagSetSha256",
                HexFormat.of().formatHex(asset.factoryBagSetSha256()));

        Map<String, Object> target = new LinkedHashMap<>();
        target.put("type", TARGET_TYPE);
        target.put("uid", asset.hardwareSn());
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schemaVersion", 2);
        envelope.put("commandUid", commandUid.toString());
        envelope.put("commandType", COMMAND_TYPE);
        envelope.put("targetDeviceName", asset.hardwareSn());
        envelope.put("target", target);
        envelope.put("issuedAt", instant(now));
        envelope.put("expiresAt", instant(now.plus(commandLifetime)));
        envelope.put("payloadSchemaVersion", 2);
        envelope.put(
                "payloadSha256",
                canonicalizer.hex(canonicalizer.payloadSha256(payload)));
        envelope.put("payload", payload);
        envelope.put("cosGrant", null);

        UUID taskUid = tasks.register(
                new ReliablePlatformDeviceControlTaskRegistration(
                        COMMAND_TYPE,
                        COMMAND_TYPE + ":ASSET:"
                                + asset.id() + ":"
                                + asset.acceptanceGeneration(),
                        TARGET_TYPE,
                        asset.hardwareSn(),
                        taskRefFactory.issue(asset.id()),
                        2,
                        objectMapper.writeValueAsString(envelope),
                        canonicalizer.payloadSha256(envelope),
                        UUID.fromString(evidence.evidenceUid()),
                        UUID.fromString(evidence.acceptanceCommandUid()),
                        1000));
        requireSingle(jdbc.update("""
                        INSERT INTO dev_factory_seal_authorization (
                            asset_id, hardware_sn_snapshot,
                            acceptance_generation,
                            acceptance_evidence_uid,
                            acceptance_challenge_uid,
                            acceptance_evidence_sha256,
                            factory_bag_revision,
                            factory_bag_set_sha256,
                            command_uid, reliable_task_uid,
                            authorization_status,
                            acknowledged_at, cancelled_at,
                            cancellation_reason, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING',
                            NULL, NULL, NULL, ?, ?
                        )
                        """,
                asset.id(),
                asset.hardwareSn(),
                asset.acceptanceGeneration(),
                evidence.evidenceUid(),
                evidence.challengeUid(),
                asset.acceptanceEvidenceSha256(),
                asset.factoryBagRevision(),
                asset.factoryBagSetSha256(),
                commandUid.toString(),
                taskUid.toString(),
                now,
                now), "insert factory seal authorization");
        return true;
    }

    /**
     * Refuses every acceptance/bag snapshot mutation after seal authority has
     * been created.  PENDING is included because the OneNet call may already
     * have delivered even when its response is lost; cancellation alone
     * cannot retract a command that is in flight or durably accepted offline.
     */
    @Transactional(propagation = Propagation.MANDATORY)
    public void requireAcceptanceSnapshotMutable(long assetId) {
        List<Long> authorizations = jdbc.query("""
                        SELECT id
                        FROM dev_factory_seal_authorization
                        WHERE asset_id = ?
                          AND NOT (
                              authorization_status = 'CANCELLED'
                              AND cancellation_reason = ?
                          )
                        ORDER BY id
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("id"),
                assetId,
                EVIDENCE_NOT_LATEST);
        if (!authorizations.isEmpty()) {
            throw new TargetApiException(
                    409,
                    "DEVICE.FACTORY_SEAL_AUTHORITY_ISSUED",
                    "封存授权已签发，不能再修改设备身份、验收事实或厂家袋；返工需先走显式撤销流程");
        }
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public FactorySealDispatchDecision authorizeDispatch(
            UUID reliableTaskUid,
            UUID commandUid,
            String hardwareSn,
            LocalDateTime checkedAt) {
        if (reliableTaskUid == null || commandUid == null
                || hardwareSn == null || checkedAt == null) {
            throw new IllegalArgumentException(
                    "factory seal dispatch identity is required");
        }
        // Keep the global lock order identical to acceptance evidence
        // ingestion: device asset first, authorization second.  A JOIN ...
        // FOR UPDATE issued first can otherwise deadlock with the path that
        // already owns the asset row and is validating issued authorities.
        if (!lockAssetByHardwareSn(hardwareSn)) {
            return new FactorySealDispatchDecision(
                    FactorySealDispatchDecision.Outcome.CANCEL_STALE,
                    "AUTHORIZATION_FACT_MISSING");
        }
        List<AuthorizationSnapshot> rows = jdbc.query("""
                        SELECT authorization.id,
                               authorization.authorization_status,
                               authorization.hardware_sn_snapshot,
                               authorization.acceptance_generation,
                               authorization.acceptance_evidence_sha256,
                               authorization.factory_bag_revision,
                               authorization.factory_bag_set_sha256,
                               asset.hardware_sn,
                               asset.acceptance_status,
                               asset.acceptance_generation
                                   AS current_acceptance_generation,
                               asset.acceptance_evidence_sha256
                                   AS current_acceptance_evidence_sha256,
                               asset.factory_bag_revision
                                   AS current_factory_bag_revision,
                               asset.factory_bag_set_sha256
                                   AS current_factory_bag_set_sha256
                        FROM dev_factory_seal_authorization authorization
                        JOIN dev_device_asset asset
                          ON asset.id = authorization.asset_id
                        WHERE authorization.reliable_task_uid = ?
                          AND authorization.command_uid = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new AuthorizationSnapshot(
                        rs.getLong("id"),
                        rs.getString("authorization_status"),
                        rs.getString("hardware_sn_snapshot"),
                        rs.getLong("acceptance_generation"),
                        rs.getBytes("acceptance_evidence_sha256"),
                        rs.getLong("factory_bag_revision"),
                        rs.getBytes("factory_bag_set_sha256"),
                        rs.getString("hardware_sn"),
                        rs.getString("acceptance_status"),
                        rs.getLong("current_acceptance_generation"),
                        rs.getBytes("current_acceptance_evidence_sha256"),
                        rs.getLong("current_factory_bag_revision"),
                        rs.getBytes("current_factory_bag_set_sha256")),
                reliableTaskUid.toString(),
                commandUid.toString());
        if (rows.size() != 1) {
            return new FactorySealDispatchDecision(
                    FactorySealDispatchDecision.Outcome.CANCEL_STALE,
                    "AUTHORIZATION_FACT_MISSING");
        }
        AuthorizationSnapshot authorization = rows.getFirst();
        if ("ACKNOWLEDGED".equals(authorization.status())
                || "SEALED".equals(authorization.status())) {
            return new FactorySealDispatchDecision(
                    FactorySealDispatchDecision.Outcome
                            .ALREADY_ACKNOWLEDGED,
                    "AUTHORIZATION_ALREADY_ACKNOWLEDGED");
        }
        if (!"PENDING".equals(authorization.status())
                || !matchesCurrent(
                        authorization, hardwareSn)) {
            cancelAuthorization(
                    authorization.id(),
                    "ACCEPTANCE_SNAPSHOT_CHANGED",
                    checkedAt);
            return new FactorySealDispatchDecision(
                    FactorySealDispatchDecision.Outcome.CANCEL_STALE,
                    "ACCEPTANCE_SNAPSHOT_CHANGED");
        }
        return new FactorySealDispatchDecision(
                FactorySealDispatchDecision.Outcome.ALLOW,
                null);
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public ObservationResult applyTrustedObservation(
            String hardwareSn,
            JsonNode event,
            LocalDateTime receivedAt) {
        String commandUid = requiredText(event, "commandUid", 36);
        JsonNode target = requiredObject(event, "target");
        JsonNode payload = requiredObject(event, "payload");
        requireText(target, "type", "DEVICE_COMMAND");
        requireText(target, "uid", commandUid);
        requireText(
                payload, "observedCommandType", COMMAND_TYPE);
        String stage = requiredText(payload, "stage", 32);
        if (!List.of("RECEIVED", "ACCEPTED", "REJECTED")
                .contains(stage)) {
            throw new IllegalArgumentException(
                    "factory seal observation stage is unsupported");
        }
        if (!lockAssetByHardwareSn(hardwareSn)) {
            throw new IllegalArgumentException(
                    "factory seal observation has no authoritative command");
        }
        List<ObservedAuthorization> rows = jdbc.query("""
                        SELECT authorization.id,
                               authorization.asset_id,
                               authorization.reliable_task_uid,
                               authorization.authorization_status,
                               authorization.hardware_sn_snapshot,
                               authorization.acceptance_generation,
                               authorization.acceptance_evidence_uid,
                               authorization.acceptance_evidence_sha256,
                               authorization.factory_bag_revision,
                               authorization.factory_bag_set_sha256,
                               asset.hardware_sn,
                               asset.acceptance_status,
                               asset.acceptance_generation
                                   AS current_acceptance_generation,
                               asset.acceptance_evidence_sha256
                                   AS current_acceptance_evidence_sha256,
                               asset.factory_bag_revision
                                   AS current_factory_bag_revision,
                               asset.factory_bag_set_sha256
                                   AS current_factory_bag_set_sha256
                        FROM dev_factory_seal_authorization authorization
                        JOIN dev_device_asset asset
                          ON asset.id = authorization.asset_id
                        WHERE authorization.command_uid = ?
                          AND authorization.hardware_sn_snapshot = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new ObservedAuthorization(
                        rs.getLong("id"),
                        rs.getLong("asset_id"),
                        rs.getString("reliable_task_uid"),
                        rs.getString("authorization_status"),
                        rs.getString("hardware_sn_snapshot"),
                        rs.getLong("acceptance_generation"),
                        rs.getString("acceptance_evidence_uid"),
                        rs.getBytes("acceptance_evidence_sha256"),
                        rs.getLong("factory_bag_revision"),
                        rs.getBytes("factory_bag_set_sha256"),
                        rs.getString("hardware_sn"),
                        rs.getString("acceptance_status"),
                        rs.getLong("current_acceptance_generation"),
                        rs.getBytes("current_acceptance_evidence_sha256"),
                        rs.getLong("current_factory_bag_revision"),
                        rs.getBytes("current_factory_bag_set_sha256")),
                commandUid,
                hardwareSn);
        if (rows.size() != 1) {
            throw new IllegalArgumentException(
                    "factory seal observation has no authoritative command");
        }
        ObservedAuthorization authorization = rows.getFirst();
        if ("CANCELLED".equals(authorization.status())) {
            return new ObservationResult(
                    authorization.assetId(), false);
        }
        String rejectionCode = optionalErrorCode(payload);
        if ("SEALED".equals(authorization.status())) {
            if ("REJECTED".equals(stage)) {
                throw new IllegalArgumentException(
                        "sealed factory authorization cannot be rejected");
            }
            if ("ACCEPTED".equals(stage)) {
                reliableTasks.completeAcceptedCommand(
                        UUID.fromString(commandUid), receivedAt);
            }
            return new ObservationResult(authorization.assetId(), false);
        }
        if ("REJECTED".equals(stage)
                && "ACKNOWLEDGED".equals(authorization.status())) {
            throw new IllegalArgumentException(
                    "acknowledged factory seal authorization cannot be rejected");
        }
        if ("REJECTED".equals(stage)
                || !matchesCurrent(authorization, hardwareSn)) {
            boolean recoverLatestEvidence = "REJECTED".equals(stage)
                    && EVIDENCE_NOT_LATEST.equals(rejectionCode)
                    && "PENDING".equals(authorization.status())
                    && matchesCurrent(authorization, hardwareSn);
            String reason = recoverLatestEvidence
                    ? EVIDENCE_NOT_LATEST
                    : "REJECTED".equals(stage)
                            ? "DEVICE_REJECTED_AUTHORIZATION"
                            : "ACCEPTANCE_SNAPSHOT_CHANGED";
            cancelAuthorization(
                    authorization.id(), reason, receivedAt);
            reliableTasks.cancelTask(
                    UUID.fromString(authorization.taskUid()),
                    reason,
                    receivedAt);
            if (recoverLatestEvidence) {
                recoverLatestAcceptanceEvidence(
                        authorization, receivedAt);
            }
            return new ObservationResult(
                    authorization.assetId(), true);
        }
        if ("RECEIVED".equals(stage)) {
            // RECEIVED only proves transport delivery.  The device must first
            // durably persist and validate the authorization, then emit
            // ACCEPTED before the server may acknowledge it or finish the
            // reliable command.
            return new ObservationResult(
                    authorization.assetId(), false);
        }
        boolean changed = false;
        if ("PENDING".equals(authorization.status())) {
            requireSingle(jdbc.update("""
                            UPDATE dev_factory_seal_authorization
                            SET authorization_status = 'ACKNOWLEDGED',
                                acknowledged_at = ?,
                                updated_at = ?
                            WHERE id = ?
                              AND authorization_status = 'PENDING'
                            """,
                    receivedAt,
                    receivedAt,
                    authorization.id()),
                    "acknowledge factory seal authorization");
            changed = true;
        }
        reliableTasks.completeAcceptedCommand(
                UUID.fromString(commandUid), receivedAt);
        return new ObservationResult(
                authorization.assetId(), changed);
    }

    /**
     * Applies the device's post-cleanup one-way seal fact.  The global lock
     * order is always asset first, then authorization, then the reliable
     * operations task.  This is also the convergence path when the completion
     * event arrives before the earlier ACCEPTED observation.
     */
    @Transactional(propagation = Propagation.MANDATORY)
    public CompletionResult applyTrustedCompletion(
            String hardwareSn,
            JsonNode event,
            LocalDateTime receivedAt) {
        if (hardwareSn == null || hardwareSn.isBlank()
                || receivedAt == null) {
            throw new IllegalArgumentException(
                    "factory seal completion identity is required");
        }
        requireText(event, "eventType", "FACTORY_SEAL_COMPLETED");
        String eventUid = requiredPattern(event, "eventUid", UUID_V4);
        String commandUid = requiredPattern(
                event, "commandUid", UUID_V4);
        String payloadSha256 = requiredPattern(
                event, "payloadSha256", SHA256);
        JsonNode target = requiredObject(event, "target");
        requireText(target, "type", TARGET_TYPE);
        requireText(target, "uid", hardwareSn);
        JsonNode payload = requiredObject(event, "payload");
        long completionSchemaVersion = requiredSafeInteger(
                payload, "sealCompletionSchemaVersion", 1);
        Set<String> expectedFields = switch (
                Math.toIntExact(completionSchemaVersion)) {
            case 1 -> COMPLETION_V1_FIELDS;
            case 2 -> COMPLETION_V2_FIELDS;
            default -> throw new IllegalArgumentException(
                    "sealCompletionSchemaVersion is unsupported");
        };
        if (payload.size() != expectedFields.size()) {
            throw new IllegalArgumentException(
                    "factory seal completion fields differ");
        }
        payload.propertyNames().forEach(field -> {
            if (!expectedFields.contains(field)) {
                throw new IllegalArgumentException(
                        "factory seal completion fields differ");
            }
        });
        CompletionPayload completion = completionPayload(payload);
        if (!hardwareSn.equals(completion.hardwareSn())
                || !commandUid.equals(
                        completion.authorizationCommandUid())) {
            throw new IllegalArgumentException(
                    "factory seal completion authority differs");
        }
        String clockQuality = requiredText(event, "clockQuality", 16);
        if (!Set.of("SYNCED", "ESTIMATED", "UNAVAILABLE")
                .contains(clockQuality)
                || !clockQuality.equals(completion.clockQuality())) {
            throw new IllegalArgumentException(
                    "factory seal completion clock quality differs");
        }
        Instant occurred = nullableUtcInstant(
                event, "occurredAt");
        if ("SYNCED".equals(clockQuality)
                && (occurred == null
                    || !occurred.equals(completion.cleanupCompletedAt()))) {
            throw new IllegalArgumentException(
                    "factory seal event time differs from completed cleanup");
        }
        if (!"SYNCED".equals(clockQuality) && occurred != null) {
            throw new IllegalArgumentException(
                    "unsynced factory seal event carries occurredAt");
        }
        byte[] canonicalPayloadSha256 = canonicalizer.payloadSha256(
                completion.canonicalPayload());
        if (!payloadSha256.equals(canonicalizer.hex(
                canonicalPayloadSha256))) {
            throw new IllegalArgumentException(
                    "factory seal completion payload digest differs");
        }
        byte[] expectedBinding = canonicalizer.payloadSha256(
                completion.bindingPayload());
        if (!MessageDigest.isEqual(
                expectedBinding,
                hexBytes(completion.authorizationBindingSha256()))) {
            throw new IllegalArgumentException(
                    "factory seal completion binding differs");
        }
        if (completion.cleanupCompletedAt() != null
                && completion.cleanupCompletedAt().isBefore(
                        completion.sealedAt())) {
            throw new IllegalArgumentException(
                    "factory seal cleanup precedes sealing");
        }

        // Do not replace this with a JOIN-first lock: acceptance ingestion
        // also owns the asset row before it touches issued authorizations.
        if (!lockAssetByHardwareSn(hardwareSn)) {
            throw new IllegalArgumentException(
                    "factory seal completion has no authoritative asset");
        }
        List<CompletionAuthorization> rows = jdbc.query("""
                        SELECT authorization.id,
                               authorization.asset_id,
                               authorization.command_uid,
                               authorization.reliable_task_uid,
                               authorization.authorization_status,
                               authorization.hardware_sn_snapshot,
                               authorization.acceptance_generation,
                               authorization.acceptance_evidence_uid,
                               authorization.acceptance_challenge_uid,
                               authorization.acceptance_evidence_sha256,
                               authorization.factory_bag_revision,
                               authorization.factory_bag_set_sha256,
                               authorization.completion_event_uid,
                               authorization.completion_payload_sha256,
                               asset.hardware_sn,
                               asset.acceptance_status,
                               asset.acceptance_generation
                                   AS current_acceptance_generation,
                               asset.acceptance_evidence_sha256
                                   AS current_acceptance_evidence_sha256,
                               asset.factory_bag_revision
                                   AS current_factory_bag_revision,
                               asset.factory_bag_set_sha256
                                   AS current_factory_bag_set_sha256
                        FROM dev_factory_seal_authorization authorization
                        JOIN dev_device_asset asset
                          ON asset.id = authorization.asset_id
                        WHERE authorization.command_uid = ?
                          AND authorization.hardware_sn_snapshot = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new CompletionAuthorization(
                        rs.getLong("id"),
                        rs.getLong("asset_id"),
                        rs.getString("command_uid"),
                        rs.getString("reliable_task_uid"),
                        rs.getString("authorization_status"),
                        rs.getString("hardware_sn_snapshot"),
                        rs.getLong("acceptance_generation"),
                        rs.getString("acceptance_evidence_uid"),
                        rs.getString("acceptance_challenge_uid"),
                        rs.getBytes("acceptance_evidence_sha256"),
                        rs.getLong("factory_bag_revision"),
                        rs.getBytes("factory_bag_set_sha256"),
                        rs.getString("completion_event_uid"),
                        rs.getBytes("completion_payload_sha256"),
                        rs.getString("hardware_sn"),
                        rs.getString("acceptance_status"),
                        rs.getLong("current_acceptance_generation"),
                        rs.getBytes("current_acceptance_evidence_sha256"),
                        rs.getLong("current_factory_bag_revision"),
                        rs.getBytes("current_factory_bag_set_sha256")),
                commandUid,
                hardwareSn);
        if (rows.size() != 1) {
            throw new IllegalArgumentException(
                    "factory seal completion has no authoritative command");
        }
        CompletionAuthorization authorization = rows.getFirst();
        if ("CANCELLED".equals(authorization.status())) {
            throw new IllegalArgumentException(
                    "cancelled factory seal authorization cannot be revived");
        }
        if (!matchesCurrent(authorization, hardwareSn)
                || authorization.acceptanceGeneration()
                    != completion.acceptanceGeneration()
                || !authorization.acceptanceEvidenceUid().equals(
                        completion.acceptanceEvidenceUid())
                || !authorization.acceptanceChallengeUid().equals(
                        completion.acceptanceChallengeUid())
                || !same(
                        authorization.acceptanceEvidenceSha256(),
                        hexBytes(completion.acceptanceEvidenceSha256()))
                || authorization.factoryBagRevision()
                    != completion.factoryBagRevision()
                || !same(
                        authorization.factoryBagSetSha256(),
                        hexBytes(completion.factoryBagSetSha256()))) {
            throw new IllegalArgumentException(
                    "factory seal completion snapshot differs");
        }
        if ("SEALED".equals(authorization.status())) {
            if (!eventUid.equals(authorization.completionEventUid())
                    || !same(
                            authorization.completionPayloadSha256(),
                            canonicalPayloadSha256)) {
                throw new IllegalArgumentException(
                        "factory seal completion conflicts with sealed fact");
            }
            reliableTasks.completeAcceptedCommand(
                    UUID.fromString(commandUid), receivedAt);
            return new CompletionResult(authorization.assetId(), false);
        }
        if (!Set.of("PENDING", "ACKNOWLEDGED").contains(
                authorization.status())) {
            throw new IllegalArgumentException(
                    "factory seal completion state is invalid");
        }
        requireSingle(jdbc.update("""
                        UPDATE dev_factory_seal_authorization
                        SET authorization_status = 'SEALED',
                            completion_event_uid = ?,
                            completion_payload_sha256 = ?,
                            image_release_id = ?,
                            image_release_sha256 = ?,
                            factory_report_sha256 = ?,
                            authorization_binding_sha256 = ?,
                            operator_confirmation_uid = ?,
                            completion_clock_quality = ?,
                            sealed_at = ?,
                            cleanup_completed_at = ?,
                            completion_received_at = ?,
                            updated_at = ?
                        WHERE id = ?
                          AND authorization_status IN (
                              'PENDING', 'ACKNOWLEDGED'
                          )
                        """,
                eventUid,
                canonicalPayloadSha256,
                completion.imageReleaseId(),
                hexBytes(completion.imageReleaseSha256()),
                hexBytes(completion.factoryReportSha256()),
                expectedBinding,
                completion.operatorConfirmationUid(),
                completion.clockQuality(),
                nullableLocalDateTime(completion.sealedAt()),
                nullableLocalDateTime(completion.cleanupCompletedAt()),
                receivedAt,
                receivedAt,
                authorization.id()),
                "complete factory seal authorization");
        reliableTasks.completeAcceptedCommand(
                UUID.fromString(commandUid), receivedAt);
        return new CompletionResult(authorization.assetId(), true);
    }

    /** Requires the exact current accepted generation to be fully sealed. */
    @Transactional(propagation = Propagation.MANDATORY)
    public void requireCurrentGenerationSealed(
            long assetId, String hardwareSn) {
        List<Long> rows = jdbc.query("""
                        /* factory-seal-assignment-gate:asset-already-locked */
                        SELECT authorization.id
                        FROM dev_factory_seal_authorization authorization
                        JOIN dev_device_asset asset
                          ON asset.id = authorization.asset_id
                        WHERE asset.id = ?
                          AND asset.hardware_sn = ?
                          AND asset.acceptance_status = 'PASSED'
                          AND authorization.authorization_status = 'SEALED'
                          AND authorization.acceptance_generation =
                              asset.acceptance_generation
                          AND authorization.acceptance_evidence_sha256 =
                              asset.acceptance_evidence_sha256
                          AND authorization.factory_bag_revision =
                              asset.factory_bag_revision
                          AND authorization.factory_bag_set_sha256 =
                              asset.factory_bag_set_sha256
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("id"),
                assetId,
                hardwareSn);
        if (rows.size() != 1) {
            throw new TargetApiException(
                    422,
                    "DEVICE.FACTORY_SEAL_REQUIRED",
                    "设备尚未完成当前验收代次的厂家封存，不能进入租户或机构业务");
        }
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public void invalidateForAsset(
            long assetId,
            String reasonCode,
            LocalDateTime cancelledAt) {
        if (reasonCode == null
                || !reasonCode.matches("[A-Z][A-Z0-9_]{0,63}")) {
            throw new IllegalArgumentException(
                    "factory seal invalidation reason is invalid");
        }
        lockAsset(assetId);
        List<PendingTask> tasksToCancel = jdbc.query("""
                        SELECT id, reliable_task_uid
                        FROM dev_factory_seal_authorization
                        WHERE asset_id = ?
                          AND authorization_status = 'PENDING'
                        FOR UPDATE
                        """,
                (rs, ignored) -> new PendingTask(
                        rs.getLong("id"),
                        rs.getString("reliable_task_uid")),
                assetId);
        for (PendingTask task : tasksToCancel) {
            cancelAuthorization(task.id(), reasonCode, cancelledAt);
            reliableTasks.cancelTask(
                    UUID.fromString(task.taskUid()),
                    reasonCode,
                    cancelledAt);
        }
    }

    private AssetSnapshot lockAsset(long assetId) {
        List<AssetSnapshot> rows = jdbc.query("""
                        SELECT id, hardware_sn, acceptance_status,
                               acceptance_generation,
                               acceptance_evidence_sha256,
                               factory_bag_revision,
                               factory_bag_set_sha256
                        FROM dev_device_asset
                        WHERE id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new AssetSnapshot(
                        rs.getLong("id"),
                        rs.getString("hardware_sn"),
                        rs.getString("acceptance_status"),
                        rs.getLong("acceptance_generation"),
                        rs.getBytes("acceptance_evidence_sha256"),
                        rs.getLong("factory_bag_revision"),
                        rs.getBytes("factory_bag_set_sha256")),
                assetId);
        if (rows.size() != 1) {
            throw new IllegalArgumentException(
                    "factory seal asset does not exist");
        }
        return rows.getFirst();
    }

    private boolean lockAssetByHardwareSn(String hardwareSn) {
        List<Long> rows = jdbc.query("""
                        /* factory-seal-lock-order:asset-first */
                        SELECT id
                        FROM dev_device_asset
                        WHERE hardware_sn = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("id"),
                hardwareSn);
        if (rows.size() > 1) {
            throw new IllegalStateException(
                    "factory seal hardware identity is not unique");
        }
        return rows.size() == 1;
    }

    private AcceptanceEvidence acceptedEvidence(AssetSnapshot asset) {
        List<AcceptanceEvidence> rows = jdbc.query("""
                        SELECT evidence_uid, challenge_uid, command_uid,
                               evidence_sha256,
                               factory_bag_revision,
                               factory_bag_set_sha256
                        FROM dev_device_acceptance_evidence
                        WHERE asset_id = ?
                          AND evidence_schema_version >= 3
                          AND evaluation_status = 'PASSED'
                          AND evidence_sha256 = ?
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                (rs, ignored) -> new AcceptanceEvidence(
                        rs.getString("evidence_uid"),
                        rs.getString("challenge_uid"),
                        rs.getString("command_uid"),
                        rs.getBytes("evidence_sha256"),
                        rs.getLong("factory_bag_revision"),
                        rs.getBytes("factory_bag_set_sha256")),
                asset.id(),
                asset.acceptanceEvidenceSha256());
        if (rows.isEmpty()) {
            return null;
        }
        AcceptanceEvidence evidence = rows.getFirst();
        return evidence.factoryBagRevision() == asset.factoryBagRevision()
                && same(
                        evidence.factoryBagSetSha256(),
                        asset.factoryBagSetSha256())
                ? evidence : null;
    }

    private void cancelAuthorization(
            long id,
            String reason,
            LocalDateTime cancelledAt) {
        jdbc.update("""
                        UPDATE dev_factory_seal_authorization
                        SET authorization_status = 'CANCELLED',
                            acknowledged_at = NULL,
                            cancelled_at = ?,
                            cancellation_reason = ?,
                            updated_at = ?
                        WHERE id = ?
                          AND authorization_status = 'PENDING'
                        """,
                cancelledAt,
                reason,
                cancelledAt,
                id);
    }

    /**
     * A trusted terminal rejection proves that the device durably refused the
     * old authorization because a newer local acceptance fact exists.  Reuse
     * a newer already-ingested PASSED fact when possible; otherwise return the
     * asset to PENDING so the normal online scheduler obtains a fresh fact.
     */
    private void recoverLatestAcceptanceEvidence(
            ObservedAuthorization authorization,
            LocalDateTime recoveredAt) {
        List<byte[]> candidates = jdbc.query("""
                        SELECT candidate.evidence_sha256
                        FROM dev_device_acceptance_evidence candidate
                        JOIN dev_device_acceptance_evidence authorized
                          ON authorized.asset_id = candidate.asset_id
                         AND authorized.evidence_uid = ?
                        WHERE candidate.asset_id = ?
                          AND candidate.id > authorized.id
                          AND candidate.evidence_schema_version >= 3
                          AND candidate.evaluation_status = 'PASSED'
                          AND candidate.factory_bag_revision = ?
                          AND candidate.factory_bag_set_sha256 = ?
                          AND NOT EXISTS (
                              SELECT 1
                              FROM dev_factory_seal_authorization used
                              WHERE used.asset_id = candidate.asset_id
                                AND used.acceptance_evidence_uid =
                                    candidate.evidence_uid
                          )
                        ORDER BY candidate.id DESC
                        LIMIT 1
                        """,
                (rs, ignored) -> rs.getBytes("evidence_sha256"),
                authorization.acceptanceEvidenceUid(),
                authorization.assetId(),
                authorization.factoryBagRevision(),
                authorization.factoryBagSetSha256());
        if (candidates.isEmpty()) {
            requireSingle(jdbc.update("""
                            UPDATE dev_device_asset
                            SET acceptance_status = 'PENDING',
                                accepted_at = NULL,
                                acceptance_evidence_sha256 = NULL,
                                last_acceptance_evaluated_at = ?,
                                acceptance_failure_json =
                                    JSON_ARRAY(?),
                                control_version = control_version + 1,
                                updated_at = ?
                            WHERE id = ?
                              AND acceptance_status = 'PASSED'
                              AND acceptance_generation = ?
                              AND acceptance_evidence_sha256 = ?
                            """,
                    recoveredAt,
                    EVIDENCE_NOT_LATEST,
                    recoveredAt,
                    authorization.assetId(),
                    authorization.acceptanceGeneration(),
                    authorization.acceptanceEvidenceSha256()),
                    "return stale acceptance to pending");
            return;
        }

        requireSingle(jdbc.update("""
                        UPDATE dev_device_asset
                        SET acceptance_generation =
                                acceptance_generation + 1,
                            acceptance_evidence_sha256 = ?,
                            last_acceptance_evaluated_at = ?,
                            acceptance_failure_json = NULL,
                            control_version = control_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND acceptance_status = 'PASSED'
                          AND acceptance_generation = ?
                          AND acceptance_evidence_sha256 = ?
                        """,
                candidates.getFirst(),
                recoveredAt,
                recoveredAt,
                authorization.assetId(),
                authorization.acceptanceGeneration(),
                authorization.acceptanceEvidenceSha256()),
                "advance rejected acceptance evidence");
        ensureForAcceptedAsset(authorization.assetId());
    }

    private static boolean matchesCurrent(
            SnapshotComparison snapshot,
            String hardwareSn) {
        return "PASSED".equals(snapshot.currentAcceptanceStatus())
                && hardwareSn.equals(snapshot.hardwareSnSnapshot())
                && hardwareSn.equals(snapshot.currentHardwareSn())
                && snapshot.acceptanceGeneration()
                    == snapshot.currentAcceptanceGeneration()
                && same(
                        snapshot.acceptanceEvidenceSha256(),
                        snapshot.currentAcceptanceEvidenceSha256())
                && snapshot.factoryBagRevision()
                    == snapshot.currentFactoryBagRevision()
                && same(
                        snapshot.factoryBagSetSha256(),
                        snapshot.currentFactoryBagSetSha256());
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
    }

    private static boolean same(byte[] left, byte[] right) {
        return left != null && right != null
                && MessageDigest.isEqual(left, right);
    }

    private static String instant(LocalDateTime value) {
        return DateTimeFormatter.ISO_INSTANT.format(
                value.toInstant(ZoneOffset.UTC));
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

    private CompletionPayload completionPayload(JsonNode payload) {
        JsonNode schemaVersion = payload.get("sealCompletionSchemaVersion");
        if (schemaVersion == null
                || !schemaVersion.isIntegralNumber()
                || (schemaVersion.longValue() != 1L
                    && schemaVersion.longValue() != 2L)) {
            throw new IllegalArgumentException(
                    "sealCompletionSchemaVersion is unsupported");
        }
        long acceptanceGeneration = requiredSafeInteger(
                payload, "acceptanceGeneration", 1);
        long factoryBagRevision = requiredSafeInteger(
                payload, "factoryBagRevision", 0);
        String imageReleaseId = requiredText(
                payload, "imageReleaseId", 128);
        if (!imageReleaseId.matches("^[\\x21-\\x7e]{1,128}$")) {
            throw new IllegalArgumentException(
                    "imageReleaseId is not printable ASCII");
        }
        long version = schemaVersion.longValue();
        String clockQuality = version == 1
                ? "SYNCED"
                : requiredText(payload, "completionClockQuality", 16);
        if (!Set.of("SYNCED", "ESTIMATED", "UNAVAILABLE")
                .contains(clockQuality)) {
            throw new IllegalArgumentException(
                    "completionClockQuality is unsupported");
        }
        String sealedAtText = nullableTextValue(payload, "sealedAt", 30);
        String cleanupCompletedAtText = nullableTextValue(
                payload, "cleanupCompletedAt", 30);
        boolean syncedClock = "SYNCED".equals(clockQuality);
        if ((syncedClock
                    && (sealedAtText == null
                        || cleanupCompletedAtText == null))
                || (!syncedClock
                    && (sealedAtText != null
                        || cleanupCompletedAtText != null))) {
            throw new IllegalArgumentException(
                    "factory seal completion timestamps differ from clock quality");
        }
        return new CompletionPayload(
                Math.toIntExact(version),
                requiredText(payload, "hardwareSn", 64),
                requiredPattern(
                        payload, "authorizationCommandUid", UUID_V4),
                acceptanceGeneration,
                requiredPattern(payload, "acceptanceEvidenceUid", UUID_V4),
                requiredPattern(
                        payload, "acceptanceEvidenceSha256", SHA256),
                requiredPattern(
                        payload, "acceptanceChallengeUid", UUID_V4),
                factoryBagRevision,
                requiredPattern(payload, "factoryBagSetSha256", SHA256),
                imageReleaseId,
                requiredPattern(payload, "imageReleaseSha256", SHA256),
                requiredPattern(payload, "factoryReportSha256", SHA256),
                requiredPattern(
                        payload, "authorizationBindingSha256", SHA256),
                requiredPattern(
                        payload, "operatorConfirmationUid", UUID_V4),
                clockQuality,
                sealedAtText,
                sealedAtText == null
                        ? null
                        : parseUtcInstant(sealedAtText, "sealedAt"),
                cleanupCompletedAtText,
                cleanupCompletedAtText == null
                        ? null
                        : parseUtcInstant(
                                cleanupCompletedAtText,
                                "cleanupCompletedAt"));
    }

    private static String nullableTextValue(
            JsonNode parent, String field, int maximumLength) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null) {
            throw new IllegalArgumentException(field + " is required");
        }
        if (value.isNull()) {
            return null;
        }
        if (!value.isTextual()
                || value.asText().isBlank()
                || value.asText().length() > maximumLength) {
            throw new IllegalArgumentException(
                    field + " must be bounded text or null");
        }
        return value.asText();
    }

    private static Instant nullableUtcInstant(
            JsonNode parent, String field) {
        String value = nullableTextValue(parent, field, 30);
        return value == null ? null : parseUtcInstant(value, field);
    }

    private static LocalDateTime nullableLocalDateTime(Instant value) {
        return value == null
                ? null
                : LocalDateTime.ofInstant(value, ZoneOffset.UTC);
    }

    private static long requiredSafeInteger(
            JsonNode parent, String field, long minimum) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isIntegralNumber()) {
            throw new IllegalArgumentException(field + " must be an integer");
        }
        long number = value.longValue();
        if (number < minimum || number > 9_007_199_254_740_991L) {
            throw new IllegalArgumentException(
                    field + " is outside the safe integer range");
        }
        return number;
    }

    private static String requiredPattern(
            JsonNode parent, String field, String pattern) {
        String value = requiredText(parent, field, 160);
        if (!value.matches(pattern)) {
            throw new IllegalArgumentException(
                    field + " has an invalid stable format");
        }
        return value;
    }

    private static Instant parseUtcInstant(
            String value, String field) {
        if (!value.matches(
                "^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
                        + "[0-9]{2}:[0-9]{2}:[0-9]{2}"
                        + "(?:\\.[0-9]{1,9})?Z$")) {
            throw new IllegalArgumentException(
                    field + " is not a UTC instant");
        }
        try {
            return Instant.parse(value);
        } catch (DateTimeParseException exception) {
            throw new IllegalArgumentException(
                    field + " is not a real UTC instant", exception);
        }
    }

    private static byte[] hexBytes(String value) {
        try {
            return HexFormat.of().parseHex(value);
        } catch (IllegalArgumentException exception) {
            throw new IllegalArgumentException(
                    "SHA-256 value is invalid", exception);
        }
    }

    private static String optionalErrorCode(JsonNode payload) {
        JsonNode value = payload == null ? null : payload.get("errorCode");
        if (value == null || value.isNull()) {
            return null;
        }
        if (!value.isTextual()
                || !value.asText().matches("[A-Z][A-Z0-9_]{0,63}")) {
            throw new IllegalArgumentException(
                    "factory seal observation errorCode is invalid");
        }
        return value.asText();
    }

    private static void requireText(
            JsonNode parent, String field, String expected) {
        if (!expected.equals(requiredText(parent, field, 64))) {
            throw new IllegalArgumentException(field + " is unsupported");
        }
    }

    private static void requireSingle(int rows, String action) {
        if (rows != 1) {
            throw new IllegalStateException(
                    action + " affected " + rows + " rows");
        }
    }

    public record ObservationResult(long assetId, boolean changed) {
    }

    public record CompletionResult(long assetId, boolean changed) {
    }

    private record CompletionPayload(
            int schemaVersion,
            String hardwareSn,
            String authorizationCommandUid,
            long acceptanceGeneration,
            String acceptanceEvidenceUid,
            String acceptanceEvidenceSha256,
            String acceptanceChallengeUid,
            long factoryBagRevision,
            String factoryBagSetSha256,
            String imageReleaseId,
            String imageReleaseSha256,
            String factoryReportSha256,
            String authorizationBindingSha256,
            String operatorConfirmationUid,
            String clockQuality,
            String sealedAtText,
            Instant sealedAt,
            String cleanupCompletedAtText,
            Instant cleanupCompletedAt) {

        private Map<String, Object> bindingPayload() {
            Map<String, Object> values = new LinkedHashMap<>();
            values.put("commandUid", authorizationCommandUid);
            values.put("hardwareSn", hardwareSn);
            values.put("acceptanceGeneration", acceptanceGeneration);
            values.put("acceptanceEvidenceUid", acceptanceEvidenceUid);
            values.put("acceptanceChallengeUid", acceptanceChallengeUid);
            values.put(
                    "acceptanceEvidenceSha256",
                    acceptanceEvidenceSha256);
            values.put("factoryBagRevision", factoryBagRevision);
            values.put("factoryBagSetSha256", factoryBagSetSha256);
            values.put("imageReleaseId", imageReleaseId);
            values.put("imageReleaseSha256", imageReleaseSha256);
            values.put("factoryReportSha256", factoryReportSha256);
            return values;
        }

        private Map<String, Object> canonicalPayload() {
            Map<String, Object> values = new LinkedHashMap<>();
            values.put("sealCompletionSchemaVersion", schemaVersion);
            values.put("hardwareSn", hardwareSn);
            values.put(
                    "authorizationCommandUid",
                    authorizationCommandUid);
            values.put("acceptanceGeneration", acceptanceGeneration);
            values.put("acceptanceEvidenceUid", acceptanceEvidenceUid);
            values.put(
                    "acceptanceEvidenceSha256",
                    acceptanceEvidenceSha256);
            values.put("acceptanceChallengeUid", acceptanceChallengeUid);
            values.put("factoryBagRevision", factoryBagRevision);
            values.put("factoryBagSetSha256", factoryBagSetSha256);
            values.put("imageReleaseId", imageReleaseId);
            values.put("imageReleaseSha256", imageReleaseSha256);
            values.put("factoryReportSha256", factoryReportSha256);
            values.put(
                    "authorizationBindingSha256",
                    authorizationBindingSha256);
            values.put("operatorConfirmationUid", operatorConfirmationUid);
            if (schemaVersion >= 2) {
                values.put("completionClockQuality", clockQuality);
            }
            values.put("sealedAt", sealedAtText);
            values.put("cleanupCompletedAt", cleanupCompletedAtText);
            return values;
        }
    }

    private interface SnapshotComparison {
        String hardwareSnSnapshot();

        long acceptanceGeneration();

        byte[] acceptanceEvidenceSha256();

        long factoryBagRevision();

        byte[] factoryBagSetSha256();

        String currentHardwareSn();

        String currentAcceptanceStatus();

        long currentAcceptanceGeneration();

        byte[] currentAcceptanceEvidenceSha256();

        long currentFactoryBagRevision();

        byte[] currentFactoryBagSetSha256();
    }

    private record AssetSnapshot(
            long id,
            String hardwareSn,
            String acceptanceStatus,
            long acceptanceGeneration,
            byte[] acceptanceEvidenceSha256,
            long factoryBagRevision,
            byte[] factoryBagSetSha256) {
    }

    private record AcceptanceEvidence(
            String evidenceUid,
            String challengeUid,
            String acceptanceCommandUid,
            byte[] evidenceSha256,
            long factoryBagRevision,
            byte[] factoryBagSetSha256) {
    }

    private record AuthorizationSnapshot(
            long id,
            String status,
            String hardwareSnSnapshot,
            long acceptanceGeneration,
            byte[] acceptanceEvidenceSha256,
            long factoryBagRevision,
            byte[] factoryBagSetSha256,
            String currentHardwareSn,
            String currentAcceptanceStatus,
            long currentAcceptanceGeneration,
            byte[] currentAcceptanceEvidenceSha256,
            long currentFactoryBagRevision,
            byte[] currentFactoryBagSetSha256)
            implements SnapshotComparison {
    }

    private record ObservedAuthorization(
            long id,
            long assetId,
            String taskUid,
            String status,
            String hardwareSnSnapshot,
            long acceptanceGeneration,
            String acceptanceEvidenceUid,
            byte[] acceptanceEvidenceSha256,
            long factoryBagRevision,
            byte[] factoryBagSetSha256,
            String currentHardwareSn,
            String currentAcceptanceStatus,
            long currentAcceptanceGeneration,
            byte[] currentAcceptanceEvidenceSha256,
            long currentFactoryBagRevision,
            byte[] currentFactoryBagSetSha256)
            implements SnapshotComparison {
    }

    private record CompletionAuthorization(
            long id,
            long assetId,
            String commandUid,
            String taskUid,
            String status,
            String hardwareSnSnapshot,
            long acceptanceGeneration,
            String acceptanceEvidenceUid,
            String acceptanceChallengeUid,
            byte[] acceptanceEvidenceSha256,
            long factoryBagRevision,
            byte[] factoryBagSetSha256,
            String completionEventUid,
            byte[] completionPayloadSha256,
            String currentHardwareSn,
            String currentAcceptanceStatus,
            long currentAcceptanceGeneration,
            byte[] currentAcceptanceEvidenceSha256,
            long currentFactoryBagRevision,
            byte[] currentFactoryBagSetSha256)
            implements SnapshotComparison {
    }

    private record PendingTask(long id, String taskUid) {
    }
}
