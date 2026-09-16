package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.persistence.DeviceOwnedRecyclingPortRefFactory;
import org.enveloping.ecobin.device.api.persistence.RecyclingDevicePortRef;
import org.enveloping.ecobin.device.api.port.DeviceListFullnessQueryPort;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DeviceAssetView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DeviceListPortView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DeviceListStatusView;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/** Bounded read-only projection: one port query and two current-fullness queries per page. */
@Service
public class DeviceListStatusQuery {
    private final JdbcTemplate jdbc;
    private final DeviceOwnedRecyclingPortRefFactory refs;
    private final DeviceListFullnessQueryPort fullness;

    public DeviceListStatusQuery(JdbcTemplate jdbc,
            DeviceOwnedRecyclingPortRefFactory refs, DeviceListFullnessQueryPort fullness) {
        this.jdbc = jdbc;
        this.refs = refs;
        this.fullness = fullness;
    }

    @Transactional(propagation = Propagation.MANDATORY, readOnly = true)
    public List<DeviceAssetView> enrich(List<DeviceAssetView> assets) {
        if (assets.isEmpty()) return assets;
        Map<UUID, RecyclingDevicePortRef> references = new LinkedHashMap<>();
        List<PortRow> rows = jdbc.query("""
                SELECT asset.asset_uid, asset.tenant_id, asset.organization_id,
                       port.id AS port_id, port.port_no,
                       COALESCE(snapshot.display_name, CONCAT('投口 ', port.port_no)) AS display_name,
                       runtime.reported_weight_grams, runtime.weight_value_available,
                       runtime.weight_sensor_health, runtime.weight_measurement_status,
                       runtime.infrared_value, runtime.infrared_sensor_health,
                       runtime.last_observed_at, runtime.delivery_door_actuator_health,
                       runtime.clean_solenoid_health, runtime.smoke_state,
                       runtime.smoke_sensor_health, runtime.safety_status,
                       head.mcu_link_status, head.camera_health, head.local_storage_health,
                       head.clock_sync_health, head.last_heartbeat_at
                FROM dev_device_asset asset
                LEFT JOIN dev_port port ON port.asset_id = asset.id
                LEFT JOIN dev_device_runtime_state head ON head.asset_id = asset.id
                LEFT JOIN dev_port_runtime_state runtime
                  ON runtime.asset_id = asset.id AND runtime.port_id = port.id
                LEFT JOIN dev_config_version configuration ON configuration.id = (
                    SELECT latest.id FROM dev_config_version latest
                    WHERE latest.asset_id = asset.id ORDER BY latest.version_no DESC LIMIT 1)
                LEFT JOIN dev_port_config_snapshot snapshot
                  ON snapshot.config_version_id = configuration.id AND snapshot.port_id = port.id
                WHERE asset.asset_uid IN (%s)
                ORDER BY asset.asset_uid, port.port_no
                """.formatted(placeholders(assets.size())), (rs, ignored) -> {
            UUID token = UUID.randomUUID();
            Long portId = rs.getObject("port_id", Long.class);
            Long tenantId = rs.getObject("tenant_id", Long.class);
            Long organizationId = rs.getObject("organization_id", Long.class);
            if (portId != null && tenantId != null && organizationId != null) {
                references.put(token, refs.issue(tenantId, organizationId, portId));
            }
            List<String> deviceFaults = new ArrayList<>();
            healthFault(deviceFaults, "控制板通信", rs.getString("mcu_link_status"));
            healthFault(deviceFaults, "摄像头", rs.getString("camera_health"));
            healthFault(deviceFaults, "本地存储", rs.getString("local_storage_health"));
            healthFault(deviceFaults, "设备时钟", rs.getString("clock_sync_health"));
            List<String> portFaults = new ArrayList<>();
            healthFault(portFaults, "投递门", rs.getString("delivery_door_actuator_health"));
            healthFault(portFaults, "清运门锁", rs.getString("clean_solenoid_health"));
            healthFault(portFaults, "称重传感器", rs.getString("weight_sensor_health"));
            healthFault(portFaults, "重量测量", rs.getString("weight_measurement_status"));
            healthFault(portFaults, "红外传感器", rs.getString("infrared_sensor_health"));
            healthFault(portFaults, "烟感", rs.getString("smoke_sensor_health"));
            if ("ALARM".equals(rs.getString("smoke_state"))) portFaults.add("烟雾报警");
            if (Set.of("UNSAFE", "LOCKED", "BLOCKED").contains(value(rs, "safety_status"))) {
                portFaults.add("安全保护已触发");
            }
            return new PortRow(UUID.fromString(rs.getString("asset_uid")), token, portId,
                    deviceFaults, time(rs, "last_heartbeat_at"),
                    new DeviceListPortView(rs.getInt("port_no"), rs.getString("display_name"),
                            rs.getObject("reported_weight_grams", Long.class),
                            rs.getObject("weight_value_available", Boolean.class),
                            rs.getString("weight_sensor_health"), rs.getString("weight_measurement_status"),
                            time(rs, "last_observed_at"), null, null, null,
                            rs.getString("infrared_value"),
                            rs.getString("infrared_sensor_health"), portFaults));
        }, assets.stream().map(asset -> asset.assetUid().toString()).toArray());

        Map<UUID, UUID> currentReports = fullness.currentReports(references);
        Map<UUID, FullnessFact> facts = new LinkedHashMap<>();
        if (!currentReports.isEmpty()) {
            jdbc.query("""
                    SELECT state_change_uid, port_id, reported_state,
                           weight_full, device_occurred_at
                    FROM dev_fullness_state_fact WHERE state_change_uid IN (%s)
                    """.formatted(placeholders(currentReports.size())), rs -> {
                facts.put(UUID.fromString(rs.getString("state_change_uid")),
                        new FullnessFact(rs.getLong("port_id"),
                                overallFull(rs.getString("reported_state")),
                                rs.getObject("weight_full", Boolean.class),
                                time(rs, "device_occurred_at")));
            }, currentReports.values().stream().map(UUID::toString).toArray());
        }
        Map<UUID, List<PortRow>> grouped = new LinkedHashMap<>();
        rows.forEach(row -> grouped.computeIfAbsent(row.assetUid(), ignored -> new ArrayList<>()).add(row));
        return assets.stream().map(asset -> {
            List<PortRow> deviceRows = grouped.getOrDefault(asset.assetUid(), List.of());
            List<DeviceListPortView> ports = deviceRows.stream().filter(row -> row.portId() != null)
                    .map(row -> withWeightFact(row, facts.get(currentReports.get(row.token())))).toList();
            return asset.withListStatus(new DeviceListStatusView(
                    deviceRows.isEmpty() ? List.of() : deviceRows.getFirst().faults(),
                    deviceRows.isEmpty() ? null : deviceRows.getFirst().observedAt(), ports));
        }).toList();
    }

    /** Returns the current bag's applied weight-fullness fact for one device. */
    @Transactional(propagation = Propagation.MANDATORY, readOnly = true)
    public Map<Integer, CurrentWeightFullness> currentWeightFullness(
            long assetId,
            Long tenantId,
            Long organizationId) {
        if (tenantId == null || organizationId == null) {
            return Map.of();
        }
        Map<UUID, RecyclingDevicePortRef> references = new LinkedHashMap<>();
        Map<UUID, Integer> portNumbers = new LinkedHashMap<>();
        jdbc.query("""
                SELECT id, port_no
                FROM dev_port
                WHERE tenant_id = ?
                  AND organization_id = ?
                  AND asset_id = ?
                ORDER BY port_no
                """, rs -> {
            UUID token = UUID.randomUUID();
            long portId = rs.getLong("id");
            references.put(token, refs.issue(
                    tenantId, organizationId, portId));
            portNumbers.put(token, rs.getInt("port_no"));
        }, tenantId, organizationId, assetId);
        Map<UUID, UUID> currentReports = fullness.currentReports(references);
        if (currentReports.isEmpty()) {
            return Map.of();
        }
        Map<UUID, FullnessFact> facts = new LinkedHashMap<>();
        jdbc.query("""
                SELECT state_change_uid, port_id, reported_state, weight_full,
                       device_occurred_at
                FROM dev_fullness_state_fact
                WHERE state_change_uid IN (%s)
                """.formatted(placeholders(currentReports.size())), rs -> {
            facts.put(UUID.fromString(rs.getString("state_change_uid")),
                    new FullnessFact(
                            rs.getLong("port_id"),
                            overallFull(rs.getString("reported_state")),
                            rs.getObject("weight_full", Boolean.class),
                            time(rs, "device_occurred_at")));
        }, currentReports.values().stream().map(UUID::toString).toArray());
        Map<Integer, CurrentWeightFullness> result = new LinkedHashMap<>();
        currentReports.forEach((token, reportUid) -> {
            FullnessFact fact = facts.get(reportUid);
            Integer portNo = portNumbers.get(token);
            if (fact != null && portNo != null) {
                result.put(portNo, new CurrentWeightFullness(
                        fact.weightFull(), fact.observedAt()));
            }
        });
        return Map.copyOf(result);
    }

    private static DeviceListPortView withWeightFact(PortRow row, FullnessFact fact) {
        var port = row.port();
        boolean matches = fact != null && row.portId() == fact.portId();
        return new DeviceListPortView(port.portNo(), port.displayName(), port.reportedWeightGrams(),
                port.weightValueAvailable(), port.weightSensorHealth(), port.weightMeasurementStatus(),
                port.observedAt(), matches ? fact.overallFull() : null,
                matches ? fact.weightFull() : null, matches ? fact.observedAt() : null,
                port.infraredValue(), port.infraredSensorHealth(), port.faults());
    }

    private static void healthFault(List<String> faults, String label, String health) {
        if (health == null) return;
        switch (health) {
            case "FAULT", "FAILED", "ERROR", "UNHEALTHY", "SENSOR_FAULT" -> faults.add(label + "故障");
            case "TIMEOUT" -> faults.add(label + "读取超时");
            case "PROTOCOL_ERROR" -> faults.add(label + "通信异常");
            case "CONFIG_ERROR" -> faults.add(label + "配置异常");
            case "OVERLOAD" -> faults.add(label + "超量程");
            case "DISCONNECTED", "OFFLINE" -> faults.add(label + "未连接");
            case "UNSTABLE" -> faults.add(label + "不稳定");
            case "FULL" -> faults.add(label + "已满");
            case "READ_ONLY" -> faults.add(label + "无法写入");
            case "UNSYNCED", "UNSYNCHRONIZED" -> faults.add(label + "未同步");
            default -> { }
        }
    }

    private static Boolean overallFull(String state) {
        return switch (state == null ? "" : state) {
            case "FULL" -> true;
            case "NOT_FULL" -> false;
            default -> null;
        };
    }

    private static String value(ResultSet rs, String column) throws SQLException {
        String value = rs.getString(column);
        return value == null ? "" : value;
    }
    private static Instant time(ResultSet rs, String column) throws SQLException {
        LocalDateTime value = rs.getObject(column, LocalDateTime.class);
        return value == null ? null : value.toInstant(ZoneOffset.UTC);
    }
    private static String placeholders(int count) { return String.join(",", Collections.nCopies(count, "?")); }
    private record FullnessFact(
            long portId, Boolean overallFull, Boolean weightFull,
            Instant observedAt) { }
    public record CurrentWeightFullness(Boolean full, Instant observedAt) { }
    private record PortRow(UUID assetUid, UUID token, Long portId, List<String> faults,
                           Instant observedAt, DeviceListPortView port) { }
}
