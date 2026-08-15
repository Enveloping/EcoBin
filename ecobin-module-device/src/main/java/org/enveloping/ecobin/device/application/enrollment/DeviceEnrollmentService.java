package org.enveloping.ecobin.device.application.enrollment;

import org.enveloping.ecobin.device.web.v1.enrollment.DeviceEnrollmentModels.ChallengeView;
import org.enveloping.ecobin.device.web.v1.enrollment.DeviceEnrollmentModels.EnrollmentRequest;
import org.enveloping.ecobin.device.web.v1.enrollment.DeviceEnrollmentModels.EnrollmentView;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.Arrays;
import java.util.Base64;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Authenticates the one-time factory enrollment transcript.  The global
 * enrollment key is used only at this public boundary and is never persisted.
 */
@Service
public class DeviceEnrollmentService {

    private static final SecureRandom RANDOM = new SecureRandom();
    private static final int MAXIMUM_RATE_BUCKETS = 10_000;
    private static final String HARDWARE_SN_PATTERN =
            "^[A-Za-z0-9_-]{8,64}$";

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final DeviceEnrollmentProperties properties;
    private final ConcurrentHashMap<String, RateBucket> challengeRates =
            new ConcurrentHashMap<>();

    public DeviceEnrollmentService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            DeviceEnrollmentProperties properties) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.properties = properties;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public ChallengeView createChallenge(String remoteAddress) {
        byte[] enrollmentKey = requireEnrollmentKey();
        Arrays.fill(enrollmentKey, (byte) 0);
        enforceRateLimit(remoteAddress);

        LocalDateTime now = databaseNow();
        LocalDateTime expiresAt = now.plus(requirePositive(
                properties.getChallengeLifetime(),
                "challengeLifetime"));
        UUID challengeUid = UUID.randomUUID();
        byte[] nonce = new byte[32];
        RANDOM.nextBytes(nonce);
        byte[] addressHash = DeviceEnrollmentCrypto.sha256(
                normalizedRemoteAddress(remoteAddress)
                        .getBytes(StandardCharsets.UTF_8));
        jdbc.update("""
                        INSERT INTO dev_device_enrollment_challenge (
                            challenge_uid, enrollment_key_id, nonce,
                            request_ip_sha256, status, expires_at,
                            consumed_at, created_at
                        ) VALUES (?, ?, ?, ?, 'ISSUED', ?, NULL, ?)
                        """,
                challengeUid.toString(),
                properties.getKeyId(),
                nonce,
                addressHash,
                expiresAt,
                now);
        return new ChallengeView(
                1,
                challengeUid,
                properties.getKeyId(),
                Base64.getEncoder().encodeToString(nonce),
                expiresAt.toInstant(ZoneOffset.UTC));
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public EnrollmentView submit(EnrollmentRequest request) {
        NormalizedRequest normalized = normalize(request);
        Challenge challenge = findChallenge(
                normalized.canonical().challengeUid(), false);
        byte[] canonicalBytes = DeviceEnrollmentCrypto.canonicalRequest(
                objectMapper, normalized.canonical());
        byte[] transcript = DeviceEnrollmentCrypto.transcript(
                challenge.nonce(), canonicalBytes);
        byte[] requestSha256 = requestSha256(normalized, transcript);

        EnrollmentRow replay = findEnrollment(
                normalized.canonical().enrollmentUid(), false);
        if (replay != null) {
            requireSameRequest(replay, requestSha256);
            return view(replay);
        }

        challenge = findChallenge(
                normalized.canonical().challengeUid(), true);
        LocalDateTime now = databaseNow();
        if (!"ISSUED".equals(challenge.status())
                || !challenge.expiresAt().isAfter(now)) {
            throw conflict(
                    "DEVICE.ENROLLMENT_CHALLENGE_UNAVAILABLE",
                    "注册挑战已经使用或过期，请重新获取挑战");
        }
        if (!properties.getKeyId().equals(challenge.enrollmentKeyId())
                || !properties.getKeyId().equals(
                normalized.canonical().enrollmentKeyId())) {
            throw invalid("注册密钥版本不匹配");
        }
        authenticate(normalized, transcript);

        try {
            jdbc.update("""
                            INSERT INTO dev_device_enrollment (
                                enrollment_uid, challenge_id,
                                enrollment_mode, hardware_sn,
                                identity_public_key,
                                identity_fingerprint_sha256,
                                response_wrap_public_key,
                                tunnel_public_key,
                                tunnel_fingerprint_sha256,
                                ssh_host_public_key,
                                ssh_host_fingerprint_sha256,
                                request_sha256, request_signature,
                                legacy_proof, status, asset_id,
                                onenet_device_id, encrypted_response,
                                response_nonce, response_sha256,
                                failure_code, attempt_count,
                                next_attempt_at, completed_at,
                                created_at, updated_at
                            ) VALUES (
                                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                'PENDING', NULL, NULL, NULL, NULL, NULL,
                                NULL, 0, ?, NULL, ?, ?
                            )
                            """,
                    normalized.canonical().enrollmentUid().toString(),
                    challenge.id(),
                    normalized.canonical().enrollmentMode(),
                    normalized.canonical().hardwareSn(),
                    normalized.canonical().identityPublicKey(),
                    DeviceEnrollmentCrypto.sshFingerprint(
                            normalized.canonical().identityPublicKey()),
                    normalized.responseWrapPublicKey(),
                    normalized.canonical().tunnelPublicKey(),
                    DeviceEnrollmentCrypto.sshFingerprint(
                            normalized.canonical().tunnelPublicKey()),
                    normalized.canonical().sshHostPublicKey(),
                    DeviceEnrollmentCrypto.sshFingerprint(
                            normalized.canonical().sshHostPublicKey()),
                    requestSha256,
                    normalized.signature(),
                    normalized.legacyProof(),
                    now,
                    now,
                    now);
        } catch (DataIntegrityViolationException collision) {
            EnrollmentRow concurrent = findEnrollment(
                    normalized.canonical().enrollmentUid(), false);
            if (concurrent != null) {
                requireSameRequest(concurrent, requestSha256);
                return view(concurrent);
            }
            throw conflict(
                    "DEVICE.ENROLLMENT_IDENTITY_ALREADY_USED",
                    "该设备身份、硬件序列号或注册挑战已经登记");
        }
        int consumed = jdbc.update("""
                        UPDATE dev_device_enrollment_challenge
                        SET status = 'CONSUMED', consumed_at = ?
                        WHERE id = ? AND status = 'ISSUED'
                        """,
                now, challenge.id());
        if (consumed != 1) {
            throw conflict(
                    "DEVICE.ENROLLMENT_CHALLENGE_UNAVAILABLE",
                    "注册挑战已经使用，请重新获取挑战");
        }
        return view(findEnrollment(
                normalized.canonical().enrollmentUid(), false));
    }

    private void authenticate(
            NormalizedRequest request,
            byte[] transcript) {
        byte[] key = requireEnrollmentKey();
        byte[] expectedMac;
        try {
            expectedMac = DeviceEnrollmentCrypto.hmacSha256(key, transcript);
        } finally {
            Arrays.fill(key, (byte) 0);
        }
        boolean macValid = MessageDigest.isEqual(
                expectedMac, request.registrationMac());
        Arrays.fill(expectedMac, (byte) 0);
        boolean signatureValid = DeviceEnrollmentCrypto.verifyEd25519(
                request.canonical().identityPublicKey(),
                transcript,
                request.signature());
        if (!macValid || !signatureValid) {
            throw new TargetApiException(
                    401,
                    "DEVICE.ENROLLMENT_AUTHENTICATION_FAILED",
                    "设备注册认证失败");
        }
    }

    private NormalizedRequest normalize(EnrollmentRequest request) {
        if (request == null
                || request.schemaVersion() == null
                || request.schemaVersion() != 1
                || !uuidV4(request.enrollmentUid())
                || !uuidV4(request.challengeUid())) {
            throw invalid("设备注册请求版本或 UUID 无效");
        }
        String hardwareSn = trimmed(request.hardwareSn());
        if (hardwareSn == null || !hardwareSn.matches(HARDWARE_SN_PATTERN)) {
            throw invalid("硬件序列号格式无效");
        }
        String mode = trimmed(request.enrollmentMode());
        if (!"SELF_ENROLLMENT".equals(mode)
                && !"LEGACY_ADOPTION".equals(mode)) {
            throw invalid("设备注册模式无效");
        }
        String identityKey = trimmed(request.identityPublicKey());
        String tunnelKey = trimmed(request.tunnelPublicKey());
        String hostKey = trimmed(request.sshHostPublicKey());
        DeviceEnrollmentCrypto.ed25519RawPublicKey(identityKey);
        DeviceEnrollmentCrypto.ed25519RawPublicKey(tunnelKey);
        DeviceEnrollmentCrypto.ed25519RawPublicKey(hostKey);
        if (identityKey.equals(tunnelKey)
                || identityKey.equals(hostKey)
                || tunnelKey.equals(hostKey)) {
            throw invalid("注册身份、隧道和 SSH 主机必须使用不同密钥");
        }
        byte[] responseKey = canonicalBase64(
                request.responseWrapPublicKey(), 32,
                "responseWrapPublicKey");
        byte[] registrationMac = canonicalBase64(
                request.registrationMac(), 32,
                "registrationMac");
        byte[] signature = canonicalBase64(
                request.signature(), 64, "signature");
        byte[] legacyProof = request.legacyProof() == null
                ? null
                : canonicalBase64(request.legacyProof(), 32, "legacyProof");
        if ("SELF_ENROLLMENT".equals(mode)) {
            if (legacyProof != null) {
                throw invalid("全新设备注册不能携带旧设备凭据证明");
            }
            String derived = DeviceEnrollmentCrypto.deriveHardwareSn(
                    identityKey);
            if (!derived.equals(hardwareSn)) {
                throw invalid("硬件序列号与设备身份公钥不匹配");
            }
        } else if (legacyProof == null) {
            throw invalid("旧设备接管必须携带 OneNet 设备凭据证明");
        }
        String keyId = trimmed(request.enrollmentKeyId());
        if (keyId == null || !keyId.matches("^[A-Z0-9_-]{1,16}$")) {
            throw invalid("注册密钥版本格式无效");
        }
        var canonical = new DeviceEnrollmentCrypto.CanonicalEnrollmentRequest(
                request.enrollmentUid(),
                request.challengeUid(),
                keyId,
                mode,
                hardwareSn,
                identityKey,
                Base64.getEncoder().encodeToString(responseKey),
                tunnelKey,
                hostKey);
        return new NormalizedRequest(
                canonical,
                responseKey,
                registrationMac,
                signature,
                legacyProof);
    }

    private byte[] requireEnrollmentKey() {
        if (!properties.isEnabled()) {
            throw unavailable(
                    "DEVICE.ENROLLMENT_DISABLED",
                    "设备自助注册当前未启用");
        }
        String keyId = trimmed(properties.getKeyId());
        if (keyId == null || !keyId.matches("^[A-Z0-9_-]{1,16}$")) {
            throw unavailable(
                    "DEVICE.ENROLLMENT_NOT_CONFIGURED",
                    "设备注册密钥版本未正确配置");
        }
        try {
            return canonicalBase64(
                    properties.getKeyBase64(), 32, "enrollment key");
        } catch (TargetApiException invalidConfiguration) {
            throw unavailable(
                    "DEVICE.ENROLLMENT_NOT_CONFIGURED",
                    "设备注册密钥未正确配置");
        }
    }

    private Challenge findChallenge(UUID uid, boolean forUpdate) {
        String suffix = forUpdate ? " FOR UPDATE" : "";
        return jdbc.query("""
                        SELECT id, enrollment_key_id, nonce,
                               status, expires_at
                        FROM dev_device_enrollment_challenge
                        WHERE challenge_uid = ?
                        """ + suffix,
                (rs, ignored) -> new Challenge(
                        rs.getLong("id"),
                        rs.getString("enrollment_key_id"),
                        rs.getBytes("nonce"),
                        rs.getString("status"),
                        rs.getObject("expires_at", LocalDateTime.class)),
                uid.toString()).stream().findFirst()
                .orElseThrow(() -> invalid("注册挑战不存在"));
    }

    private EnrollmentRow findEnrollment(UUID uid, boolean forUpdate) {
        String suffix = forUpdate ? " FOR UPDATE" : "";
        return jdbc.query("""
                        SELECT enrollment_uid, status, request_sha256,
                               encrypted_response, failure_code
                        FROM dev_device_enrollment
                        WHERE enrollment_uid = ?
                        """ + suffix,
                DeviceEnrollmentService::mapEnrollment,
                uid.toString()).stream().findFirst().orElse(null);
    }

    private EnrollmentView view(EnrollmentRow row) {
        JsonNode encrypted = row.encryptedResponse() == null
                ? null
                : objectMapper.readTree(row.encryptedResponse());
        String publicStatus = "PROVISIONING".equals(row.status())
                ? "PENDING" : row.status();
        return new EnrollmentView(
                1,
                row.enrollmentUid(),
                publicStatus,
                "PENDING".equals(publicStatus)
                        ? properties.getRetryDelay().toMillis() : null,
                encrypted,
                row.failureCode());
    }

    private void enforceRateLimit(String remoteAddress) {
        if (properties.getChallengeLimitPerMinute() < 1) {
            throw unavailable(
                    "DEVICE.ENROLLMENT_NOT_CONFIGURED",
                    "设备注册限流配置无效");
        }
        long minute = Instant.now().truncatedTo(ChronoUnit.MINUTES)
                .getEpochSecond();
        String address = normalizedRemoteAddress(remoteAddress);
        if (challengeRates.size() >= MAXIMUM_RATE_BUCKETS
                && !challengeRates.containsKey(address)) {
            challengeRates.entrySet().removeIf(entry ->
                    entry.getValue().minute() < minute - 60);
            if (challengeRates.size() >= MAXIMUM_RATE_BUCKETS) {
                throw tooManyRequests();
            }
        }
        RateBucket bucket = challengeRates.compute(address, (key, old) ->
                old == null || old.minute() != minute
                        ? new RateBucket(minute, 1)
                        : new RateBucket(minute, old.count() + 1));
        if (bucket.count() > properties.getChallengeLimitPerMinute()) {
            throw tooManyRequests();
        }
    }

    private static byte[] requestSha256(
            NormalizedRequest request,
            byte[] transcript) {
        ByteArrayOutputStream value = new ByteArrayOutputStream();
        value.writeBytes(transcript);
        value.writeBytes(request.registrationMac());
        value.writeBytes(request.signature());
        if (request.legacyProof() != null) {
            value.writeBytes(request.legacyProof());
        }
        return DeviceEnrollmentCrypto.sha256(value.toByteArray());
    }

    private static void requireSameRequest(
            EnrollmentRow row,
            byte[] requestSha256) {
        if (!MessageDigest.isEqual(row.requestSha256(), requestSha256)) {
            throw conflict(
                    "DEVICE.ENROLLMENT_IDEMPOTENCY_CONFLICT",
                    "相同 enrollmentUid 已绑定到不同注册请求");
        }
    }

    private static EnrollmentRow mapEnrollment(
            ResultSet rs,
            int ignored) throws SQLException {
        return new EnrollmentRow(
                UUID.fromString(rs.getString("enrollment_uid")),
                rs.getString("status"),
                rs.getBytes("request_sha256"),
                rs.getBytes("encrypted_response"),
                rs.getString("failure_code"));
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
    }

    private static byte[] canonicalBase64(
            String value,
            int exactLength,
            String field) {
        if (value == null) {
            throw invalid(field + " 不能为空");
        }
        byte[] decoded;
        try {
            decoded = DeviceEnrollmentCrypto.decodeBase64(
                    value, exactLength);
        } catch (IllegalArgumentException invalidValue) {
            throw invalid(field + " 不是规范 Base64 数据");
        }
        if (!Base64.getEncoder().encodeToString(decoded).equals(value)) {
            throw invalid(field + " 不是规范 Base64 数据");
        }
        return decoded;
    }

    private static java.time.Duration requirePositive(
            java.time.Duration value,
            String field) {
        if (value == null || value.isZero() || value.isNegative()) {
            throw new IllegalStateException(field + " must be positive");
        }
        return value;
    }

    private static boolean uuidV4(UUID value) {
        return value != null && value.version() == 4;
    }

    private static String trimmed(String value) {
        return value == null ? null : value.trim();
    }

    private static String normalizedRemoteAddress(String value) {
        String normalized = trimmed(value);
        return normalized == null || normalized.isEmpty()
                ? "unknown" : normalized;
    }

    private static TargetApiException invalid(String message) {
        return new TargetApiException(
                400, "COMMON.INVALID_REQUEST", message);
    }

    private static TargetApiException conflict(
            String code,
            String message) {
        return new TargetApiException(409, code, message);
    }

    private static TargetApiException unavailable(
            String code,
            String message) {
        return new TargetApiException(503, code, message, true, Map.of());
    }

    private static TargetApiException tooManyRequests() {
        return new TargetApiException(
                429,
                "DEVICE.ENROLLMENT_RATE_LIMITED",
                "注册挑战请求过于频繁，请稍后重试",
                true,
                Map.of());
    }

    private record NormalizedRequest(
            DeviceEnrollmentCrypto.CanonicalEnrollmentRequest canonical,
            byte[] responseWrapPublicKey,
            byte[] registrationMac,
            byte[] signature,
            byte[] legacyProof) {
    }

    private record Challenge(
            long id,
            String enrollmentKeyId,
            byte[] nonce,
            String status,
            LocalDateTime expiresAt) {
    }

    private record EnrollmentRow(
            UUID enrollmentUid,
            String status,
            byte[] requestSha256,
            byte[] encryptedResponse,
            String failureCode) {
    }

    private record RateBucket(long minute, int count) {
    }
}
