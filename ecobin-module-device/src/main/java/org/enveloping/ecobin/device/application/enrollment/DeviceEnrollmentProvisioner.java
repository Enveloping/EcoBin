package org.enveloping.ecobin.device.application.enrollment;

import org.enveloping.ecobin.device.api.port.OneNetDeviceProvisioningException;
import org.enveloping.ecobin.device.api.port.OneNetDeviceProvisioningPort;
import org.enveloping.ecobin.device.api.result.OneNetProvisionedDevice;
import org.enveloping.ecobin.device.application.target.DeviceEntryUrlFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.ObjectMapper;

import java.security.MessageDigest;
import java.security.SecureRandom;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Power-loss-safe continuation of an authenticated enrollment.  OneNet is
 * called outside the database transaction; a deterministic description marker
 * lets the adapter recover an ambiguous create response without duplicating a
 * device.
 */
@Component
public class DeviceEnrollmentProvisioner {

    private static final SecureRandom RANDOM = new SecureRandom();
    private static final SecureRandom PUBLIC_CODE_RANDOM = new SecureRandom();
    private static final ZoneId FACTORY_ZONE = ZoneId.of("Asia/Shanghai");

    private final JdbcTemplate jdbc;
    private final TransactionTemplate transaction;
    private final ObjectMapper objectMapper;
    private final DeviceEnrollmentProperties properties;
    private final RemoteSupportBootstrapProperties remoteSupport;
    private final OneNetDeviceProvisioningPort oneNet;
    private final DeviceEntryUrlFactory deviceEntryUrlFactory;
    private final String oneNetProductId;

    public DeviceEnrollmentProvisioner(
            JdbcTemplate jdbc,
            TransactionTemplate transaction,
            ObjectMapper objectMapper,
            DeviceEnrollmentProperties properties,
            RemoteSupportBootstrapProperties remoteSupport,
            OneNetDeviceProvisioningPort oneNet,
            DeviceEntryUrlFactory deviceEntryUrlFactory,
            @Value("${onenet.product-id:}") String oneNetProductId) {
        this.jdbc = jdbc;
        this.transaction = transaction;
        this.objectMapper = objectMapper;
        this.properties = properties;
        this.remoteSupport = remoteSupport;
        this.oneNet = oneNet;
        this.deviceEntryUrlFactory = deviceEntryUrlFactory;
        this.oneNetProductId = oneNetProductId == null
                ? "" : oneNetProductId.trim();
    }

    @Scheduled(
            initialDelayString =
                    "${ecobin.device.enrollment.scan-initial-delay-ms:1000}",
            fixedDelayString =
                    "${ecobin.device.enrollment.scan-delay-ms:5000}")
    public void processDueEnrollments() {
        if (!properties.isEnabled()) {
            return;
        }
        LocalDateTime now = databaseNow();
        LocalDateTime staleBefore = now.minus(
                positive(properties.getProvisioningTimeout()));
        List<UUID> due = jdbc.query("""
                        SELECT enrollment_uid
                        FROM dev_device_enrollment
                        WHERE (
                            status = 'PENDING'
                            AND (next_attempt_at IS NULL
                                 OR next_attempt_at <= ?)
                        ) OR (
                            status = 'PROVISIONING'
                            AND updated_at <= ?
                        )
                        ORDER BY id
                        LIMIT 10
                        """,
                (rs, ignored) -> UUID.fromString(
                        rs.getString("enrollment_uid")),
                now,
                staleBefore);
        due.forEach(this::process);
    }

    public void process(UUID enrollmentUid) {
        ClaimedEnrollment claimed = transaction.execute(status ->
                claim(enrollmentUid));
        if (claimed == null) {
            return;
        }
        try {
            validateRuntimeConfiguration();
            String marker = "ecobin-enrollment=" + claimed.enrollmentUid();
            OneNetProvisionedDevice provisioned = oneNet.ensureDevice(
                    claimed.hardwareSn(),
                    marker,
                    "SELF_ENROLLMENT".equals(claimed.mode()));
            if ("SELF_ENROLLMENT".equals(claimed.mode())
                    && !marker.equals(provisioned.description())) {
                throw new PermanentEnrollmentException(
                        "ONENET_DEVICE_OWNERSHIP_CONFLICT");
            }
            if ("LEGACY_ADOPTION".equals(claimed.mode())) {
                byte[] expected = DeviceEnrollmentCrypto.legacyProof(
                        provisioned.secretKey(), claimed.transcript());
                boolean proofValid = MessageDigest.isEqual(
                        expected, claimed.legacyProof());
                java.util.Arrays.fill(expected, (byte) 0);
                if (!proofValid) {
                    throw new PermanentEnrollmentException(
                            "LEGACY_ONENET_PROOF_INVALID");
                }
            }
            transaction.executeWithoutResult(status ->
                    complete(claimed, provisioned));
        } catch (OneNetDeviceProvisioningException failure) {
            if (failure.retryable()) {
                retry(claimed.id(), failure.code());
            } else {
                fail(claimed.id(), failure.code());
            }
        } catch (PermanentEnrollmentException failure) {
            fail(claimed.id(), failure.code());
        } catch (RuntimeException transientFailure) {
            // Details are deliberately not logged: an external exception can
            // contain a OneNet response body with sec_key.
            retry(claimed.id(), "PROVISIONING_TEMPORARILY_UNAVAILABLE");
        }
    }

    private ClaimedEnrollment claim(UUID enrollmentUid) {
        LocalDateTime now = databaseNow();
        LocalDateTime staleBefore = now.minus(
                positive(properties.getProvisioningTimeout()));
        ClaimedEnrollment row = jdbc.query("""
                        SELECT enrollment.id, enrollment.enrollment_uid,
                               enrollment.enrollment_mode,
                               enrollment.hardware_sn,
                               enrollment.identity_public_key,
                               enrollment.response_wrap_public_key,
                               enrollment.tunnel_public_key,
                               enrollment.ssh_host_public_key,
                               enrollment.legacy_proof,
                               enrollment.status, enrollment.next_attempt_at,
                               enrollment.updated_at,
                               challenge.nonce
                        FROM dev_device_enrollment enrollment
                        JOIN dev_device_enrollment_challenge challenge
                          ON challenge.id = enrollment.challenge_id
                        WHERE enrollment.enrollment_uid = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> {
                    String identityKey = rs.getString(
                            "identity_public_key");
                    byte[] nonce = rs.getBytes("nonce");
                    return new ClaimedEnrollment(
                            rs.getLong("id"),
                            UUID.fromString(rs.getString("enrollment_uid")),
                            rs.getString("enrollment_mode"),
                            rs.getString("hardware_sn"),
                            identityKey,
                            rs.getBytes("response_wrap_public_key"),
                            rs.getString("tunnel_public_key"),
                            rs.getString("ssh_host_public_key"),
                            rs.getBytes("legacy_proof"),
                            nonce,
                            null,
                            rs.getString("status"),
                            rs.getObject(
                                    "next_attempt_at", LocalDateTime.class),
                            rs.getObject("updated_at", LocalDateTime.class));
                },
                enrollmentUid.toString()).stream().findFirst().orElse(null);
        if (row == null || "READY".equals(row.status())
                || "FAILED".equals(row.status())) {
            return null;
        }
        boolean claimable = "PENDING".equals(row.status())
                && (row.nextAttemptAt() == null
                    || !row.nextAttemptAt().isAfter(now));
        claimable |= "PROVISIONING".equals(row.status())
                && !row.updatedAt().isAfter(staleBefore);
        if (!claimable) {
            return null;
        }
        EnrollmentTranscript transcript = enrollmentTranscript(row.id());
        int affected = jdbc.update("""
                        UPDATE dev_device_enrollment
                        SET status = 'PROVISIONING',
                            attempt_count = attempt_count + 1,
                            next_attempt_at = NULL,
                            updated_at = ?
                        WHERE id = ?
                          AND status IN ('PENDING', 'PROVISIONING')
                        """,
                now, row.id());
        if (affected != 1) {
            return null;
        }
        return new ClaimedEnrollment(
                row.id(), row.enrollmentUid(), row.mode(), row.hardwareSn(),
                row.identityPublicKey(), row.responseWrapPublicKey(),
                row.tunnelPublicKey(), row.sshHostPublicKey(),
                row.legacyProof(), row.challengeNonce(),
                transcript.transcript(), "PROVISIONING", null, now);
    }

    /*
     * Rebuild the exact authenticated transcript from persisted request fields
     * and the challenge.  This keeps the legacy proof verifiable after a
     * process restart without storing the global K1.
     */
    private EnrollmentTranscript enrollmentTranscript(long enrollmentId) {
        return jdbc.query("""
                        SELECT enrollment.enrollment_uid,
                               challenge.challenge_uid,
                               challenge.enrollment_key_id,
                               enrollment.enrollment_mode,
                               enrollment.hardware_sn,
                               enrollment.identity_public_key,
                               enrollment.response_wrap_public_key,
                               enrollment.tunnel_public_key,
                               enrollment.ssh_host_public_key,
                               challenge.nonce
                        FROM dev_device_enrollment enrollment
                        JOIN dev_device_enrollment_challenge challenge
                          ON challenge.id = enrollment.challenge_id
                        WHERE enrollment.id = ?
                        """,
                (rs, ignored) -> {
                    var canonical = new DeviceEnrollmentCrypto
                            .CanonicalEnrollmentRequest(
                            UUID.fromString(rs.getString("enrollment_uid")),
                            UUID.fromString(rs.getString("challenge_uid")),
                            rs.getString("enrollment_key_id"),
                            rs.getString("enrollment_mode"),
                            rs.getString("hardware_sn"),
                            rs.getString("identity_public_key"),
                            Base64.getEncoder().encodeToString(rs.getBytes(
                                    "response_wrap_public_key")),
                            rs.getString("tunnel_public_key"),
                            rs.getString("ssh_host_public_key"));
                    byte[] canonicalBytes = DeviceEnrollmentCrypto
                            .canonicalRequest(objectMapper, canonical);
                    return new EnrollmentTranscript(
                            DeviceEnrollmentCrypto.transcript(
                                    rs.getBytes("nonce"), canonicalBytes));
                },
                enrollmentId).stream().findFirst()
                .orElseThrow(() -> new IllegalStateException(
                        "claimed enrollment transcript is unavailable"));
    }

    private void complete(
            ClaimedEnrollment enrollment,
            OneNetProvisionedDevice provisioned) {
        LocalDateTime now = databaseNow();
        String status = jdbc.queryForObject("""
                        SELECT status
                        FROM dev_device_enrollment
                        WHERE id = ?
                        FOR UPDATE
                        """,
                String.class,
                enrollment.id());
        if ("READY".equals(status)) {
            return;
        }
        if (!"PROVISIONING".equals(status)) {
            throw new IllegalStateException(
                    "enrollment is no longer being provisioned");
        }

        Asset asset = "SELF_ENROLLMENT".equals(enrollment.mode())
                ? createSelfEnrolledAsset(enrollment, now)
                : adoptLegacyAsset(enrollment, now);
        insertMaintenanceIdentity(enrollment, asset.id(), now);

        DeviceEntryUrlFactory.Entry entry =
                deviceEntryUrlFactory.create(asset.publicCode());
        Map<String, Object> plaintext = enrollmentResponse(
                enrollment, asset, provisioned, entry.url());
        DeviceEnrollmentCrypto.EncryptedResponse encrypted =
                DeviceEnrollmentCrypto.encryptResponse(
                        objectMapper,
                        enrollment.enrollmentUid(),
                        enrollment.hardwareSn(),
                        enrollment.responseWrapPublicKey(),
                        plaintext,
                        enrollment.challengeNonce(),
                        RANDOM);
        byte[] responseSha256 = DeviceEnrollmentCrypto.sha256(
                encrypted.envelope());
        int affected = jdbc.update("""
                        UPDATE dev_device_enrollment
                        SET status = 'READY', asset_id = ?,
                            onenet_device_id = ?, encrypted_response = ?,
                            response_nonce = ?, response_sha256 = ?,
                            failure_code = NULL, next_attempt_at = NULL,
                            completed_at = ?, updated_at = ?
                        WHERE id = ? AND status = 'PROVISIONING'
                        """,
                asset.id(),
                provisioned.deviceId(),
                encrypted.envelope(),
                encrypted.nonce(),
                responseSha256,
                now,
                now,
                enrollment.id());
        if (affected != 1) {
            throw new IllegalStateException(
                    "enrollment completion lost its state lock");
        }
    }

    private Asset createSelfEnrolledAsset(
            ClaimedEnrollment enrollment,
            LocalDateTime now) {
        if (!jdbc.queryForList("""
                        SELECT id FROM dev_device_asset
                        WHERE hardware_sn = ? FOR UPDATE
                        """, Long.class, enrollment.hardwareSn()).isEmpty()) {
            throw new PermanentEnrollmentException(
                    "HARDWARE_SN_ALREADY_REGISTERED");
        }
        UUID assetUid = UUID.randomUUID();
        String publicCode = randomPublicCode();
        String productionBatch = "AUTO-" + LocalDate.now(FACTORY_ZONE)
                .format(DateTimeFormatter.BASIC_ISO_DATE);
        try {
            jdbc.update("""
                            INSERT INTO dev_device_asset (
                                asset_uid, device_public_code, hardware_sn,
                                model_name, production_batch,
                                registration_source, expected_port_count,
                                installation_display_name,
                                installation_updated_at,
                                tenant_id, tenant_assigned_at,
                                organization_id, organization_assigned_at,
                                acceptance_status, accepted_at,
                                acceptance_evidence_sha256,
                                last_acceptance_evaluated_at,
                                acceptance_failure_json,
                                lifecycle_status, disabled_at,
                                disable_reason, retired_at,
                                retirement_reason, control_version,
                                created_at, updated_at
                            ) VALUES (
                                ?, ?, ?, ?, ?, 'SELF_ENROLLMENT', ?, ?, ?,
                                NULL, NULL, NULL, NULL,
                                'PENDING', NULL, NULL, NULL, NULL,
                                'NORMAL', NULL, NULL, NULL, NULL, 0, ?, ?
                            )
                            """,
                    assetUid.toString(),
                    publicCode,
                    enrollment.hardwareSn(),
                    properties.getModelCode(),
                    productionBatch,
                    properties.getExpectedPortCount(),
                    "回收箱 " + enrollment.hardwareSn(),
                    now,
                    now,
                    now);
        } catch (DataIntegrityViolationException collision) {
            throw new PermanentEnrollmentException(
                    "DEVICE_ASSET_IDENTITY_CONFLICT");
        }
        long assetId = jdbc.queryForObject("""
                        SELECT id FROM dev_device_asset
                        WHERE asset_uid = ?
                        """, Long.class, assetUid.toString());
        jdbc.update("""
                        INSERT INTO dev_device_transport_state (
                            asset_id, onenet_connection_status,
                            status_observed_at, status_received_at,
                            evidence_source, source_inbox_id,
                            lock_version, created_at, updated_at
                        ) VALUES (
                            ?, 'UNKNOWN', NULL, NULL, NULL, NULL, 0, ?, ?
                        )
                        """,
                assetId, now, now);
        return new Asset(assetId, assetUid, publicCode);
    }

    private Asset adoptLegacyAsset(
            ClaimedEnrollment enrollment,
            LocalDateTime now) {
        Asset asset = jdbc.query("""
                        SELECT id, asset_uid, device_public_code
                        FROM dev_device_asset
                        WHERE hardware_sn = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new Asset(
                        rs.getLong("id"),
                        UUID.fromString(rs.getString("asset_uid")),
                        rs.getString("device_public_code")),
                enrollment.hardwareSn()).stream().findFirst()
                .orElseThrow(() -> new PermanentEnrollmentException(
                        "LEGACY_DEVICE_ASSET_NOT_FOUND"));
        int updated = jdbc.update("""
                        UPDATE dev_device_asset
                        SET registration_source = 'LEGACY_ADOPTION',
                            updated_at = ?
                        WHERE id = ?
                        """,
                now, asset.id());
        if (updated != 1) {
            throw new IllegalStateException("legacy asset update failed");
        }
        return asset;
    }

    private void insertMaintenanceIdentity(
            ClaimedEnrollment enrollment,
            long assetId,
            LocalDateTime now) {
        try {
            jdbc.update("""
                            INSERT INTO dev_device_maintenance_identity (
                                asset_id, enrollment_id,
                                identity_public_key,
                                identity_fingerprint_sha256,
                                tunnel_public_key,
                                tunnel_fingerprint_sha256,
                                ssh_host_public_key,
                                ssh_host_fingerprint_sha256,
                                maintenance_principal,
                                tunnel_server_host, tunnel_server_port,
                                tunnel_server_user,
                                tunnel_server_host_public_key,
                                registered_at, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                    assetId,
                    enrollment.id(),
                    enrollment.identityPublicKey(),
                    DeviceEnrollmentCrypto.sshFingerprint(
                            enrollment.identityPublicKey()),
                    enrollment.tunnelPublicKey(),
                    DeviceEnrollmentCrypto.sshFingerprint(
                            enrollment.tunnelPublicKey()),
                    enrollment.sshHostPublicKey(),
                    DeviceEnrollmentCrypto.sshFingerprint(
                            enrollment.sshHostPublicKey()),
                    maintenancePrincipal(enrollment.hardwareSn()),
                    remoteSupport.getTunnelHost(),
                    remoteSupport.getTunnelSshPort(),
                    remoteSupport.getTunnelUser(),
                    remoteSupport.getTunnelServerHostPublicKey(),
                    now,
                    now,
                    now);
        } catch (DataIntegrityViolationException collision) {
            throw new PermanentEnrollmentException(
                    "MAINTENANCE_IDENTITY_CONFLICT");
        }
    }

    private Map<String, Object> enrollmentResponse(
            ClaimedEnrollment enrollment,
            Asset asset,
            OneNetProvisionedDevice provisioned,
            String deviceEntryUrl) {
        Map<String, Object> oneNetValue = new LinkedHashMap<>();
        oneNetValue.put("productId", oneNetProductId);
        oneNetValue.put("deviceName", provisioned.deviceName());
        oneNetValue.put("deviceId", provisioned.deviceId());
        oneNetValue.put("deviceKey", provisioned.secretKey());
        oneNetValue.put("mqttHost", properties.getMqttHost());
        oneNetValue.put("mqttPort", properties.getMqttPort());

        Map<String, Object> remote = new LinkedHashMap<>();
        remote.put("tunnelHost", remoteSupport.getTunnelHost());
        remote.put("tunnelSshPort", remoteSupport.getTunnelSshPort());
        remote.put("tunnelUser", remoteSupport.getTunnelUser());
        remote.put("tunnelServerHostPublicKey",
                remoteSupport.getTunnelServerHostPublicKey());
        remote.put("jumpUser", remoteSupport.getJumpUser());
        remote.put("maintenancePrincipal",
                maintenancePrincipal(enrollment.hardwareSn()));
        remote.put("maintenanceCaPublicKey",
                remoteSupport.getMaintenanceCaPublicKey());

        Map<String, Object> response = new LinkedHashMap<>();
        response.put("schemaVersion", 1);
        response.put("assetUid", asset.uid().toString());
        response.put("hardwareSn", enrollment.hardwareSn());
        response.put("modelCode", properties.getModelCode());
        response.put("expectedPortCount", properties.getExpectedPortCount());
        response.put("oneNet", oneNetValue);
        response.put("deviceEntryUrl", deviceEntryUrl);
        response.put("remoteSupport", remote);
        return response;
    }

    private void validateRuntimeConfiguration() {
        if (oneNetProductId.isBlank()
                || properties.getExpectedPortCount() < 1
                || properties.getExpectedPortCount() > 6
                || properties.getModelCode() == null
                || properties.getModelCode().isBlank()
                || properties.getMqttHost() == null
                || properties.getMqttHost().isBlank()
                || properties.getMqttPort() < 1
                || properties.getMqttPort() > 65535
                || !remoteSupport.isEnabled()
                || remoteSupport.getTunnelHost() == null
                || remoteSupport.getTunnelHost().isBlank()
                || remoteSupport.getTunnelSshPort() < 1
                || remoteSupport.getTunnelSshPort() > 65535
                || !"ecobin-tunnel".equals(remoteSupport.getTunnelUser())
                || remoteSupport.getJumpUser() == null
                || remoteSupport.getJumpUser().isBlank()
                || remoteSupport.getMaintenanceCaPublicKey() == null) {
            throw new IllegalStateException(
                    "device enrollment runtime configuration is incomplete");
        }
        DeviceEnrollmentCrypto.ed25519RawPublicKey(
                remoteSupport.getTunnelServerHostPublicKey());
        DeviceEnrollmentCrypto.ed25519RawPublicKey(
                remoteSupport.getMaintenanceCaPublicKey());
    }

    private void retry(long enrollmentId, String ignoredSafeCode) {
        transaction.executeWithoutResult(status -> {
            LocalDateTime now = databaseNow();
            jdbc.update("""
                            UPDATE dev_device_enrollment
                            SET status = 'PENDING', failure_code = NULL,
                                next_attempt_at = ?, updated_at = ?
                            WHERE id = ? AND status = 'PROVISIONING'
                            """,
                    now.plus(positive(properties.getRetryDelay())),
                    now,
                    enrollmentId);
        });
    }

    private void fail(long enrollmentId, String failureCode) {
        String safeCode = failureCode != null
                        && failureCode.matches("[A-Z0-9_]{1,100}")
                ? failureCode : "PROVISIONING_FAILED";
        transaction.executeWithoutResult(status -> {
            LocalDateTime now = databaseNow();
            jdbc.update("""
                            UPDATE dev_device_enrollment
                            SET status = 'FAILED', failure_code = ?,
                                next_attempt_at = NULL,
                                completed_at = ?, updated_at = ?
                            WHERE id = ? AND status = 'PROVISIONING'
                            """,
                    safeCode, now, now, enrollmentId);
        });
    }

    private static String maintenancePrincipal(String hardwareSn) {
        return "ecobin-device-" + hardwareSn;
    }

    private static String randomPublicCode() {
        byte[] random = new byte[18];
        PUBLIC_CODE_RANDOM.nextBytes(random);
        return "Dv_" + Base64.getUrlEncoder()
                .withoutPadding().encodeToString(random);
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
    }

    private static java.time.Duration positive(java.time.Duration value) {
        if (value == null || value.isZero() || value.isNegative()) {
            throw new IllegalStateException("duration must be positive");
        }
        return value;
    }

    private record ClaimedEnrollment(
            long id,
            UUID enrollmentUid,
            String mode,
            String hardwareSn,
            String identityPublicKey,
            byte[] responseWrapPublicKey,
            String tunnelPublicKey,
            String sshHostPublicKey,
            byte[] legacyProof,
            byte[] challengeNonce,
            byte[] transcript,
            String status,
            LocalDateTime nextAttemptAt,
            LocalDateTime updatedAt) {
    }

    private record EnrollmentTranscript(byte[] transcript) {
    }

    private record Asset(long id, UUID uid, String publicCode) {
    }

    private static final class PermanentEnrollmentException
            extends RuntimeException {

        private final String code;

        private PermanentEnrollmentException(String code) {
            super(code);
            this.code = code;
        }

        private String code() {
            return code;
        }
    }
}
