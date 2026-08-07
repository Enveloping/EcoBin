package org.enveloping.ecobin.operations.infrastructure.persistence.reliability;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.PreparedStatementCreator;
import org.springframework.jdbc.core.RowMapper;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ReliableOperationsJdbcRepositorySqlTest {

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
        assertTrue(normalize(sqlCaptor.getValue()).contains(
                platformScope("task")));
    }

    private static String platformScope(String alias) {
        return alias + ".scope_kind = 'PLATFORM' "
                + "AND " + alias + ".tenant_id IS NULL "
                + "AND " + alias + ".organization_id IS NULL "
                + "AND " + alias + ".task_type IN ( "
                + "'REQUEST_DEVICE_ACCEPTANCE', "
                + "'CONFIRM_EDGE_EVENT' )";
    }

    private static String normalize(String value) {
        return value.replaceAll("\\s+", " ").trim();
    }
}
