package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.port.DeviceAcceptanceChallengeCoordinatorPort;
import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistrationPort;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.time.Duration;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Creates credential-free platform tasks for factory acceptance.  Temporary
 * COS credentials are attached only by the real outbound adapter.
 */
@Service
public class AutomaticDeviceAcceptanceChallengeService
        implements DeviceAcceptanceChallengeCoordinatorPort {

    static final String TASK_TYPE = "REQUEST_DEVICE_ACCEPTANCE";
    static final String TARGET_TYPE = "DEVICE_ASSET";

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final DeviceConfigurationCanonicalizer canonicalizer;
    private final DeviceEntryUrlFactory deviceEntryUrlFactory;
    private final PlatformDeviceAssetTaskRefFactory taskRefFactory;
    private final ReliablePlatformDeviceControlTaskRegistrationPort
            taskRegistration;
    private final boolean realExternalMode;
    private final Duration retryInterval;
    private final Duration commandLifetime;

    public AutomaticDeviceAcceptanceChallengeService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            DeviceConfigurationCanonicalizer canonicalizer,
            DeviceEntryUrlFactory deviceEntryUrlFactory,
            PlatformDeviceAssetTaskRefFactory taskRefFactory,
            ReliablePlatformDeviceControlTaskRegistrationPort
                    taskRegistration,
            @Value("${ecobin.external.mode:fake}") String externalMode,
            @Value("${ecobin.device.acceptance.challenge-retry-interval:PT5M}")
            Duration retryInterval,
            @Value("${ecobin.device.acceptance.challenge-lifetime:PT5M}")
            Duration commandLifetime) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.canonicalizer = canonicalizer;
        this.deviceEntryUrlFactory = deviceEntryUrlFactory;
        this.taskRefFactory = taskRefFactory;
        this.taskRegistration = taskRegistration;
        this.realExternalMode = "real".equalsIgnoreCase(externalMode);
        this.retryInterval = positive(retryInterval, "retryInterval");
        this.commandLifetime = positive(commandLifetime, "commandLifetime");
    }

    @Override
    /*
     * ONLINE inbox handling already owns the asset/transport row locks.  Join
     * that transaction so the presence projection and its reliable challenge
     * are committed atomically.  A new transaction would wait on the caller's
     * locks and eventually roll the ONLINE event back.  The scheduler has no
     * caller transaction, so REQUIRED still opens one for scheduled retries.
     */
    @Transactional(isolation = Isolation.READ_COMMITTED)
    public boolean requestIfNeeded(long assetId) {
        if (!realExternalMode) {
            return false;
        }
        List<AssetCandidate> assets = jdbc.query("""
                        SELECT asset.id, asset.hardware_sn,
                               asset.device_public_code,
                               asset.expected_port_count,
                               asset.acceptance_status,
                               asset.lifecycle_status,
                               transport.onenet_connection_status
                        FROM dev_device_asset asset
                        JOIN dev_device_transport_state transport
                          ON transport.asset_id = asset.id
                        WHERE asset.id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new AssetCandidate(
                        rs.getLong("id"),
                        rs.getString("hardware_sn"),
                        rs.getString("device_public_code"),
                        rs.getInt("expected_port_count"),
                        rs.getString("acceptance_status"),
                        rs.getString("lifecycle_status"),
                        rs.getString("onenet_connection_status")),
                assetId);
        if (assets.size() != 1) {
            throw new IllegalArgumentException(
                    "acceptance asset does not exist");
        }
        AssetCandidate asset = assets.getFirst();
        if ("PASSED".equals(asset.acceptanceStatus())
                || !"NORMAL".equals(asset.lifecycleStatus())
                || !"ONLINE".equals(asset.transportStatus())) {
            return false;
        }
        Integer installedBagCount = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_factory_installed_bag
                        WHERE asset_id = ?
                        """,
                Integer.class,
                asset.id());
        if (installedBagCount == null
                || installedBagCount != asset.expectedPortCount()) {
            return false;
        }
        Integer active = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM ops_reliable_task
                        WHERE scope_kind = 'PLATFORM'
                          AND task_type = ?
                          AND source_device_asset_id = ?
                          AND state = 'PENDING'
                        """,
                Integer.class,
                TASK_TYPE,
                asset.id());
        if (active != null && active > 0) {
            return false;
        }
        LocalDateTime latest = jdbc.queryForObject("""
                        SELECT MAX(created_at)
                        FROM ops_reliable_task
                        WHERE scope_kind = 'PLATFORM'
                          AND task_type = ?
                          AND source_device_asset_id = ?
                        """,
                LocalDateTime.class,
                TASK_TYPE,
                asset.id());
        LocalDateTime now = databaseNow();
        if (latest != null
                && latest.plus(retryInterval).isAfter(now)) {
            return false;
        }

        UUID challengeUid = UUID.randomUUID();
        UUID commandUid = UUID.randomUUID();
        DeviceEntryUrlFactory.Entry deviceEntry =
                deviceEntryUrlFactory.create(asset.devicePublicCode());
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("challengeUid", challengeUid.toString());
        payload.put("expectedPortCount", asset.expectedPortCount());
        payload.put("deviceEntryUrl", deviceEntry.url());
        payload.put(
                "deviceEntryUrlSha256",
                deviceEntry.sha256Hex());

        Map<String, Object> target = new LinkedHashMap<>();
        target.put("type", TARGET_TYPE);
        target.put("uid", asset.hardwareSn());
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schemaVersion", 2);
        envelope.put("commandUid", commandUid.toString());
        envelope.put("commandType", TASK_TYPE);
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
        byte[] envelopeSha256 = canonicalizer.payloadSha256(envelope);
        taskRegistration.register(
                new ReliablePlatformDeviceControlTaskRegistration(
                        TASK_TYPE,
                        TASK_TYPE + ":" + challengeUid.toString()
                                .toUpperCase(),
                        TARGET_TYPE,
                        challengeUid.toString(),
                        taskRefFactory.issue(asset.id()),
                        2,
                        objectMapper.writeValueAsString(envelope),
                        envelopeSha256,
                        challengeUid,
                        commandUid,
                        1000));
        return true;
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
    }

    private static Duration positive(Duration value, String field) {
        if (value == null || value.isZero() || value.isNegative()) {
            throw new IllegalArgumentException(field + " must be positive");
        }
        return value;
    }

    private static String instant(LocalDateTime value) {
        return DateTimeFormatter.ISO_INSTANT.format(
                value.toInstant(ZoneOffset.UTC));
    }

    private record AssetCandidate(
            long id,
            String hardwareSn,
            String devicePublicCode,
            int expectedPortCount,
            String acceptanceStatus,
            String lifecycleStatus,
            String transportStatus) {
    }
}
