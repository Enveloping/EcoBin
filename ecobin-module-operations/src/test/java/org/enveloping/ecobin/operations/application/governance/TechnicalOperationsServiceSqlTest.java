package org.enveloping.ecobin.operations.application.governance;

import tools.jackson.databind.ObjectMapper;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationIdempotencyPort;
import org.enveloping.ecobin.identity.api.persistence.ManagementScopePersistenceRef;
import org.enveloping.ecobin.identity.api.port.ManagementScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.result.AuthorizedManagementScope;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.QuarantineView;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.ReliableTaskView;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;

import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class TechnicalOperationsServiceSqlTest {

    @Test
    void reliableTaskListKeepsWhereAndOrderBySeparatedWithoutFilters() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        TechnicalOperationsService service = service(jdbc);
        when(jdbc.queryForObject(anyString(), eq(Long.class),
                any(Object[].class))).thenReturn(0L);
        when(jdbc.query(anyString(),
                org.mockito.ArgumentMatchers
                        .<RowMapper<ReliableTaskView>>any(),
                any(Object[].class))).thenReturn(List.of());

        service.tasks(null, null, null, null, null, null,
                null, null, null, null);

        ArgumentCaptor<String> sql = ArgumentCaptor.forClass(String.class);
        verify(jdbc).query(sql.capture(),
                org.mockito.ArgumentMatchers.<RowMapper<ReliableTaskView>>any(),
                any(Object[].class));
        assertThat(sql.getValue())
                .contains("WHERE 1 = 1\nORDER BY task.updated_at");
    }

    @Test
    void quarantineListKeepsWhereAndOrderBySeparatedWithoutFilters() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        TechnicalOperationsService service = service(jdbc);
        when(jdbc.queryForObject(anyString(), eq(Long.class),
                any(Object[].class))).thenReturn(0L);
        when(jdbc.query(anyString(),
                org.mockito.ArgumentMatchers
                        .<RowMapper<QuarantineView>>any(),
                any(Object[].class))).thenReturn(List.of());

        service.quarantines(null, null, null, null, null, null, null);

        ArgumentCaptor<String> sql = ArgumentCaptor.forClass(String.class);
        verify(jdbc).query(sql.capture(),
                org.mockito.ArgumentMatchers.<RowMapper<QuarantineView>>any(),
                any(Object[].class));
        assertThat(sql.getValue())
                .contains("WHERE 1 = 1\nORDER BY quarantine.first_seen_at");
    }

    @Test
    void taskTypeCatalogScansObservedTypesOnlyOncePerApplicationInstance() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        TechnicalOperationsService service = service(jdbc);
        when(jdbc.query(anyString(),
                org.mockito.ArgumentMatchers
                        .<RowMapper<ReliableTaskTypeCatalog.Observation>>any()))
                .thenReturn(List.of());

        service.taskTypes();
        service.taskTypes();

        verify(jdbc, times(1)).query(anyString(),
                org.mockito.ArgumentMatchers
                        .<RowMapper<ReliableTaskTypeCatalog.Observation>>any());
    }

    private static TechnicalOperationsService service(JdbcTemplate jdbc) {
        ManagementScopeAuthorizationPort authorization =
                mock(ManagementScopeAuthorizationPort.class);
        var scope = new AuthorizedManagementScope(
                true,
                UUID.randomUUID(),
                UUID.randomUUID(),
                "test-admin",
                null,
                false,
                List.of(),
                mock(ManagementScopePersistenceRef.class));
        when(authorization.authorize(any())).thenReturn(scope);
        return new TechnicalOperationsService(
                jdbc,
                authorization,
                mock(AuditPort.class),
                mock(ObjectMapper.class),
                mock(GlobalOperationIdempotencyPort.class));
    }
}
