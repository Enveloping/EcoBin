package org.enveloping.ecobin.operations.infrastructure.persistence.reliability;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.PreparedStatementCreator;
import org.springframework.jdbc.core.RowMapper;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.time.Duration;
import java.time.LocalDateTime;
import java.util.Arrays;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ReliableOperationsJdbcRepositorySqlTest {

    @Test
    void persistsExternalRequestIdentityWithTheAttemptResult() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.update(anyString(), any(Object[].class))).thenReturn(1);
        ReliableOperationsJdbcRepository repository =
                new ReliableOperationsJdbcRepository(jdbc);
        LocalDateTime now = LocalDateTime.of(2026, 8, 30, 12, 0);

        repository.recordDeviceAttemptResult(
                7L,
                "PERMANENT_TECHNICAL_FAILURE",
                25L,
                new byte[32],
                new byte[32],
                200,
                "ONENET_10415",
                "a25087f46df04b69b29e90ef0acfd115",
                "required value",
                now);

        var sqlCaptor = org.mockito.ArgumentCaptor.forClass(String.class);
        var argumentsCaptor = org.mockito.ArgumentCaptor
                .forClass(Object[].class);
        verify(jdbc).update(sqlCaptor.capture(), argumentsCaptor.capture());
        assertTrue(normalize(sqlCaptor.getValue()).contains(
                "external_request_id = ?"));
        assertTrue(Arrays.asList(argumentsCaptor.getValue()).contains(
                "a25087f46df04b69b29e90ef0acfd115"));
    }

    @Test
    void bindsExpiredEvidenceBlockUpdateInSqlPlaceholderOrder() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.update(anyString(), any(Object[].class))).thenReturn(1);
        ReliableOperationsJdbcRepository repository =
                new ReliableOperationsJdbcRepository(jdbc);
        LocalDateTime now = LocalDateTime.of(2026, 8, 11, 12, 0);

        repository.blockExpiredDeviceEvidenceWait(7L, 3L, now);

        var sqlCaptor = org.mockito.ArgumentCaptor.forClass(String.class);
        var argumentsCaptor = org.mockito.ArgumentCaptor
                .forClass(Object[].class);
        verify(jdbc).update(
                sqlCaptor.capture(), argumentsCaptor.capture());
        assertTrue(normalize(sqlCaptor.getValue()).contains(
                "completed_at = ?"));
        assertArrayEquals(
                new Object[]{now, now, 7L, 3L},
                argumentsCaptor.getValue());
    }

    @Test
    void persistsTheRequestedTerminalDomainQuarantineReason() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        UUID quarantineUid = UUID.fromString(
                "20000000-0000-4000-8000-000000000002");
        when(jdbc.queryForObject(
                anyString(),
                eq(String.class),
                any(byte[].class))).thenReturn(quarantineUid.toString());
        ReliableOperationsJdbcRepository repository =
                new ReliableOperationsJdbcRepository(jdbc);
        byte[] dedupe = new byte[32];

        UUID stored = repository.upsertDomainQuarantine(
                dedupe,
                "ORGANIZATION",
                11L,
                12L,
                "ONENET",
                "test-device-1",
                "event-1",
                13L,
                new byte[32],
                new byte[32],
                "EVENT_TARGET_NOT_AUTHORITATIVE",
                "configuration progress references an obsolete target",
                LocalDateTime.of(2026, 8, 9, 12, 0));

        var sqlCaptor = org.mockito.ArgumentCaptor.forClass(String.class);
        var argumentsCaptor = org.mockito.ArgumentCaptor
                .forClass(Object[].class);
        verify(jdbc).update(
                sqlCaptor.capture(), argumentsCaptor.capture());
        assertEquals(quarantineUid, stored);
        assertTrue(normalize(sqlCaptor.getValue()).contains(
                "reason_code, raw_transport_sha256"));
        assertTrue(Arrays.asList(argumentsCaptor.getValue()).contains(
                "EVENT_TARGET_NOT_AUTHORITATIVE"));
        assertTrue(Arrays.asList(argumentsCaptor.getValue()).contains(
                "configuration progress references an obsolete target"));
    }

    @Test
    @SuppressWarnings("unchecked")
    void claimsPlatformAcceptanceConfirmationsBeforeAssignment()
            throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        LocalDateTime now = LocalDateTime.of(2026, 8, 7, 13, 0);
        when(jdbc.queryForObject(
                anyString(), eq(LocalDateTime.class))).thenReturn(now);
        when(jdbc.query(
                any(PreparedStatementCreator.class),
                any(RowMapper.class))).thenReturn(List.of());
        ReliableOperationsJdbcRepository repository =
                new ReliableOperationsJdbcRepository(jdbc);

        repository.claimDeviceCommandTasks(
                "platform-confirmation-test", 10, Duration.ofSeconds(30));

        var creatorCaptor = org.mockito.ArgumentCaptor.forClass(
                PreparedStatementCreator.class);
        verify(jdbc).query(creatorCaptor.capture(), any(RowMapper.class));
        Connection connection = mock(Connection.class);
        PreparedStatement statement = mock(PreparedStatement.class);
        var sqlCaptor = org.mockito.ArgumentCaptor.forClass(String.class);
        when(connection.prepareStatement(anyString())).thenReturn(statement);
        creatorCaptor.getValue().createPreparedStatement(connection);
        verify(connection).prepareStatement(sqlCaptor.capture());

        String sql = normalize(sqlCaptor.getValue());
        assertTrue(sql.contains(platformScope("candidate")));
        assertTrue(sql.contains(platformScope("t")));
    }

    @Test
    void reconcilesPlatformAcceptanceConfirmationTransportGate() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        ReliableOperationsJdbcRepository repository =
                new ReliableOperationsJdbcRepository(jdbc);

        repository.reconcileDeviceTaskGates(
                13L, LocalDateTime.of(2026, 8, 7, 13, 0));

        var sqlCaptor = org.mockito.ArgumentCaptor.forClass(String.class);
        verify(jdbc).update(sqlCaptor.capture(), any(Object[].class));
        String sql = normalize(sqlCaptor.getValue());
        assertTrue(sql.contains(platformScope("task")));
        assertTrue(sql.contains(
                "GREATEST(task.next_run_at, ?)"));
    }

    @Test
    void locksTransportStateWhenDerivingANewTaskGate() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.queryForObject(
                anyString(), eq(String.class), eq(13L)))
                .thenReturn("DEVICE_OFFLINE");
        ReliableOperationsJdbcRepository repository =
                new ReliableOperationsJdbcRepository(jdbc);

        String reason = repository.lockInitialDeviceDispatchWaitReason(13L);

        var sqlCaptor = org.mockito.ArgumentCaptor.forClass(String.class);
        verify(jdbc).queryForObject(
                sqlCaptor.capture(), eq(String.class), eq(13L));
        String sql = normalize(sqlCaptor.getValue());
        assertEquals("DEVICE_OFFLINE", reason);
        assertTrue(sql.contains("onenet_connection_status"));
        assertTrue(sql.endsWith("FOR UPDATE"));
    }

    private static String platformScope(String alias) {
        return alias + ".scope_kind = 'PLATFORM' "
                + "AND " + alias + ".tenant_id IS NULL "
                + "AND " + alias + ".organization_id IS NULL "
                + "AND " + alias + ".task_type IN ( "
                + "'REQUEST_DEVICE_ACCEPTANCE', "
                + "'AUTHORIZE_FACTORY_SEAL', "
                + "'SYNC_DEVICE_ENTRY_URL', "
                + "'OPEN_REMOTE_SUPPORT_TUNNEL', "
                + "'CLOSE_REMOTE_SUPPORT_TUNNEL', "
                + "'START_MCU_FIRMWARE_UPDATE', "
                + "'START_BUSINESS_RUNTIME_UPDATE', "
                + "'CANCEL_BUSINESS_RUNTIME_UPDATE', "
                + "'QUARANTINE_DELIVERY_RECOVERY', "
                + "'CONFIRM_EDGE_EVENT' )";
    }

    private static String normalize(String value) {
        return value.replaceAll("\\s+", " ").trim();
    }
}
