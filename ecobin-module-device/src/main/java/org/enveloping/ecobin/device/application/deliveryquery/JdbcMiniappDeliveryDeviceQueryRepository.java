package org.enveloping.ecobin.device.application.deliveryquery;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Repository
class JdbcMiniappDeliveryDeviceQueryRepository
        implements MiniappDeliveryDeviceQueryRepository {

    /*
     * Configuration eligibility comes from the trusted configuration-progress
     * projection. MCU-link and UART transport diagnostics are intentionally
     * not selected. The mcu-payload digest below is only part of immutable
     * configuration identity; it is not a live Orange Pi-to-MCU health check.
     */
    static final String FIND_CURRENT_ASSET_SQL = """
            SELECT asset.id AS asset_id,
                   asset.device_public_code,
                   asset.lifecycle_status,
                   asset.acceptance_status,
                   CASE
                       WHEN occupancy.asset_id IS NULL THEN 0
                       ELSE 1
                   END AS device_busy,
                   configuration.id AS configuration_id,
                   configuration.version_no AS configuration_version,
                   asset.installation_display_name AS device_display_name,
                   asset.installation_address AS location_address,
                   configuration.edge_heartbeat_interval_ms,
                   configuration.edge_heartbeat_miss_threshold,
                   configuration.content_sha256
                       AS configuration_content_sha256,
                   configuration.mcu_payload_sha256
                       AS configuration_mcu_payload_sha256,
                   application.status
                       AS configuration_application_status,
                   application.reported_version_no
                       AS application_reported_version,
                   application.reported_content_sha256
                       AS application_reported_content_sha256,
                   application.reported_mcu_payload_sha256
                       AS application_reported_mcu_payload_sha256,
                   application.applied_at AS configuration_applied_at,
                   COALESCE(
                       transport.onenet_connection_status,
                       'UNKNOWN'
                   ) AS edge_connection_status,
                   runtime.safety_status,
                   runtime.local_storage_health,
                   runtime.local_storage_state,
                   runtime.trusted_runtime_edge_event_id,
                   runtime.trusted_runtime_edge_event_type,
                   runtime.trusted_runtime_sequence,
                   runtime.trusted_runtime_received_at,
                   runtime.applied_config_version_no
                       AS progress_applied_configuration_version,
                   runtime.applied_config_content_sha256
                       AS progress_applied_configuration_content_sha256,
                   runtime.applied_mcu_payload_sha256
                       AS progress_applied_configuration_mcu_payload_sha256,
                   management.architecture_generation
                       AS management_architecture_generation,
                   compatibility.business_admission_status
            FROM dev_device_asset asset
            JOIN iam_tenant tenant
              ON tenant.id = asset.tenant_id
            JOIN iam_organization organization
              ON organization.tenant_id = asset.tenant_id
             AND organization.id = asset.organization_id
            LEFT JOIN dev_device_transport_state transport
              ON transport.asset_id = asset.id
            LEFT JOIN dev_device_occupancy occupancy
              ON occupancy.asset_id = asset.id
             AND occupancy.tenant_id = asset.tenant_id
             AND occupancy.organization_id = asset.organization_id
            LEFT JOIN dev_device_management_profile management
              ON management.asset_id = asset.id
            LEFT JOIN dev_device_compatibility_projection compatibility
              ON compatibility.asset_id = asset.id
            LEFT JOIN dev_config_version configuration
              ON configuration.id = (
                  SELECT latest.id
                  FROM dev_config_version latest
                  WHERE latest.tenant_id = asset.tenant_id
                    AND latest.organization_id =
                        asset.organization_id
                    AND latest.asset_id = asset.id
                  ORDER BY latest.version_no DESC
                  LIMIT 1
              )
            LEFT JOIN dev_config_application application
              ON application.tenant_id = asset.tenant_id
             AND application.organization_id =
                 asset.organization_id
             AND application.asset_id = asset.id
             AND application.config_version_id = configuration.id
            LEFT JOIN dev_device_runtime_state runtime
              ON runtime.tenant_id = asset.tenant_id
             AND runtime.organization_id =
                 asset.organization_id
             AND runtime.asset_id = asset.id
            WHERE asset.tenant_id = ?
              AND asset.organization_id = ?
              AND asset.device_public_code = ?
              AND asset.lifecycle_status = 'NORMAL'
              AND asset.acceptance_status = 'PASSED'
              AND tenant.status = 'ENABLED'
              AND organization.status = 'ENABLED'
            """;

    /*
     * Runtime fields are returned for diagnostics only. They do not decide
     * whether the backend may create a delivery task.
     */
    static final String FIND_PORTS_SQL = """
            SELECT port.id AS port_id,
                   port.port_no,
                   configuration.display_name,
                   configuration.business_enabled,
                   configuration.unit_price_yuan_per_kg,
                   configuration.fullness_mode,
                   configuration.calibration_version
                       AS configured_calibration_version,
                   runtime.delivery_door_actuator_health,
                   runtime.clean_lock_power_state,
                   runtime.clean_solenoid_health,
                   runtime.weight_sensor_health,
                   runtime.weight_measurement_status,
                   runtime.weight_value_available,
                   runtime.reported_weight_grams,
                   runtime.weight_value_kind,
                   runtime.calibration_version
                       AS runtime_calibration_version,
                   runtime.smoke_state,
                   runtime.smoke_sensor_health,
                   runtime.runtime_fault_bitmap,
                   runtime.safety_status,
                   runtime.pending_delivery_result_session_id,
                   runtime.trusted_runtime_edge_event_id,
                   runtime.trusted_runtime_edge_event_type,
                   runtime.trusted_runtime_sequence
            FROM dev_port port
            LEFT JOIN dev_port_config_snapshot configuration
              ON configuration.tenant_id = port.tenant_id
             AND configuration.organization_id = port.organization_id
             AND configuration.asset_id = port.asset_id
             AND configuration.port_id = port.id
             AND configuration.config_version_id = ?
            LEFT JOIN dev_port_runtime_state runtime
              ON runtime.tenant_id = port.tenant_id
             AND runtime.organization_id = port.organization_id
             AND runtime.asset_id = port.asset_id
             AND runtime.port_id = port.id
            WHERE port.tenant_id = ?
              AND port.organization_id = ?
              AND port.asset_id = ?
            ORDER BY port.port_no
            """;

    static final String FIND_OWNED_SESSION_SQL = """
            SELECT delivery_session.id AS session_id,
                   delivery_session.session_uid,
                   delivery_session.status AS device_status,
                   asset.device_public_code,
                   port.port_no,
                   delivery_session.first_physical_progress_at,
                   delivery_session.device_completed_at,
                   delivery_session.ended_at,
                   delivery_session.end_reason
            FROM dev_delivery_session delivery_session
            JOIN dev_device_asset asset
              ON asset.tenant_id = delivery_session.tenant_id
             AND asset.organization_id =
                 delivery_session.organization_id
             AND asset.id = delivery_session.asset_id
            JOIN dev_port port
              ON port.tenant_id = delivery_session.tenant_id
             AND port.organization_id =
                 delivery_session.organization_id
             AND port.asset_id =
                 delivery_session.asset_id
             AND port.id = delivery_session.port_id
            WHERE delivery_session.tenant_id = ?
              AND delivery_session.organization_id = ?
              AND delivery_session.organization_user_id = ?
              AND delivery_session.session_uid = ?
            """;

    private final JdbcTemplate jdbc;

    JdbcMiniappDeliveryDeviceQueryRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public LocalDateTime databaseNow() {
        LocalDateTime result = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)",
                LocalDateTime.class);
        if (result == null) {
            throw new IllegalStateException(
                    "database time is unavailable");
        }
        return result;
    }

    @Override
    public Optional<AssetSnapshotRow> findAsset(
            long tenantId,
            long organizationId,
            String deviceCode) {
        return jdbc.query(
                FIND_CURRENT_ASSET_SQL,
                (rs, ignored) -> asset(rs),
                tenantId,
                organizationId,
                deviceCode).stream().findFirst();
    }

    @Override
    public List<PortSnapshotRow> findPorts(
            long tenantId,
            long organizationId,
            long assetId,
            Long configurationId) {
        return jdbc.query(
                FIND_PORTS_SQL,
                (rs, ignored) -> port(rs),
                configurationId,
                tenantId,
                organizationId,
                assetId);
    }

    @Override
    public Optional<OwnedSessionRow> findOwnedSession(
            long tenantId,
            long organizationId,
            long organizationUserId,
            UUID sessionUid) {
        return jdbc.query(
                FIND_OWNED_SESSION_SQL,
                (rs, ignored) -> new OwnedSessionRow(
                        rs.getLong("session_id"),
                        UUID.fromString(rs.getString("session_uid")),
                        rs.getString("device_status"),
                        rs.getString("device_public_code"),
                        rs.getInt("port_no"),
                        rs.getObject(
                                "first_physical_progress_at",
                                LocalDateTime.class),
                        rs.getObject(
                                "device_completed_at",
                                LocalDateTime.class),
                        rs.getObject(
                                "ended_at",
                                LocalDateTime.class),
                        rs.getString("end_reason")),
                tenantId,
                organizationId,
                organizationUserId,
                sessionUid.toString()).stream().findFirst();
    }

    private static AssetSnapshotRow asset(ResultSet rs)
            throws SQLException {
        return new AssetSnapshotRow(
                rs.getLong("asset_id"),
                rs.getString("device_public_code"),
                rs.getString("acceptance_status"),
                rs.getString("lifecycle_status"),
                rs.getBoolean("device_busy"),
                nullableLong(rs, "configuration_id"),
                nullableLong(rs, "configuration_version"),
                rs.getString("device_display_name"),
                rs.getString("location_address"),
                nullableLong(rs, "edge_heartbeat_interval_ms"),
                nullableLong(rs, "edge_heartbeat_miss_threshold"),
                rs.getBytes("configuration_content_sha256"),
                rs.getBytes("configuration_mcu_payload_sha256"),
                rs.getString("configuration_application_status"),
                nullableLong(rs, "application_reported_version"),
                rs.getBytes("application_reported_content_sha256"),
                rs.getBytes(
                        "application_reported_mcu_payload_sha256"),
                rs.getObject(
                        "configuration_applied_at",
                        LocalDateTime.class),
                rs.getString("edge_connection_status"),
                rs.getString("safety_status"),
                rs.getString("local_storage_health"),
                rs.getString("local_storage_state"),
                nullableLong(rs, "trusted_runtime_edge_event_id"),
                rs.getString("trusted_runtime_edge_event_type"),
                nullableLong(rs, "trusted_runtime_sequence"),
                rs.getObject(
                        "trusted_runtime_received_at",
                        LocalDateTime.class),
                nullableLong(
                        rs,
                        "progress_applied_configuration_version"),
                rs.getBytes(
                        "progress_applied_configuration_content_sha256"),
                rs.getBytes(
                        "progress_applied_configuration_mcu_payload_sha256"),
                rs.getString(
                        "management_architecture_generation"),
                rs.getString("business_admission_status"));
    }

    private static PortSnapshotRow port(ResultSet rs)
            throws SQLException {
        return new PortSnapshotRow(
                rs.getLong("port_id"),
                rs.getInt("port_no"),
                rs.getString("display_name"),
                nullableBoolean(rs, "business_enabled"),
                rs.getBigDecimal("unit_price_yuan_per_kg"),
                rs.getString("fullness_mode"),
                nullableLong(rs, "configured_calibration_version"),
                rs.getString("delivery_door_actuator_health"),
                rs.getString("clean_lock_power_state"),
                rs.getString("clean_solenoid_health"),
                rs.getString("weight_sensor_health"),
                rs.getString("weight_measurement_status"),
                nullableBoolean(rs, "weight_value_available"),
                nullableLong(rs, "reported_weight_grams"),
                rs.getString("weight_value_kind"),
                nullableLong(rs, "runtime_calibration_version"),
                rs.getString("smoke_state"),
                rs.getString("smoke_sensor_health"),
                nullableLong(rs, "runtime_fault_bitmap"),
                rs.getString("safety_status"),
                nullableLong(rs, "pending_delivery_result_session_id"),
                nullableLong(rs, "trusted_runtime_edge_event_id"),
                rs.getString("trusted_runtime_edge_event_type"),
                nullableLong(rs, "trusted_runtime_sequence"));
    }

    private static Long nullableLong(ResultSet rs, String column)
            throws SQLException {
        long value = rs.getLong(column);
        return rs.wasNull() ? null : value;
    }

    private static Boolean nullableBoolean(
            ResultSet rs,
            String column) throws SQLException {
        boolean value = rs.getBoolean(column);
        return rs.wasNull() ? null : value;
    }
}
