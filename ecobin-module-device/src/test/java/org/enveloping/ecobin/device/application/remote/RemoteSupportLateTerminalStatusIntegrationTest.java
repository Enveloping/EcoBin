package org.enveloping.ecobin.device.application.remote;

import org.enveloping.ecobin.device.application.enrollment.RemoteSupportBootstrapProperties;
import org.enveloping.ecobin.device.application.target.DeviceConfigurationCanonicalizer;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationIdempotencyPort;
import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskProofPort;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistrationPort;
import org.enveloping.ecobin.identity.api.port.DeviceScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.port.PlatformMaintenanceSshKeyQueryPort;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import tools.jackson.databind.ObjectMapper;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;

class RemoteSupportLateTerminalStatusIntegrationTest {

    private static final String HARDWARE_SN = "SN-REMOTE-LATE-0001";

    private JdbcTemplate jdbc;
    private ObjectMapper objectMapper;
    private RemoteSupportSessionService sessions;
    private UUID sessionUid;
    private UUID openCommandUid;
    private UUID closeCommandUid;

    @BeforeEach
    void setUp() {
        DriverManagerDataSource dataSource = new DriverManagerDataSource();
        dataSource.setDriverClassName("org.h2.Driver");
        dataSource.setUrl("jdbc:h2:mem:remote_support_late_"
                + UUID.randomUUID()
                + ";MODE=MySQL;DB_CLOSE_DELAY=-1");
        jdbc = new DatabaseClockJdbcTemplate(dataSource);
        objectMapper = new ObjectMapper();
        createSchema();
        TransactionSynchronizationManager.initSynchronization();

        RemoteSupportBootstrapProperties properties =
                new RemoteSupportBootstrapProperties();
        properties.setEnabled(true);
        sessions = new RemoteSupportSessionService(
                jdbc,
                objectMapper,
                mock(DeviceScopeAuthorizationPort.class),
                mock(PlatformMaintenanceSshKeyQueryPort.class),
                mock(GlobalOperationIdempotencyPort.class),
                mock(AuditPort.class),
                mock(PlatformDeviceAssetTaskRefFactory.class),
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class),
                mock(ReliableDeviceTaskProofPort.class),
                mock(DeviceConfigurationCanonicalizer.class),
                mock(RemoteSupportLeaseStore.class),
                mock(OpenSshMaintenanceCertificateSigner.class),
                properties);

        sessionUid = UUID.randomUUID();
        openCommandUid = UUID.randomUUID();
        closeCommandUid = UUID.randomUUID();
    }

    @AfterEach
    void tearDown() {
        TransactionSynchronizationManager.clearSynchronization();
    }

    @Test
    void delayedDeviceClosedRefreshesSnapshotAfterServerAlreadyClosed() {
        seedTerminalSession("CLOSED", "OPEN", null);
        Instant occurredAt = Instant.now().minusSeconds(2)
                .truncatedTo(ChronoUnit.MILLIS);

        boolean changed = sessions.applyStatus(
                77L,
                statusEvent(
                        UUID.randomUUID(),
                        closeCommandUid,
                        "CLOSED",
                        null,
                        occurredAt));

        Map<String, Object> projection = sessionProjection();
        assertEquals("CLOSED", projection.get("state"));
        assertEquals("CLOSED", projection.get("device_reported_state"));
        assertTrue(changed);
        assertEquals("REVOKED", projection.get("server_lease_state"));
        assertNull(projection.get("failure_code"));
        assertEquals(1, ((Number) projection.get("lease_released")).intValue());
        assertEquals(1, jdbc.queryForObject(
                "SELECT COUNT(*) FROM dev_remote_support_status_event",
                Integer.class));
    }

    @Test
    void laterReceivedEvidenceWinsEvenWhenDeviceTimestampIsOlder() {
        seedTerminalSession("CLOSED", "CLOSED", null);
        Instant latestOccurredAt = Instant.now().minusSeconds(2)
                .truncatedTo(ChronoUnit.MILLIS);
        insertStatusHistory(
                UUID.randomUUID(),
                70L,
                "CLOSED",
                null,
                latestOccurredAt);

        boolean changed = sessions.applyStatus(
                71L,
                statusEvent(
                        UUID.randomUUID(),
                        closeCommandUid,
                        "FAILED",
                        "SSH_EXITED",
                        latestOccurredAt.minusSeconds(1)));

        Map<String, Object> projection = sessionProjection();
        assertTrue(changed);
        assertEquals("CLOSED", projection.get("state"));
        assertEquals("FAILED", projection.get("device_reported_state"));
        assertNull(projection.get("failure_code"));
        assertEquals(2, jdbc.queryForObject(
                "SELECT COUNT(*) FROM dev_remote_support_status_event",
                Integer.class));
    }

    @Test
    void delayedDeviceClosedDoesNotReplaceAuthoritativeFailure() {
        seedTerminalSession(
                "FAILED", "OPEN", "SERVER_LEASE_LOST");

        boolean changed = sessions.applyStatus(
                78L,
                statusEvent(
                        UUID.randomUUID(),
                        closeCommandUid,
                        "CLOSED",
                        null,
                        Instant.now().minusSeconds(2)
                                .truncatedTo(ChronoUnit.MILLIS)));

        Map<String, Object> projection = sessionProjection();
        assertTrue(changed);
        assertEquals("FAILED", projection.get("state"));
        assertEquals("CLOSED", projection.get("device_reported_state"));
        assertEquals("SERVER_LEASE_LOST", projection.get("failure_code"));
        assertEquals("server terminal detail",
                projection.get("failure_detail"));
        assertEquals("REVOKED", projection.get("server_lease_state"));
        assertEquals(1, ((Number) projection.get("lease_released")).intValue());
    }

    private void createSchema() {
        jdbc.execute("""
                CREATE TABLE dev_device_asset (
                    id BIGINT PRIMARY KEY,
                    hardware_sn VARCHAR(64) NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_remote_support_session (
                    id BIGINT PRIMARY KEY,
                    session_uid VARCHAR(36) NOT NULL,
                    operation_uid VARCHAR(36) NOT NULL,
                    request_sha256 VARBINARY(32) NOT NULL,
                    asset_id BIGINT NOT NULL,
                    requested_by_platform_admin_id BIGINT NOT NULL,
                    requested_by_platform_admin_uid VARCHAR(36) NOT NULL,
                    maintenance_ssh_key_uid VARCHAR(36) NOT NULL,
                    tunnel_public_key VARCHAR(128) NOT NULL,
                    tunnel_fingerprint_sha256 VARBINARY(32) NOT NULL,
                    ssh_host_public_key VARCHAR(128) NOT NULL,
                    state VARCHAR(16) NOT NULL,
                    port_no INT NOT NULL,
                    device_reported_state VARCHAR(16),
                    server_lease_state VARCHAR(16) NOT NULL,
                    open_command_uid VARCHAR(36) NOT NULL,
                    close_operation_uid VARCHAR(36),
                    close_request_sha256 VARBINARY(32),
                    close_command_uid VARCHAR(36),
                    failure_code VARCHAR(100),
                    failure_detail VARCHAR(500),
                    certificate_text VARCHAR(2048),
                    connect_deadline_at TIMESTAMP NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    opened_at TIMESTAMP,
                    closed_at TIMESTAMP,
                    lease_released_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL,
                    lock_version BIGINT NOT NULL
                )
                """);
        jdbc.execute("""
                CREATE TABLE dev_remote_support_status_event (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    event_uid VARCHAR(36) NOT NULL UNIQUE,
                    session_id BIGINT NOT NULL,
                    source_inbox_id BIGINT NOT NULL UNIQUE,
                    reported_state VARCHAR(16) NOT NULL,
                    failure_code VARCHAR(100),
                    ssh_exit_code INT,
                    event_sha256 VARBINARY(32) NOT NULL,
                    occurred_at TIMESTAMP,
                    clock_quality VARCHAR(16) NOT NULL DEFAULT 'SYNCED',
                    received_at TIMESTAMP NOT NULL,
                    created_at TIMESTAMP NOT NULL
                )
                """);
    }

    private void seedTerminalSession(
            String state,
            String deviceReportedState,
            String failureCode) {
        jdbc.update(
                "INSERT INTO dev_device_asset (id, hardware_sn) VALUES (?, ?)",
                1L,
                HARDWARE_SN);
        LocalDateTime now = LocalDateTime.now(ZoneOffset.UTC);
        jdbc.update("""
                        INSERT INTO dev_remote_support_session (
                            id, session_uid, operation_uid, request_sha256,
                            asset_id, requested_by_platform_admin_id,
                            requested_by_platform_admin_uid,
                            maintenance_ssh_key_uid, tunnel_public_key,
                            tunnel_fingerprint_sha256, ssh_host_public_key,
                            state, port_no, device_reported_state,
                            server_lease_state, open_command_uid,
                            close_operation_uid, close_request_sha256,
                            close_command_uid, failure_code, failure_detail,
                            certificate_text, connect_deadline_at, expires_at,
                            opened_at, closed_at, lease_released_at,
                            created_at, updated_at, lock_version
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                1L,
                sessionUid.toString(),
                UUID.randomUUID().toString(),
                new byte[32],
                1L,
                9L,
                UUID.randomUUID().toString(),
                UUID.randomUUID().toString(),
                "ssh-ed25519 " + "A".repeat(68),
                new byte[32],
                "ssh-ed25519 " + "B".repeat(68),
                state,
                22012,
                deviceReportedState,
                "REVOKED",
                openCommandUid.toString(),
                UUID.randomUUID().toString(),
                new byte[32],
                closeCommandUid.toString(),
                failureCode,
                failureCode == null ? null : "server terminal detail",
                null,
                now.minusMinutes(10),
                now.plusMinutes(10),
                now.minusMinutes(9),
                now.minusSeconds(5),
                now.minusSeconds(4),
                now.minusMinutes(11),
                now.minusSeconds(4),
                3L);
    }

    private void insertStatusHistory(
            UUID eventUid,
            long sourceInboxId,
            String state,
            String failureCode,
            Instant occurredAt) {
        LocalDateTime timestamp = LocalDateTime.ofInstant(
                occurredAt, ZoneOffset.UTC);
        jdbc.update("""
                        INSERT INTO dev_remote_support_status_event (
                            event_uid, session_id, source_inbox_id,
                            reported_state, failure_code, ssh_exit_code,
                            event_sha256, occurred_at, received_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                        """,
                eventUid.toString(),
                1L,
                sourceInboxId,
                state,
                failureCode,
                new byte[32],
                timestamp,
                timestamp,
                timestamp);
    }

    private tools.jackson.databind.JsonNode statusEvent(
            UUID eventUid,
            UUID commandUid,
            String state,
            String failureCode,
            Instant occurredAt) {
        var root = objectMapper.createObjectNode();
        root.put("eventCanonicalSha256", "a".repeat(64));
        root.putObject("trustedSource").put("deviceName", HARDWARE_SN);
        var event = root.putObject("event");
        event.put("eventUid", eventUid.toString());
        event.put("eventType", "REMOTE_SUPPORT_TUNNEL_STATUS");
        event.put("commandUid", commandUid.toString());
        event.put("occurredAt", occurredAt.toString());
        event.put("clockQuality", "SYNCED");
        event.putObject("target")
                .put("type", "DEVICE_ASSET")
                .put("uid", HARDWARE_SN);
        var payload = event.putObject("payload");
        payload.put("sessionUid", sessionUid.toString());
        payload.put("state", state);
        payload.put("remotePort", 22012);
        if (failureCode != null) {
            payload.put("failureCode", failureCode);
        }
        return root;
    }

    private Map<String, Object> sessionProjection() {
        return jdbc.queryForMap("""
                SELECT state, device_reported_state, server_lease_state,
                       failure_code, failure_detail,
                       CASE WHEN lease_released_at IS NULL THEN 0 ELSE 1 END
                           AS lease_released
                FROM dev_remote_support_session
                WHERE id = 1
                """);
    }

    private static final class DatabaseClockJdbcTemplate
            extends JdbcTemplate {

        private DatabaseClockJdbcTemplate(
                javax.sql.DataSource dataSource) {
            super(dataSource);
        }

        @Override
        public <T> T queryForObject(String sql, Class<T> requiredType) {
            if ("SELECT UTC_TIMESTAMP(3)".equals(sql)) {
                return requiredType.cast(
                        LocalDateTime.now(ZoneOffset.UTC));
            }
            return super.queryForObject(sql, requiredType);
        }
    }
}
