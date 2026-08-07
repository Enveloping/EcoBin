package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistrationPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;

/**
 * Confirms a reliable factory-acceptance fact before the permanent asset has
 * a tenant or organization. The confirmation is anchored directly to the
 * platform-owned asset and is created in the evidence transaction.
 */
@Service
public class ReliablePlatformEdgeConfirmationService {

    static final String TASK_TYPE = "CONFIRM_EDGE_EVENT";
    static final String TARGET_TYPE = "BUSINESS_CONFIRMATION";
    static final int MAX_AUTO_ATTEMPTS = 100;

    private final ObjectMapper objectMapper;
    private final JdbcTemplate jdbc;
    private final DeviceConfigurationCanonicalizer canonicalizer;
    private final PlatformDeviceAssetTaskRefFactory taskRefFactory;
    private final ReliablePlatformDeviceControlTaskRegistrationPort
            taskRegistrationPort;

    public ReliablePlatformEdgeConfirmationService(
            ObjectMapper objectMapper,
            JdbcTemplate jdbc,
            DeviceConfigurationCanonicalizer canonicalizer,
            PlatformDeviceAssetTaskRefFactory taskRefFactory,
            ReliablePlatformDeviceControlTaskRegistrationPort
                    taskRegistrationPort) {
        this.objectMapper = objectMapper;
        this.jdbc = jdbc;
        this.canonicalizer = canonicalizer;
        this.taskRefFactory = taskRefFactory;
        this.taskRegistrationPort = taskRegistrationPort;
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public void ensureApplied(
            long assetId,
            String hardwareSn,
            String originalEventUid,
            String originalPayloadSha256,
            String effectKind,
            LocalDateTime processedAt) {
        String taskKey = TASK_TYPE + ":"
                + originalEventUid.toUpperCase(Locale.ROOT);
        Integer existing = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM ops_reliable_task
                        WHERE task_key = ?
                        """,
                Integer.class,
                taskKey);
        if (existing != null && existing > 0) {
            return;
        }

        UUID eventUid = UUID.fromString(originalEventUid);
        UUID confirmationUid = UUID.randomUUID();
        UUID commandUid = UUID.randomUUID();
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("confirmationUid", confirmationUid.toString());
        payload.put("effectKind", effectKind);
        payload.put("errorCode", null);
        payload.put("originalEventUid", originalEventUid);
        payload.put("originalPayloadSha256", originalPayloadSha256);
        payload.put("outcome", "BUSINESS_APPLIED");
        payload.put("processedAt", instant(processedAt));
        payload.put("quarantineUid", null);
        payload.put("resultReferences", List.of());

        Map<String, Object> target = new LinkedHashMap<>();
        target.put("type", "EDGE_EVENT");
        target.put("uid", originalEventUid);
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schemaVersion", 2);
        envelope.put("commandUid", commandUid.toString());
        envelope.put("commandType", TASK_TYPE);
        envelope.put("targetDeviceName", hardwareSn);
        envelope.put("target", target);
        envelope.put("issuedAt", instant(processedAt));
        envelope.put("expiresAt", instant(processedAt.plusYears(10)));
        envelope.put("payloadSchemaVersion", 2);
        envelope.put(
                "payloadSha256",
                canonicalizer.hex(canonicalizer.payloadSha256(payload)));
        envelope.put("payload", payload);
        envelope.put("cosGrant", null);

        taskRegistrationPort.register(
                new ReliablePlatformDeviceControlTaskRegistration(
                        TASK_TYPE,
                        taskKey,
                        TARGET_TYPE,
                        confirmationUid.toString(),
                        taskRefFactory.issue(assetId),
                        2,
                        objectMapper.writeValueAsString(envelope),
                        canonicalizer.payloadSha256(envelope),
                        eventUid,
                        eventUid,
                        MAX_AUTO_ATTEMPTS));
    }

    private static String instant(LocalDateTime value) {
        return DateTimeFormatter.ISO_INSTANT.format(
                value.toInstant(ZoneOffset.UTC));
    }
}
