package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.ManagementScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.query.ManagementScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.query.ManagementScopeAuthorizationQuery.Channel;
import org.enveloping.ecobin.device.web.v1.MiniappStaffDeviceModels.DeviceDetail;
import org.enveloping.ecobin.device.web.v1.MiniappStaffDeviceModels.DeviceSummary;
import org.enveloping.ecobin.device.web.v1.MiniappStaffDeviceModels.PortSummary;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;

@Service
public class MiniappStaffDeviceQueryService {

    private final JdbcTemplate jdbc;
    private final ManagementScopeAuthorizationPort authorization;

    public MiniappStaffDeviceQueryService(
            JdbcTemplate jdbc,
            ManagementScopeAuthorizationPort authorization) {
        this.jdbc = jdbc;
        this.authorization = authorization;
    }

    @Transactional(readOnly = true)
    public List<DeviceSummary> devices() {
        Scope scope = scope();
        return jdbc.query(summarySql() + " ORDER BY asset.device_public_code",
                (rs, ignored) -> new DeviceSummary(
                        rs.getString("device_public_code"),
                        rs.getString("display_name"),
                        rs.getString("location_address"),
                        rs.getString("edge_connection_status"),
                        rs.getString("mcu_link_status"),
                        rs.getString("safety_status"),
                        rs.getInt("port_count"),
                        instant(rs.getObject(
                                "last_heartbeat_at", LocalDateTime.class))),
                scope.tenantId(), scope.organizationId());
    }

    @Transactional(readOnly = true)
    public DeviceDetail device(String requestedCode) {
        Scope scope = scope();
        String code = code(requestedCode);
        DeviceSummary device = jdbc.query(
                        summarySql() + " AND asset.device_public_code = ?",
                        (rs, ignored) -> new DeviceSummary(
                                rs.getString("device_public_code"),
                                rs.getString("display_name"),
                                rs.getString("location_address"),
                                rs.getString("edge_connection_status"),
                                rs.getString("mcu_link_status"),
                                rs.getString("safety_status"),
                                rs.getInt("port_count"),
                                instant(rs.getObject(
                                        "last_heartbeat_at",
                                        LocalDateTime.class))),
                        scope.tenantId(), scope.organizationId(), code)
                .stream().findFirst().orElseThrow(
                        MiniappStaffDeviceQueryService::notFound);
        List<PortSummary> ports = jdbc.query("""
                        SELECT port.port_no,
                               COALESCE(snapshot.display_name,
                                   CONCAT('投口 ', port.port_no)) display_name,
                               COALESCE(snapshot.business_enabled, 0)
                                   AS business_enabled,
                               COALESCE(runtime.delivery_door_state, 'UNKNOWN')
                                   AS delivery_door_state,
                               COALESCE(runtime.weight_sensor_health, 'UNKNOWN')
                                   AS weight_sensor_health,
                               COALESCE(runtime.infrared_sensor_health, 'UNKNOWN')
                                   AS infrared_sensor_health,
                               COALESCE(runtime.smoke_state, 'UNKNOWN')
                                   AS smoke_state,
                               COALESCE(runtime.safety_status, 'UNKNOWN')
                                   AS safety_status,
                               runtime.last_observed_at
                        FROM dev_port port
                        JOIN dev_device_asset asset
                          ON asset.id = port.asset_id
                         AND asset.tenant_id = port.tenant_id
                         AND asset.organization_id = port.organization_id
                        LEFT JOIN dev_config_version configuration
                          ON configuration.id = (
                            SELECT latest.id
                            FROM dev_config_version latest
                            WHERE latest.asset_id = asset.id
                            ORDER BY latest.version_no DESC
                            LIMIT 1
                          )
                        LEFT JOIN dev_port_config_snapshot snapshot
                          ON snapshot.config_version_id = configuration.id
                         AND snapshot.port_id = port.id
                        LEFT JOIN dev_port_runtime_state runtime
                          ON runtime.port_id = port.id
                        WHERE port.tenant_id = ?
                          AND port.organization_id = ?
                          AND asset.device_public_code = ?
                          AND asset.lifecycle_status = 'NORMAL'
                        ORDER BY port.port_no
                        """,
                (rs, ignored) -> new PortSummary(
                        rs.getInt("port_no"),
                        rs.getString("display_name"),
                        rs.getBoolean("business_enabled"),
                        rs.getString("delivery_door_state"),
                        rs.getString("weight_sensor_health"),
                        rs.getString("infrared_sensor_health"),
                        rs.getString("smoke_state"),
                        rs.getString("safety_status"),
                        instant(rs.getObject(
                                "last_observed_at", LocalDateTime.class))),
                scope.tenantId(), scope.organizationId(), code);
        return new DeviceDetail(device, ports, databaseNow());
    }

    private Scope scope() {
        var authorized = authorization.authorize(
                new ManagementScopeAuthorizationQuery(
                        Channel.MINIAPP_STAFF,
                        false, null, null, "device.read"));
        return authorized.persistenceRef().withScopeOnce(
                (tenantId, organizations, platformId, staffId) -> {
                    if (tenantId == null || organizations.size() != 1) {
                        throw new IllegalStateException(
                                "miniapp-staff device scope is incomplete");
                    }
                    return new Scope(tenantId, organizations.getFirst());
                });
    }

    private static String summarySql() {
        return """
                SELECT asset.device_public_code,
                       asset.installation_display_name AS display_name,
                       asset.installation_address AS location_address,
                       COALESCE(runtime.edge_connection_status, 'UNKNOWN')
                           AS edge_connection_status,
                       COALESCE(runtime.mcu_link_status, 'UNKNOWN')
                           AS mcu_link_status,
                       COALESCE(runtime.safety_status, 'UNKNOWN')
                           AS safety_status,
                       runtime.last_heartbeat_at,
                        (SELECT COUNT(*) FROM dev_port port
                        WHERE port.asset_id = asset.id)
                           AS port_count
                FROM dev_device_asset asset
                LEFT JOIN dev_device_runtime_state runtime
                  ON runtime.asset_id = asset.id
                 AND runtime.tenant_id = asset.tenant_id
                 AND runtime.organization_id = asset.organization_id
                WHERE asset.tenant_id = ?
                  AND asset.organization_id = ?
                  AND asset.lifecycle_status = 'NORMAL'
                """;
    }

    private Instant databaseNow() {
        LocalDateTime result = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        if (result == null) {
            throw new IllegalStateException("database time unavailable");
        }
        return instant(result);
    }

    private static String code(String value) {
        if (value == null || !value.trim()
                .matches("Dv_[A-Za-z0-9_-]{24,61}")) {
            throw notFound();
        }
        return value.trim();
    }

    private static Instant instant(LocalDateTime value) {
        return value == null ? null : value.toInstant(ZoneOffset.UTC);
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404, "RESOURCE.NOT_FOUND", "设备不存在或不可用");
    }

    private record Scope(long tenantId, long organizationId) { }
}
