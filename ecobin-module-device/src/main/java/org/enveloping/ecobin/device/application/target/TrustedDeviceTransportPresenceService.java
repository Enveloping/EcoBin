package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.port.TrustedDeviceTransportPresencePort;
import org.enveloping.ecobin.device.api.result.DeviceTransportPresenceApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceTransportEvent;
import org.enveloping.ecobin.framework.reliability.UntrustedInboxSourceException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;

@Service
public class TrustedDeviceTransportPresenceService
        implements TrustedDeviceTransportPresencePort {

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;

    public TrustedDeviceTransportPresenceService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
    }

    @Override
    @Transactional(isolation = Isolation.READ_COMMITTED)
    public DeviceTransportPresenceApplyResult apply(
            TrustedDeviceTransportEvent event) {
        return event.sourceInbox().use(inboxId -> {
            JsonNode normalized = objectMapper.readTree(
                    event.normalizedPayload());
            JsonNode source = requiredObject(
                    normalized, "trustedSource");
            JsonNode presence = requiredObject(
                    normalized, "presence");
            String hardwareSn = requiredText(
                    source, "deviceName", 64);
            String status = requiredStatus(presence);
            LocalDateTime observedAt = observedAt(presence);
            return merge(
                    hardwareSn,
                    status,
                    observedAt,
                    databaseNow(),
                    "LIFECYCLE_EVENT",
                    inboxId);
        });
    }

    @Override
    @Transactional(isolation = Isolation.READ_COMMITTED)
    public DeviceTransportPresenceApplyResult observeOutboundOffline(
            String hardwareSn) {
        return current(requireHardwareSn(hardwareSn));
    }

    @Override
    @Transactional(isolation = Isolation.READ_COMMITTED)
    public DeviceTransportPresenceApplyResult observeAuthenticatedMessage(
            String hardwareSn,
            long sourceInboxId) {
        if (sourceInboxId <= 0) {
            throw new IllegalArgumentException(
                    "sourceInboxId must be positive");
        }
        Integer inboxExists = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM ops_inbox_message
                        WHERE id = ?
                        """,
                Integer.class,
                sourceInboxId);
        if (inboxExists == null || inboxExists != 1) {
            throw new UntrustedInboxSourceException(
                    "authenticated device inbox is not authoritative");
        }
        return current(requireHardwareSn(hardwareSn));
    }

    /**
     * 普通设备消息和 OneNet 下行错误只能证明某次收发发生过，不能证明设备当前在线或
     * 离线。保留这两个旧入口供现有调用方平滑迁移，但它们只读取生命周期投影，不再
     * 改写权威连接状态。
     */
    private DeviceTransportPresenceApplyResult current(String hardwareSn) {
        List<DeviceTransportPresenceApplyResult> rows = jdbc.query("""
                        SELECT transport.asset_id,
                               transport.onenet_connection_status
                        FROM dev_device_asset asset
                        JOIN dev_device_transport_state transport
                          ON transport.asset_id = asset.id
                        WHERE asset.hardware_sn = ?
                        """,
                (rs, ignored) -> new DeviceTransportPresenceApplyResult(
                        rs.getLong("asset_id"),
                        rs.getString("onenet_connection_status"),
                        false),
                hardwareSn);
        if (rows.size() != 1) {
            throw new UntrustedInboxSourceException(
                    "authenticated device asset is not authoritative");
        }
        return rows.getFirst();
    }

    private DeviceTransportPresenceApplyResult merge(
            String hardwareSn,
            String status,
            LocalDateTime observedAt,
            LocalDateTime receivedAt,
            String evidenceSource,
            Long sourceInboxId) {
        List<TransportRow> rows = jdbc.query("""
                        SELECT
                            asset.id,
                            transport.onenet_connection_status,
                            transport.status_observed_at,
                            transport.status_received_at
                        FROM dev_device_asset asset
                        JOIN dev_device_transport_state transport
                          ON transport.asset_id = asset.id
                        WHERE asset.hardware_sn = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new TransportRow(
                        rs.getLong("id"),
                        rs.getString("onenet_connection_status"),
                        rs.getObject(
                                "status_observed_at",
                                LocalDateTime.class),
                        rs.getObject(
                                "status_received_at",
                                LocalDateTime.class)),
                hardwareSn);
        if (rows.size() != 1) {
            throw new UntrustedInboxSourceException(
                    "authenticated device asset is not authoritative");
        }
        TransportRow current = rows.getFirst();
        if (!newer(status, observedAt, receivedAt, current)) {
            return new DeviceTransportPresenceApplyResult(
                    current.assetId(),
                    current.status(),
                    false);
        }
        int updated = jdbc.update("""
                        UPDATE dev_device_transport_state
                        SET onenet_connection_status = ?,
                            status_observed_at = ?,
                            status_received_at = ?,
                            evidence_source = ?,
                            source_inbox_id = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE asset_id = ?
                        """,
                status,
                observedAt,
                receivedAt,
                evidenceSource,
                sourceInboxId,
                receivedAt,
                current.assetId());
        requireSingle(updated, "merge device transport presence");
        if ("OFFLINE".equals(status)) {
            jdbc.update("""
                            UPDATE dev_device_runtime_state runtime
                            SET runtime.edge_connection_status = 'OFFLINE',
                                runtime.lock_version =
                                    runtime.lock_version + 1,
                                runtime.updated_at = ?
                            WHERE runtime.asset_id = ?
                              AND runtime.edge_connection_status <> 'OFFLINE'
                            """,
                    receivedAt,
                    current.assetId());
        }
        return new DeviceTransportPresenceApplyResult(
                current.assetId(), status, true);
    }

    private static boolean newer(
            String candidateStatus,
            LocalDateTime candidateObservedAt,
            LocalDateTime candidateReceivedAt,
            TransportRow current) {
        if (current.observedAt() == null) {
            return true;
        }
        int observed = candidateObservedAt.compareTo(current.observedAt());
        if (observed != 0) {
            return observed > 0;
        }
        if (!candidateStatus.equals(current.status())) {
            return "OFFLINE".equals(candidateStatus);
        }
        return current.receivedAt() == null
                || candidateReceivedAt.isAfter(current.receivedAt());
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)",
                LocalDateTime.class);
    }

    private static JsonNode requiredObject(JsonNode parent, String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isObject()) {
            throw new IllegalArgumentException(
                    field + " must be an object");
        }
        return value;
    }

    private static String requiredText(
            JsonNode parent, String field, int maximumLength) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isTextual()
                || value.asText().isBlank()
                || value.asText().length() > maximumLength) {
            throw new IllegalArgumentException(
                    field + " must be bounded text");
        }
        return value.asText();
    }

    private static String requiredStatus(JsonNode presence) {
        String status = requiredText(presence, "status", 16);
        if (!status.equals("ONLINE") && !status.equals("OFFLINE")) {
            throw new IllegalArgumentException(
                    "presence status is unsupported");
        }
        return status;
    }

    private static LocalDateTime observedAt(JsonNode presence) {
        String value = requiredText(presence, "observedAt", 40);
        try {
            return LocalDateTime.ofInstant(
                    Instant.parse(value), ZoneOffset.UTC);
        } catch (RuntimeException exception) {
            throw new IllegalArgumentException(
                    "presence observedAt must be UTC RFC3339",
                    exception);
        }
    }

    private static String requireHardwareSn(String value) {
        if (value == null || value.isBlank() || value.length() > 64
                || !value.equals(value.trim())) {
            throw new IllegalArgumentException(
                    "hardwareSn must be bounded text");
        }
        return value;
    }

    private static void requireSingle(int updated, String action) {
        if (updated != 1) {
            throw new IllegalStateException(
                    action + " updated " + updated + " rows");
        }
    }

    private record TransportRow(
            long assetId,
            String status,
            LocalDateTime observedAt,
            LocalDateTime receivedAt) {
    }
}
