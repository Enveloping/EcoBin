package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.port.TrustedPlatformDeviceAssetFactPort;
import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedPlatformDeviceAssetFactEvent;
import org.enveloping.ecobin.framework.reliability.UntrustedInboxSourceException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Set;

/**
 * 完成永久机构分配前产生的可靠设备资产事实。
 *
 * <p>规范化消息本身保留在平台收件箱；没有机构运行表或告警可写，因此这里只确认设备
 * 事实已被平台安全接收。机构分配及配置应用后，设备会自动发送新的机构运行快照。</p>
 */
@Service
public class TrustedPlatformDeviceAssetFactService
        implements TrustedPlatformDeviceAssetFactPort {

    private static final Set<String> SUPPORTED = Set.of(
            "DEVICE_FAULT_OBSERVED",
            "DEVICE_FAULT_RECOVERED",
            "SAFETY_SENSOR_STATE_CHANGED");
    private static final String UUID_V4 =
            "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}"
                    + "-[89ab][0-9a-f]{3}-[0-9a-f]{12}$";
    private static final String SHA256 = "^[0-9a-f]{64}$";

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final ReliablePlatformEdgeConfirmationService confirmationService;

    public TrustedPlatformDeviceAssetFactService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            ReliablePlatformEdgeConfirmationService confirmationService) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.confirmationService = confirmationService;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public TrustedDeviceEventApplyResult apply(
            TrustedPlatformDeviceAssetFactEvent inboxEvent) {
        if (!SUPPORTED.contains(inboxEvent.messageKind())) {
            throw new IllegalArgumentException(
                    "unsupported platform device asset fact");
        }
        return inboxEvent.sourceInbox().use(ignoredInboxId -> {
            JsonNode normalized = objectMapper.readTree(
                    inboxEvent.normalizedPayload());
            JsonNode source = requiredObject(normalized, "trustedSource");
            JsonNode event = requiredObject(normalized, "event");
            JsonNode target = requiredObject(event, "target");
            String hardwareSn = requiredText(source, "deviceName", 64);
            requireTextEquals(
                    event, "eventType", inboxEvent.messageKind());
            requireIntegerEquals(event, "schemaVersion", 2);
            requireTextEquals(target, "type", "DEVICE_ASSET");
            requireTextEquals(target, "uid", hardwareSn);
            String eventUid = requiredPattern(
                    event, "eventUid", UUID_V4);
            String payloadSha256 = requiredPattern(
                    event, "payloadSha256", SHA256);
            requiredPattern(
                    normalized, "eventCanonicalSha256", SHA256);

            List<Long> assetIds = jdbc.query("""
                            SELECT id
                            FROM dev_device_asset
                            WHERE hardware_sn = ?
                            FOR UPDATE
                            """,
                    (rs, ignored) -> rs.getLong("id"),
                    hardwareSn);
            if (assetIds.size() != 1) {
                throw new UntrustedInboxSourceException(
                        "platform device fact target is not registered");
            }
            LocalDateTime now = jdbc.queryForObject(
                    "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
            confirmationService.ensureApplied(
                    assetIds.getFirst(),
                    hardwareSn,
                    eventUid,
                    payloadSha256,
                    "NO_ACTION_REQUIRED",
                    now);
            return TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED;
        });
    }

    private static JsonNode requiredObject(JsonNode parent, String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isObject()) {
            throw new IllegalArgumentException(field + " must be an object");
        }
        return value;
    }

    private static String requiredText(
            JsonNode parent,
            String field,
            int maximumLength) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isTextual()
                || value.asText().isBlank()
                || value.asText().length() > maximumLength) {
            throw new IllegalArgumentException(
                    field + " must be bounded text");
        }
        return value.asText();
    }

    private static String requiredPattern(
            JsonNode parent,
            String field,
            String pattern) {
        String value = requiredText(parent, field, 160);
        if (!value.matches(pattern)) {
            throw new IllegalArgumentException(
                    field + " has an invalid stable format");
        }
        return value;
    }

    private static void requireTextEquals(
            JsonNode parent,
            String field,
            String expected) {
        if (!expected.equals(requiredText(parent, field, 160))) {
            throw new IllegalArgumentException(
                    field + " differs from the trusted target");
        }
    }

    private static void requireIntegerEquals(
            JsonNode parent,
            String field,
            int expected) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.canConvertToInt()
                || value.asInt() != expected) {
            throw new IllegalArgumentException(
                    field + " differs from the supported schema");
        }
    }
}
