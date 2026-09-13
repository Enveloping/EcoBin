package org.enveloping.ecobin;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.simple.SimpleJdbcInsert;
import tools.jackson.databind.node.ObjectNode;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.UUID;

/** Synthetic original work; all real V79 CHECKs/FKs/triggers and runtime grants stay enabled. */
final class DeliveryIssueFullSchemaFixture {
    final JdbcTemplate jdbc;
    final LocalDateTime now;
    final String sn = "P1BM-" + UUID.randomUUID();
    final long tenant, organization, asset, session, command;

    DeliveryIssueFullSchemaFixture(JdbcTemplate jdbc, ObjectNode event, ObjectNode cloud) {
        this.jdbc = jdbc;
        now = jdbc.queryForObject("SELECT UTC_TIMESTAMP(3)", LocalDateTime.class).minusSeconds(2);
        tenant = insert("iam_tenant", "tenant_code", "issue-" + uid().substring(0, 12), "enterprise_name", "Issue fixture",
                "status", "ENABLED", "lock_version", 0, "created_at", now, "updated_at", now);
        organization = insert("iam_organization", "tenant_id", tenant, "organization_code", "issue-" + uid().substring(0, 12),
                "organization_name", "Issue fixture", "status", "ENABLED", "lock_version", 0,
                "created_at", now, "updated_at", now);
        long channel = insert("iam_miniapp_channel", "channel_uid", uid(), "appid", "wx" + uid().replace("-", "").substring(0, 16),
                "display_name", "Issue fixture", "login_enabled", 0, "lock_version", 0,
                "configured_at", now, "created_at", now, "updated_at", now);
        insert("iam_organization_miniapp_binding", "binding_uid", uid(), "tenant_id", tenant,
                "organization_id", organization, "miniapp_channel_id", channel, "status", "ACTIVE",
                "bound_at", now, "lock_version", 0, "created_at", now, "updated_at", now);
        long subject = insert("iam_wechat_subject", "wechat_subject_uid", uid(), "miniapp_channel_id", channel,
                "openid", "issue-" + uid(), "status", "ACTIVE", "auth_version", 0, "lock_version", 0,
                "created_at", now, "updated_at", now);
        long user = insert("iam_organization_user", "organization_user_uid", uid(), "tenant_id", tenant,
                "organization_id", organization, "miniapp_channel_id", channel, "wechat_subject_id", subject,
                "status", "ACTIVE", "auth_version", 0, "lock_version", 0, "registered_at", now,
                "last_login_at", now, "created_at", now, "updated_at", now);
        insert("fund_user_wallet", "wallet_uid", uid(), "tenant_id", tenant, "organization_id", organization,
                "organization_user_id", user, "available_balance_cent", 12345, "created_at", now, "updated_at", now);
        asset = insert("dev_device_asset", "asset_uid", uid(), "device_public_code", "Dv_" + uid().replace("-", ""),
                "hardware_sn", sn, "model_name", "Issue fixture", "expected_port_count", 2,
                "installation_display_name", "Local only", "installation_updated_at", now,
                "tenant_id", tenant, "tenant_assigned_at", now, "organization_id", organization,
                "organization_assigned_at", now, "created_at", now, "updated_at", now);
        long port = insert("dev_port", "tenant_id", tenant, "organization_id", organization,
                "asset_id", asset, "port_no", 2, "created_at", now);
        String bagCode = "issue-bag-" + uid();
        var p = cloud.path("payload");
        long bag = insert("rec_bag", "bag_uid", p.path("bagUid").asText(), "tenant_id", tenant,
                "organization_id", organization, "bag_code", bagCode, "registered_at", now, "created_at", now);
        byte[] content = HexFormat.of().parseHex(p.path("config").path("contentSha256").asText());
        byte[] mcu = HexFormat.of().parseHex(p.path("config").path("mcuPayloadSha256").asText());
        long config = insert("dev_config_version", "tenant_id", tenant, "organization_id", organization,
                "asset_id", asset, "version_no", 8, "schema_version", 2,
                "edge_heartbeat_interval_ms", 1000, "edge_heartbeat_miss_threshold", 3,
                "mcu_heartbeat_interval_ms", 1000, "mcu_heartbeat_miss_threshold", 3,
                "door_close_retry_limit", 0, "continue_delivery_wait_ms", 30000, "negative_weight_threshold_g", 500,
                "delivery_auto_close_ms", 120000, "weight_measurement_timeout_ms", 5000,
                "clean_solenoid_pulse_ms", 1000, "smoke_monitoring_enabled", 1, "content_sha256", content,
                "mcu_payload_sha256", mcu, "publication_source", "SYSTEM", "published_at", now, "created_at", now);
        BigDecimal price = new BigDecimal("0.4500");
        long portConfig = insert("dev_port_config_snapshot", "tenant_id", tenant, "organization_id", organization,
                "asset_id", asset, "config_version_id", config, "port_id", port, "display_name", "Local test",
                "business_enabled", 1, "unit_price_yuan_per_kg", price, "fullness_mode", "INFRARED_OR_WEIGHT",
                "configured_full_weight_g", 50000, "delivery_settle_delay_ms", 0, "fullness_settle_wait_ms", 0,
                "fullness_confirmation_wait_ms", 1000, "door_auto_close_timeout_ms", 120000,
                "weight_stable_window_ms", 1000, "weight_maximum_fluctuation_g", 100, "weight_required_sample_count", 5,
                "weight_measurement_timeout_ms", 5000, "weight_minimum_g", -1000, "weight_maximum_g", 350000,
                "calibration_version", 1, "infrared_sample_timeout_ms", 1000,
                "delivery_door_operation_timeout_ms", 30000, "created_at", now);
        long deliveryConfig = insert("rec_organization_delivery_config", "tenant_id", tenant,
                "organization_id", organization, "version_no", 1, "content_sha256", content,
                "review_mode", "NORMAL_AUTO_IMMEDIATE", "automatic_review_max_amount_cent", 100000, "open_balance_floor_cent", -1000,
                "max_review_abs_weight_g", 350000, "publication_source", "SYSTEM", "published_at", now, "created_at", now);
        session = insert("dev_delivery_session", "session_uid", event.path("payload").path("sessionUid").asText(),
                "tenant_id", tenant, "organization_id", organization, "asset_id", asset, "port_id", port,
                "organization_user_id", user, "device_config_version_id", config, "device_config_version_no", 8,
                "device_config_content_sha256", content, "device_config_mcu_payload_sha256", mcu,
                "port_config_snapshot_id", portConfig, "delivery_config_version_id", deliveryConfig,
                "delivery_config_content_sha256", content, "bag_id", bag, "bag_uid_snapshot", p.path("bagUid").asText(),
                "bag_code_snapshot", bagCode, "status", "IN_PROGRESS", "unit_price_yuan_per_kg", price,
                "open_balance_floor_cent", -1000, "max_review_abs_weight_g", 350000,
                "negative_weight_anomaly_threshold_g", 500, "local_end_selection_timeout_ms", 30000,
                "authorization_expires_at", now.plusMinutes(1), "result_recovery_deadline_at", now.plusHours(1),
                "first_edge_accepted_at", now, "first_physical_progress_at", now, "created_at", now, "updated_at", now);
        command = insert("dev_device_command", "command_uid", cloud.path("commandUid").asText(),
                "tenant_id", tenant, "organization_id", organization, "asset_id", asset,
                "command_type", "START_DELIVERY_SESSION", "delivery_session_id", session, "payload_schema_version", 2,
                "semantic_payload", p.toString(), "semantic_payload_sha256", HexFormat.of().parseHex(cloud.path("payloadSha256").asText()),
                "physical_state", "PHYSICAL_STARTED", "queued_at", now, "edge_accepted_at", now,
                "physical_started_at", now, "created_at", now, "updated_at", now);
        jdbc.update("""
                INSERT INTO dev_device_occupancy(asset_id,tenant_id,organization_id,occupancy_kind,delivery_session_id,acquired_at,lock_version)
                VALUES(?,?,?,'DELIVERY',?,?,0)
                """, asset, tenant, organization, session, now);
    }

    private long insert(String table, Object... fields) {
        var values = new LinkedHashMap<String, Object>();
        for (int i = 0; i < fields.length; i += 2) values.put((String)fields[i], fields[i + 1]);
        return new SimpleJdbcInsert(jdbc).withTableName(table).usingColumns(values.keySet().toArray(String[]::new))
                .usingGeneratedKeyColumns("id").executeAndReturnKey(values).longValue();
    }

    LaterDelivery seedLaterDeliveryWithAnotherBag() {
        var row = new LinkedHashMap<>(jdbc.queryForMap("SELECT * FROM dev_delivery_session WHERE id=?", session));
        row.remove("id"); row.put("session_uid", uid()); row.put("status", "IN_PROGRESS");
        row.put("ended_at", null); row.put("end_reason", null); row.put("lock_version", 0);
        String bagUid = uid(), bagCode = "later-bag-" + uid();
        long bag = insert("rec_bag", "bag_uid", bagUid, "tenant_id", tenant, "organization_id", organization,
                "bag_code", bagCode, "registered_at", now, "created_at", now);
        row.put("bag_id", bag); row.put("bag_uid_snapshot", bagUid); row.put("bag_code_snapshot", bagCode);
        long later = new SimpleJdbcInsert(jdbc).withTableName("dev_delivery_session")
                .usingColumns(row.keySet().toArray(String[]::new)).usingGeneratedKeyColumns("id")
                .executeAndReturnKey(row).longValue();
        jdbc.update("""
                INSERT INTO dev_device_occupancy(asset_id,tenant_id,organization_id,occupancy_kind,delivery_session_id,acquired_at,lock_version)
                VALUES(?,?,?,'DELIVERY',?,?,0)
                """, asset, tenant, organization, later, now);
        jdbc.update("""
                INSERT INTO rec_bag_current_occupancy(bag_id,tenant_id,organization_id,occupancy_type,port_id,acquired_at)
                VALUES(?,?,?,'PORT_BOUND',?,?)
                """, bag, tenant, organization, row.get("port_id"), now);
        return new LaterDelivery(later, bag);
    }

    record LaterDelivery(long sessionId, long bagId) {}

    private static String uid() { return UUID.randomUUID().toString(); }
}
