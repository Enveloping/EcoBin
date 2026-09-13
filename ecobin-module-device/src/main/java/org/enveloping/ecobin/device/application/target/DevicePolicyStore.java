package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.web.v1.DeviceModels.DevicePolicyReleaseRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DevicePolicyValues;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DevicePolicyView;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.springframework.jdbc.core.JdbcTemplate;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/** Database-owned policies; all mutations run inside the application's audited transaction. */
final class DevicePolicyStore {
    private final JdbcTemplate jdbc;
    private final DevicePolicyProvider provider;
    static final String ELIGIBLE = "asset.organization_id IS NOT NULL AND asset.tenant_id IS NOT NULL "
            + "AND asset.lifecycle_status = 'NORMAL' AND asset.acceptance_status = 'PASSED'";
    static final String INHERITING = "NOT EXISTS (SELECT 1 FROM dev_tenant_device_policy tenant_policy "
            + "WHERE tenant_policy.tenant_id = asset.tenant_id AND tenant_policy.configuration_mode = 'CUSTOM')";

    DevicePolicyStore(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
        this.provider = new DevicePolicyProvider(jdbc);
    }

    Row row(Long tenantId, boolean lock) {
        if (lock) {
            // Lock only the policy. Locking the joined publisher account would
            // invert the actor -> policy order used by authorization.
            jdbc.queryForList("SELECT policy_version FROM "
                            + (tenantId == null ? "dev_device_default_policy WHERE singleton_id = 1" : "dev_tenant_device_policy WHERE tenant_id = ?")
                            + " FOR UPDATE", Long.class, tenantId == null ? new Object[0] : new Object[]{tenantId});
        }
        String sql = tenantId == null ? """
                SELECT policy.*, 'DEFAULT' configuration_mode, admin.display_name updated_by
                FROM dev_device_default_policy policy
                LEFT JOIN iam_platform_admin admin ON admin.id = policy.updated_by_platform_admin_id
                WHERE singleton_id = 1
                """ : """
                SELECT policy.*, 'STAFF' publication_source, staff.display_name updated_by
                FROM dev_tenant_device_policy policy
                JOIN iam_staff_account staff ON staff.tenant_id = policy.tenant_id AND staff.id = policy.updated_by_staff_account_id
                WHERE policy.tenant_id = ?
                """;
        return jdbc.query(sql, (rs, ignored) -> new Row(
                tenantId, rs.getLong("policy_version"), rs.getString("configuration_mode"),
                UUID.fromString(rs.getString("rollout_uid")), rs.getString("rollout_status"),
                rs.getLong("next_asset_id"), rs.getLong("target_asset_count"), rs.getLong("processed_asset_count"),
                rs.getLong("published_asset_count"), rs.getString("publication_source"), rs.getString("updated_by"),
                rs.getString("change_reason"), rs.getTimestamp("updated_at").toInstant()),
                tenantId == null ? new Object[0] : new Object[]{tenantId}).stream().findFirst().orElse(null);
    }

    DevicePolicyView view(Long tenantId) {
        Row defaults = row(null, false);
        Row tenant = tenantId == null ? null : row(tenantId, false);
        Row metadata = tenant == null ? defaults : tenant;
        if (tenant != null && "INHERIT".equals(tenant.mode()) && defaults.updatedAt().isAfter(tenant.updatedAt())) {
            metadata = defaults;
        }
        var effective = provider.current(tenantId);
        var defaultValues = provider.current(null);
        // Query actual latest versions, not the rollout cursor: another publisher
        // or automatic activation may already have generated a matching version.
        String matches = "COALESCE(config.device_default_policy_version_no, 0) = ? AND COALESCE(config.tenant_device_policy_version_no, 0) = ?";
        String sql = """
                SELECT COUNT(*) total_count,
                  COALESCE(SUM(CASE WHEN NOT (%s) THEN 1 ELSE 0 END), 0) unpublished_count,
                  COALESCE(SUM(CASE WHEN NOT (%s) THEN 1 WHEN task.state = 'BLOCKED' THEN 0
                    WHEN application.status IN ('EDGE_SAVED', 'APPLIED', 'FAILED') THEN 0 ELSE 1 END), 0) pending_count,
                  COALESCE(SUM(CASE WHEN (%s) AND COALESCE(task.state, '') <> 'BLOCKED' AND application.status = 'EDGE_SAVED' THEN 1 ELSE 0 END), 0) saved_count,
                  COALESCE(SUM(CASE WHEN (%s) AND COALESCE(task.state, '') <> 'BLOCKED' AND application.status = 'APPLIED' THEN 1 ELSE 0 END), 0) applied_count,
                  COALESCE(SUM(CASE WHEN (%s) AND COALESCE(task.state, '') <> 'BLOCKED' AND application.status = 'FAILED' THEN 1 ELSE 0 END), 0) failed_count,
                  COALESCE(SUM(CASE WHEN (%s) AND task.state = 'BLOCKED' THEN 1 ELSE 0 END), 0) blocked_count
                FROM dev_device_asset asset
                LEFT JOIN dev_config_version config ON config.id = (
                    SELECT candidate.id FROM dev_config_version candidate
                    WHERE candidate.asset_id = asset.id AND candidate.tenant_id = asset.tenant_id AND candidate.organization_id = asset.organization_id
                    ORDER BY candidate.version_no DESC LIMIT 1)
                LEFT JOIN dev_config_application application ON application.config_version_id = config.id AND application.asset_id = asset.id
                LEFT JOIN ops_reliable_task task ON task.task_type = 'ENSURE_DEVICE_CONFIGURATION'
                    AND task.target_type = 'CONFIGURATION_APPLICATION' AND task.target_stable_key = application.application_uid
                WHERE %s AND %s
                """.formatted(matches, matches, matches, matches, matches, matches, ELIGIBLE,
                tenantId == null ? INHERITING : "asset.tenant_id = ?");
        var args = new java.util.ArrayList<Object>();
        for (int i = 0; i < 6; i++) {
            args.add(effective.defaultVersion() == null ? 0 : effective.defaultVersion());
            args.add(effective.tenantVersion() == null ? 0 : effective.tenantVersion());
        }
        if (tenantId != null) args.add(tenantId);
        long[] counts = jdbc.queryForObject(sql, (rs, ignored) -> new long[]{rs.getLong("total_count"),
                rs.getLong("unpublished_count"), rs.getLong("pending_count"), rs.getLong("saved_count"),
                rs.getLong("applied_count"), rs.getLong("failed_count"), rs.getLong("blocked_count")}, args.toArray());
        return new DevicePolicyView(tenantId == null ? defaults.version() : tenant == null ? 0 : tenant.version(),
                defaults.version(), tenantId == null ? "DEFAULT" : tenant == null ? "INHERIT" : tenant.mode(),
                effective.price(), effective.mode(), effective.weightKg(), effective.negativeThreshold(),
                new DevicePolicyValues(defaultValues.price(), defaultValues.mode(), defaultValues.weightKg(), defaultValues.negativeThreshold()),
                metadata.publicationSource(), metadata.updatedBy() == null ? "系统" : metadata.updatedBy(), metadata.reason(), metadata.updatedAt(),
                metadata.rolloutUid(), counts[1] == 0 ? "DONE" : "RUNNING", counts[0],
                counts[0] - counts[1], counts[0] - counts[1], counts[2], counts[3], counts[4], counts[5], counts[6]);
    }

    void save(Long tenantId, long actorId, DevicePolicyReleaseRequest request) {
        // Serialize policy writers in this order, including creation of a missing
        // tenant row. No device locks are taken while editing a policy.
        Row defaults = row(null, true);
        Row before = tenantId == null ? defaults : row(tenantId, true);
        long version = before == null ? 0 : before.version();
        if (version != request.expectedVersion() || defaults.version() != request.expectedDefaultVersion()) {
            throw new TargetApiException(409, "COMMON.VERSION_CONFLICT", "设备配置或平台默认值已变化，请刷新后重试", true,
                    Map.of("currentVersion", version, "currentDefaultVersion", defaults.version()));
        }
        var current = provider.current(tenantId);
        String currentMode = before == null ? "INHERIT" : before.mode();
        if (currentMode.equals(request.configurationMode())
                && ("INHERIT".equals(currentMode)
                    || (current.price().equals(request.unitPriceYuanPerKg()) && current.mode().equals(request.fullnessMode())
                        && current.weightKg().equals(request.fullnessWeightKg()) && current.negativeThreshold() == request.negativeWeightThresholdGram()))) {
            throw new TargetApiException(422, "DEVICE.CONFIGURATION_POLICY_UNCHANGED", "设备配置没有变化", false, Map.of());
        }
        if (version >= 9007199254740991L) throw new TargetApiException(409, "COMMON.VERSION_CONFLICT", "设备配置版本已达到上限", false, Map.of());
        boolean inherit = "INHERIT".equals(request.configurationMode());
        UUID rollout = UUID.randomUUID();
        Object price = inherit ? null : request.unitPriceYuanPerKg();
        Object mode = inherit ? null : request.fullnessMode();
        Object weight = inherit ? null : request.fullnessWeightKg();
        Object negative = inherit ? null : request.negativeWeightThresholdGram();
        long count = count(tenantId);
        if (tenantId == null) {
            jdbc.update("""
                    UPDATE dev_device_default_policy SET policy_version = policy_version + 1,
                      unit_price_yuan_per_kg = ?, fullness_mode = ?, fullness_weight_kg = ?, negative_weight_threshold_g = ?,
                      rollout_uid = ?, rollout_status = 'PENDING', next_asset_id = 0, target_asset_count = ?,
                      processed_asset_count = 0, published_asset_count = 0, publication_source = 'PLATFORM_ADMIN',
                      updated_by_platform_admin_id = ?, change_reason = ?, started_at = UTC_TIMESTAMP(3),
                      completed_at = NULL, updated_at = UTC_TIMESTAMP(3), lock_version = lock_version + 1
                    WHERE singleton_id = 1
                    """, price, mode, weight, negative, rollout.toString(), count, actorId, request.reason());
        } else if (before == null) {
            jdbc.update("""
                    INSERT INTO dev_tenant_device_policy (tenant_id, policy_version, configuration_mode,
                      unit_price_yuan_per_kg, fullness_mode, fullness_weight_kg, negative_weight_threshold_g,
                      rollout_uid, rollout_status, target_asset_count, updated_by_staff_account_id, change_reason,
                      started_at, updated_at)
                    VALUES (?, 1, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3))
                    """, tenantId, request.configurationMode(), price, mode, weight, negative, rollout.toString(), count, actorId, request.reason());
        } else {
            jdbc.update("""
                    UPDATE dev_tenant_device_policy SET policy_version = policy_version + 1, configuration_mode = ?,
                      unit_price_yuan_per_kg = ?, fullness_mode = ?, fullness_weight_kg = ?, negative_weight_threshold_g = ?,
                      rollout_uid = ?, rollout_status = 'PENDING', next_asset_id = 0, target_asset_count = ?,
                      processed_asset_count = 0, published_asset_count = 0, updated_by_staff_account_id = ?,
                      change_reason = ?, started_at = UTC_TIMESTAMP(3), completed_at = NULL,
                      updated_at = UTC_TIMESTAMP(3), lock_version = lock_version + 1 WHERE tenant_id = ?
                    """, request.configurationMode(), price, mode, weight, negative, rollout.toString(), count, actorId, request.reason(), tenantId);
        }
    }

    private long count(Long tenantId) {
        return jdbc.queryForObject("SELECT COUNT(*) FROM dev_device_asset asset WHERE " + ELIGIBLE + " AND "
                        + (tenantId == null ? INHERITING : "asset.tenant_id = ?"), Long.class,
                tenantId == null ? new Object[0] : new Object[]{tenantId});
    }

    Row nextRollout() {
        Row defaults = row(null, true);
        if (!"DONE".equals(defaults.status())) return defaults;
        List<Long> tenants = jdbc.queryForList("SELECT tenant_id FROM dev_tenant_device_policy WHERE rollout_status <> 'DONE' ORDER BY started_at, tenant_id LIMIT 1", Long.class);
        return tenants.isEmpty() ? null : row(tenants.getFirst(), true);
    }

    List<Long> nextAssets(Row row) {
        String query = "SELECT asset.id FROM dev_device_asset asset WHERE " + ELIGIBLE + " AND asset.id > ? AND "
                + (row.tenantId() == null ? INHERITING : "asset.tenant_id = ?") + " ORDER BY asset.id LIMIT 100";
        return jdbc.queryForList(query, Long.class, row.tenantId() == null ? new Object[]{row.cursor()} : new Object[]{row.cursor(), row.tenantId()});
    }

    void advance(Row row, List<Long> assets, long published) {
        boolean completed = assets.size() < 100;
        jdbc.update("UPDATE " + (row.tenantId() == null ? "dev_device_default_policy" : "dev_tenant_device_policy")
                        + " SET next_asset_id = ?, processed_asset_count = processed_asset_count + ?, published_asset_count = published_asset_count + ?,"
                        + " target_asset_count = GREATEST(target_asset_count, ?), rollout_status = ?, completed_at = "
                        + (completed ? "UTC_TIMESTAMP(3)" : "NULL") + " WHERE " + (row.tenantId() == null ? "singleton_id" : "tenant_id") + " = ? AND rollout_uid = ?",
                assets.isEmpty() ? row.cursor() : assets.getLast(), assets.size(), published, row.processed() + assets.size(),
                completed ? "DONE" : "RUNNING", row.tenantId() == null ? 1 : row.tenantId(), row.rolloutUid().toString());
    }

    record Row(Long tenantId, long version, String mode, UUID rolloutUid, String status, long cursor,
               long target, long processed, long published, String publicationSource, String updatedBy, String reason, Instant updatedAt) { }
}
