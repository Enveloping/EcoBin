package org.enveloping.ecobin.device.application.startdelivery;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.PreparedStatementCreator;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.stereotype.Repository;

import java.sql.PreparedStatement;
import java.sql.Statement;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

@Repository
class JdbcStartDeliveryDeviceRepository
        implements StartDeliveryDeviceRepository {

    static final String LOCK_ACTIVE_SESSION_SQL = """
            SELECT id
            FROM dev_delivery_session
            WHERE tenant_id = ?
              AND organization_id = ?
              AND organization_user_id = ?
              AND status IN (
                  'PREPARED',
                  'AUTHORIZATION_QUEUED',
                  'IN_PROGRESS',
                  'RESULT_PENDING_RECOVERY'
              )
              AND offline_occupancy_released_at IS NULL
            ORDER BY id
            FOR UPDATE
            """;

    static final String LOCK_ASSET_SQL = """
            SELECT asset.id,
                   asset.hardware_sn,
                   asset.device_public_code,
                   asset.lifecycle_status,
                   asset.acceptance_status,
                   asset.tenant_id,
                   asset.organization_id,
                   asset.expected_port_count,
                   management.architecture_generation
                       AS management_architecture_generation,
                   compatibility.business_admission_status
            FROM dev_device_asset asset
            LEFT JOIN dev_device_management_profile management
              ON management.asset_id = asset.id
            LEFT JOIN dev_device_compatibility_projection compatibility
              ON compatibility.asset_id = asset.id
            WHERE asset.device_public_code = ?
            FOR UPDATE
            """;

    static final String LOCK_TENANT_SQL = """
            SELECT id, status
            FROM iam_tenant
            WHERE id = ?
            FOR UPDATE
            """;

    static final String LOCK_ORGANIZATION_SQL = """
            SELECT id, status
            FROM iam_organization
            WHERE tenant_id = ?
              AND id = ?
            FOR UPDATE
            """;

    static final String LOCK_TRANSPORT_PRESENCE_SQL = """
            SELECT onenet_connection_status
            FROM dev_device_transport_state
            WHERE asset_id = ?
            FOR UPDATE
            """;

    static final String LOCK_OCCUPANCY_SQL = """
            SELECT occupancy_kind
            FROM dev_device_occupancy
            WHERE asset_id = ?
            FOR UPDATE
            """;

    static final String LOCK_RELEASED_PENDING_DELIVERY_SQL = """
            SELECT id
            FROM dev_delivery_session
            WHERE asset_id = ?
              AND offline_occupancy_released_at IS NOT NULL
              AND ended_at IS NULL
              AND status IN (
                  'PREPARED',
                  'AUTHORIZATION_QUEUED',
                  'IN_PROGRESS',
                  'RESULT_PENDING_RECOVERY'
              )
            ORDER BY id
            FOR UPDATE
            """;

    // Configuration versions are append-only. LOCK_ASSET_SQL already
    // serializes delivery admission with configuration publication, so this
    // read must not request an update lock from the least-privilege runtime
    // account.
    static final String LOCK_LATEST_CONFIGURATION_SQL = """
            SELECT config.id,
                   config.version_no,
                   config.content_sha256,
                   config.mcu_payload_sha256,
                   config.edge_heartbeat_interval_ms,
                   config.edge_heartbeat_miss_threshold,
                   config.continue_delivery_wait_ms,
                   config.negative_weight_threshold_g,
                   config.delivery_auto_close_ms,
                   EXISTS (
                       SELECT 1
                       FROM dev_config_application application
                       WHERE application.tenant_id = config.tenant_id
                         AND application.organization_id = config.organization_id
                         AND application.asset_id = config.asset_id
                         AND application.config_version_id = config.id
                         AND application.status = 'APPLIED'
                         AND application.reported_version_no = config.version_no
                         AND application.reported_content_sha256 = config.content_sha256
                         AND application.reported_mcu_payload_sha256 = config.mcu_payload_sha256
                   ) AS application_applied,
                   EXISTS (
                       SELECT 1
                       FROM dev_device_runtime_state runtime
                       WHERE runtime.asset_id = config.asset_id
                         AND runtime.tenant_id = config.tenant_id
                         AND runtime.organization_id = config.organization_id
                         AND runtime.applied_config_version_no = config.version_no
                         AND runtime.applied_config_content_sha256 = config.content_sha256
                         AND runtime.applied_mcu_payload_sha256 = config.mcu_payload_sha256
                   ) AS runtime_applied
            FROM dev_config_version config
            WHERE config.tenant_id = ?
              AND config.organization_id = ?
              AND config.asset_id = ?
            ORDER BY config.version_no DESC
            LIMIT 1
            """;

    static final String LOCK_PORT_SQL = """
            SELECT id, port_no
            FROM dev_port
            WHERE tenant_id = ?
              AND organization_id = ?
              AND asset_id = ?
              AND port_no = ?
            """;

    static final String LOCK_PORT_CONFIGURATION_SQL = """
            SELECT id, business_enabled, unit_price_yuan_per_kg,
                   fullness_mode, calibration_version
            FROM dev_port_config_snapshot
            WHERE tenant_id = ?
              AND organization_id = ?
              AND asset_id = ?
              AND config_version_id = ?
              AND port_id = ?
            """;

    static final String INSERT_SESSION_SQL = """
            INSERT INTO dev_delivery_session (
                session_uid,
                tenant_id,
                organization_id,
                asset_id,
                port_id,
                organization_user_id,
                device_config_version_id,
                device_config_version_no,
                device_config_content_sha256,
                device_config_mcu_payload_sha256,
                port_config_snapshot_id,
                delivery_config_version_id,
                delivery_config_content_sha256,
                bag_id,
                bag_uid_snapshot,
                bag_code_snapshot,
                status,
                unit_price_yuan_per_kg,
                open_balance_floor_cent,
                max_review_abs_weight_g,
                negative_weight_anomaly_threshold_g,
                local_end_selection_timeout_ms,
                authorization_expires_at,
                result_recovery_deadline_at,
                first_edge_accepted_at,
                first_physical_progress_at,
                device_completed_at,
                ended_at,
                end_reason,
                lock_version,
                created_at,
                updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                'AUTHORIZATION_QUEUED',
                ?, ?, ?, ?, ?,
                ?, ?,
                NULL, NULL, NULL, NULL, NULL,
                0, ?, ?
            )
            """;

    static final String INSERT_OCCUPANCY_SQL = """
            INSERT INTO dev_device_occupancy (
                asset_id,
                tenant_id,
                organization_id,
                occupancy_kind,
                delivery_session_id,
                clean_operation_id,
                acquired_at,
                lock_version
            ) VALUES (
                ?, ?, ?,
                'DELIVERY', ?, NULL, ?, 0
            )
            """;

    static final String INSERT_COMMAND_SQL = """
            INSERT INTO dev_device_command (
                command_uid,
                tenant_id,
                organization_id,
                asset_id,
                command_type,
                delivery_session_id,
                clean_operation_id,
                config_application_id,
                fullness_detection_id,
                baseline_measurement_id,
                payload_schema_version,
                semantic_payload,
                semantic_payload_sha256,
                physical_state,
                queued_at,
                edge_accepted_at,
                physical_started_at,
                physical_ended_at,
                lock_version,
                created_at,
                updated_at
            ) VALUES (
                ?, ?, ?, ?,
                'START_DELIVERY_SESSION',
                ?, NULL, NULL, NULL, NULL,
                2, CAST(? AS JSON), ?,
                'QUEUED', ?,
                NULL, NULL, NULL,
                0, ?, ?
            )
            """;

    private final JdbcTemplate jdbc;

    JdbcStartDeliveryDeviceRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public LocalDateTime databaseNow() {
        LocalDateTime result = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)",
                LocalDateTime.class);
        if (result == null) {
            throw invariant("database time is unavailable");
        }
        return result;
    }

    @Override
    public List<Long> lockActiveSessionIds(
            long tenantId,
            long organizationId,
            long organizationUserId) {
        return jdbc.query(
                LOCK_ACTIVE_SESSION_SQL,
                (rs, ignored) -> rs.getLong("id"),
                tenantId,
                organizationId,
                organizationUserId);
    }

    @Override
    public Optional<AssetRow> lockAssetByDeviceCode(
            String deviceCode) {
        return jdbc.query(
                LOCK_ASSET_SQL,
                (rs, ignored) -> new AssetRow(
                        rs.getLong("id"),
                        rs.getString("hardware_sn"),
                        rs.getString("device_public_code"),
                        rs.getString("lifecycle_status"),
                        rs.getString("acceptance_status"),
                        nullableLong(rs, "tenant_id"),
                        nullableLong(rs, "organization_id"),
                        rs.getInt("expected_port_count"),
                        rs.getString(
                                "management_architecture_generation"),
                        rs.getString("business_admission_status")),
                deviceCode).stream().findFirst();
    }

    @Override
    public Optional<SubjectStatusRow> lockTenant(long tenantId) {
        return jdbc.query(
                LOCK_TENANT_SQL,
                (rs, ignored) -> new SubjectStatusRow(
                        rs.getLong("id"),
                        rs.getString("status")),
                tenantId).stream().findFirst();
    }

    @Override
    public Optional<SubjectStatusRow> lockOrganization(
            long tenantId,
            long organizationId) {
        return jdbc.query(
                LOCK_ORGANIZATION_SQL,
                (rs, ignored) -> new SubjectStatusRow(
                        rs.getLong("id"),
                        rs.getString("status")),
                tenantId,
                organizationId).stream().findFirst();
    }

    @Override
    public Optional<TransportPresenceRow> lockTransportPresence(
            long assetId) {
        return jdbc.query(
                LOCK_TRANSPORT_PRESENCE_SQL,
                (rs, ignored) -> new TransportPresenceRow(
                        rs.getString("onenet_connection_status")),
                assetId).stream().findFirst();
    }

    @Override
    public Optional<OccupancyRow> lockOccupancy(long assetId) {
        return jdbc.query(
                LOCK_OCCUPANCY_SQL,
                (rs, ignored) -> new OccupancyRow(
                        rs.getString("occupancy_kind")),
                assetId).stream().findFirst();
    }

    @Override
    public List<Long> lockReleasedPendingDeliveryIds(long assetId) {
        return jdbc.query(
                LOCK_RELEASED_PENDING_DELIVERY_SQL,
                (rs, ignored) -> rs.getLong("id"),
                assetId);
    }

    @Override
    public Optional<ConfigurationRow> lockLatestConfiguration(
            long tenantId,
            long organizationId,
            long assetId) {
        return jdbc.query(
                LOCK_LATEST_CONFIGURATION_SQL,
                (rs, ignored) -> new ConfigurationRow(
                        rs.getLong("id"),
                        rs.getLong("version_no"),
                        rs.getBytes("content_sha256"),
                        rs.getBytes("mcu_payload_sha256"),
                        rs.getLong("edge_heartbeat_interval_ms"),
                        rs.getLong("edge_heartbeat_miss_threshold"),
                        rs.getLong("continue_delivery_wait_ms"),
                        rs.getLong("negative_weight_threshold_g"),
                        rs.getLong("delivery_auto_close_ms"),
                        rs.getBoolean("application_applied"),
                        rs.getBoolean("runtime_applied")),
                tenantId,
                organizationId,
                assetId).stream().findFirst();
    }

    @Override
    public Optional<PortRow> lockPort(
            long tenantId,
            long organizationId,
            long assetId,
            int portNo) {
        return jdbc.query(
                LOCK_PORT_SQL,
                (rs, ignored) -> new PortRow(
                        rs.getLong("id"),
                        rs.getInt("port_no")),
                tenantId,
                organizationId,
                assetId,
                portNo).stream().findFirst();
    }

    @Override
    public Optional<PortConfigurationRow> lockPortConfiguration(
            long tenantId,
            long organizationId,
            long assetId,
            long configurationId,
            long portId) {
        return jdbc.query(
                LOCK_PORT_CONFIGURATION_SQL,
                (rs, ignored) -> new PortConfigurationRow(
                        rs.getLong("id"),
                        rs.getBoolean("business_enabled"),
                        rs.getBigDecimal("unit_price_yuan_per_kg"),
                        rs.getString("fullness_mode"),
                        rs.getLong("calibration_version")),
                tenantId,
                organizationId,
                assetId,
                configurationId,
                portId).stream().findFirst();
    }

    @Override
    public long insertSession(SessionInsert insert) {
        return insertAndReturnKey(
                INSERT_SESSION_SQL,
                insert.sessionUid().toString(),
                insert.tenantId(),
                insert.organizationId(),
                insert.assetId(),
                insert.portId(),
                insert.organizationUserId(),
                insert.deviceConfigurationId(),
                insert.deviceConfigurationVersion(),
                insert.deviceConfigurationContentSha256(),
                insert.deviceConfigurationMcuPayloadSha256(),
                insert.portConfigurationId(),
                insert.deliveryConfigurationId(),
                insert.deliveryConfigurationContentSha256(),
                insert.bagId(),
                insert.bagUid().toString(),
                insert.bagCode(),
                insert.unitPriceYuanPerKg(),
                insert.openBalanceFloorCent(),
                insert.maxReviewAbsWeightGrams(),
                insert.negativeWeightThresholdGrams(),
                insert.continueDeliveryWaitMs(),
                insert.authorizationExpiresAt(),
                insert.resultRecoveryDeadlineAt(),
                insert.now(),
                insert.now());
    }

    @Override
    public void insertOccupancy(
            long assetId,
            long tenantId,
            long organizationId,
            long sessionId,
            LocalDateTime acquiredAt) {
        requireSingle(
                jdbc.update(
                        INSERT_OCCUPANCY_SQL,
                        assetId,
                        tenantId,
                        organizationId,
                        sessionId,
                        acquiredAt),
                "insert delivery occupancy");
    }

    @Override
    public long insertCommand(CommandInsert insert) {
        return insertAndReturnKey(
                INSERT_COMMAND_SQL,
                insert.commandUid().toString(),
                insert.tenantId(),
                insert.organizationId(),
                insert.assetId(),
                insert.deliverySessionId(),
                insert.semanticEnvelopeJson(),
                insert.semanticEnvelopeSha256(),
                insert.now(),
                insert.now(),
                insert.now());
    }

    private long insertAndReturnKey(
            String sql,
            Object... parameters) {
        KeyHolder keyHolder = new GeneratedKeyHolder();
        PreparedStatementCreator creator = connection -> {
            PreparedStatement statement = connection.prepareStatement(
                    sql,
                    Statement.RETURN_GENERATED_KEYS);
            for (int index = 0;
                 index < parameters.length;
                 index++) {
                statement.setObject(index + 1, parameters[index]);
            }
            return statement;
        };
        requireSingle(
                jdbc.update(creator, keyHolder),
                "insert start-delivery device fact");
        Number key = keyHolder.getKey();
        if (key == null || key.longValue() <= 0) {
            throw invariant("generated key is unavailable");
        }
        return key.longValue();
    }

    private static void requireSingle(int affected, String operation) {
        if (affected != 1) {
            throw invariant(operation + " affected " + affected + " rows");
        }
    }

    private static Long nullableLong(
            java.sql.ResultSet resultSet,
            String column) throws java.sql.SQLException {
        long value = resultSet.getLong(column);
        return resultSet.wasNull() ? null : value;
    }

    private static IllegalStateException invariant(String message) {
        return new IllegalStateException(
                "start-delivery device invariant failed: " + message);
    }
}
