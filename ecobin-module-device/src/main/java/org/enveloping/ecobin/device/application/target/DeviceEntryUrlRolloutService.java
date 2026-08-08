package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistrationPort;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;

/** Creates one resumable platform rollout when the configured base URL changes. */
@Service
public class DeviceEntryUrlRolloutService {

    static final String TASK_TYPE = "SYNC_DEVICE_ENTRY_URL";
    static final String TARGET_TYPE = "DEVICE_ASSET";
    private static final int BATCH_SIZE = 100;

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final DeviceConfigurationCanonicalizer canonicalizer;
    private final DeviceEntryUrlFactory deviceEntryUrlFactory;
    private final PlatformDeviceAssetTaskRefFactory taskRefFactory;
    private final ReliablePlatformDeviceControlTaskRegistrationPort
            taskRegistration;
    private final boolean realExternalMode;

    public DeviceEntryUrlRolloutService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            DeviceConfigurationCanonicalizer canonicalizer,
            DeviceEntryUrlFactory deviceEntryUrlFactory,
            PlatformDeviceAssetTaskRefFactory taskRefFactory,
            ReliablePlatformDeviceControlTaskRegistrationPort
                    taskRegistration,
            @Value("${ecobin.external.mode:fake}") String externalMode) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.canonicalizer = canonicalizer;
        this.deviceEntryUrlFactory = deviceEntryUrlFactory;
        this.taskRefFactory = taskRefFactory;
        this.taskRegistration = taskRegistration;
        this.realExternalMode = "real".equalsIgnoreCase(externalMode);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public boolean reconcileNextBatch() {
        if (!realExternalMode) {
            return false;
        }
        LocalDateTime now = databaseNow();
        byte[] configuredSha256 = deviceEntryUrlFactory.baseUrlSha256();
        jdbc.update("""
                        INSERT IGNORE INTO dev_device_entry_url_rollout (
                            singleton_id, rollout_uid, base_url_sha256,
                            rollout_status, next_asset_id,
                            started_at, completed_at, updated_at
                        ) VALUES (1, ?, ?, 'PENDING', 0, ?, NULL, ?)
                        """,
                UUID.randomUUID().toString(),
                configuredSha256,
                now,
                now);
        Rollout rollout = jdbc.query("""
                        SELECT rollout_uid, base_url_sha256,
                               rollout_status, next_asset_id, started_at
                        FROM dev_device_entry_url_rollout
                        WHERE singleton_id = 1
                        FOR UPDATE
                        """,
                (rs, ignored) -> new Rollout(
                        UUID.fromString(rs.getString("rollout_uid")),
                        rs.getBytes("base_url_sha256"),
                        rs.getString("rollout_status"),
                        rs.getLong("next_asset_id"),
                        rs.getObject("started_at", LocalDateTime.class)))
                .stream().findFirst().orElseThrow();
        if (!Arrays.equals(
                rollout.baseUrlSha256(), configuredSha256)) {
            UUID rolloutUid = UUID.randomUUID();
            jdbc.update("""
                            UPDATE dev_device_entry_url_rollout
                            SET rollout_uid = ?, base_url_sha256 = ?,
                                rollout_status = 'PENDING', next_asset_id = 0,
                                started_at = ?, completed_at = NULL,
                                updated_at = ?
                            WHERE singleton_id = 1
                            """,
                    rolloutUid.toString(),
                    configuredSha256,
                    now,
                    now);
            rollout = new Rollout(
                    rolloutUid,
                    configuredSha256,
                    "PENDING",
                    0,
                    now);
        }
        if ("DONE".equals(rollout.status())) {
            return false;
        }

        List<Asset> assets = jdbc.query("""
                        SELECT id, hardware_sn, device_public_code,
                               lifecycle_status
                        FROM dev_device_asset
                        WHERE id > ?
                        ORDER BY id
                        LIMIT ?
                        """,
                (rs, ignored) -> new Asset(
                        rs.getLong("id"),
                        rs.getString("hardware_sn"),
                        rs.getString("device_public_code"),
                        rs.getString("lifecycle_status")),
                rollout.nextAssetId(),
                BATCH_SIZE);
        if (assets.isEmpty()) {
            jdbc.update("""
                            UPDATE dev_device_entry_url_rollout
                            SET rollout_status = 'DONE', completed_at = ?,
                                updated_at = ?
                            WHERE singleton_id = 1
                              AND rollout_uid = ?
                            """,
                    now,
                    now,
                    rollout.uid().toString());
            return false;
        }

        for (Asset asset : assets) {
            if (!"RETIRED".equals(asset.lifecycleStatus())) {
                register(asset, rollout);
            }
        }
        long nextAssetId = assets.getLast().id();
        jdbc.update("""
                        UPDATE dev_device_entry_url_rollout
                        SET next_asset_id = ?, updated_at = ?
                        WHERE singleton_id = 1
                          AND rollout_uid = ?
                        """,
                nextAssetId,
                now,
                rollout.uid().toString());
        return true;
    }

    private void register(Asset asset, Rollout rollout) {
        DeviceEntryUrlFactory.Entry entry =
                deviceEntryUrlFactory.create(asset.devicePublicCode());
        UUID commandUid = UUID.randomUUID();
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("deviceEntryUrl", entry.url());
        payload.put("deviceEntryUrlSha256", entry.sha256Hex());

        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schemaVersion", 2);
        envelope.put("commandUid", commandUid.toString());
        envelope.put("commandType", TASK_TYPE);
        envelope.put("targetDeviceName", asset.hardwareSn());
        envelope.put("target", Map.of(
                "type", TARGET_TYPE,
                "uid", asset.hardwareSn()));
        envelope.put("issuedAt", instant(rollout.startedAt()));
        envelope.put(
                "expiresAt",
                instant(rollout.startedAt().plusYears(10)));
        envelope.put("payloadSchemaVersion", 2);
        envelope.put(
                "payloadSha256",
                canonicalizer.hex(
                        canonicalizer.payloadSha256(payload)));
        envelope.put("payload", payload);
        envelope.put("cosGrant", null);
        byte[] envelopeSha256 = canonicalizer.payloadSha256(envelope);

        taskRegistration.register(
                new ReliablePlatformDeviceControlTaskRegistration(
                        TASK_TYPE,
                        TASK_TYPE + ":"
                                + rollout.uid().toString()
                                .toUpperCase(Locale.ROOT)
                                + ":" + asset.id(),
                        TARGET_TYPE,
                        asset.hardwareSn(),
                        taskRefFactory.issue(asset.id()),
                        2,
                        objectMapper.writeValueAsString(envelope),
                        envelopeSha256,
                        rollout.uid(),
                        commandUid,
                        1000));
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
    }

    private static String instant(LocalDateTime value) {
        return DateTimeFormatter.ISO_INSTANT.format(
                value.toInstant(ZoneOffset.UTC));
    }

    private record Rollout(
            UUID uid,
            byte[] baseUrlSha256,
            String status,
            long nextAssetId,
            LocalDateTime startedAt) {

        private Rollout {
            baseUrlSha256 = Arrays.copyOf(
                    baseUrlSha256, baseUrlSha256.length);
        }

        @Override
        public byte[] baseUrlSha256() {
            return Arrays.copyOf(
                    baseUrlSha256, baseUrlSha256.length);
        }
    }

    private record Asset(
            long id,
            String hardwareSn,
            String devicePublicCode,
            String lifecycleStatus) {
    }
}
