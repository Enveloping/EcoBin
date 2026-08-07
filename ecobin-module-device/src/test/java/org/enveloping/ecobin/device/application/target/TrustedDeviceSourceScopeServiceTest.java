package org.enveloping.ecobin.device.application.target;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;

import java.sql.ResultSet;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class TrustedDeviceSourceScopeServiceTest {

    private static final String CONFIRMATION_UID =
            "10000000-0000-4000-8000-000000000001";

    @Test
    void resolvesUnassignedAcceptanceConfirmationAsPlatform() {
        TrustedDeviceSourceScopeService service = service(
                "PLATFORM", null, null);
        AtomicReference<String> kind = new AtomicReference<>();
        AtomicReference<Long> tenant = new AtomicReference<>();
        AtomicReference<Long> organization = new AtomicReference<>();

        service.resolverForBusinessConfirmation(
                        "test-device-1", CONFIRMATION_UID)
                .resolve((scopeKind, tenantId, organizationId) -> {
                    kind.set(scopeKind);
                    tenant.set(tenantId);
                    organization.set(organizationId);
                });

        assertEquals("PLATFORM", kind.get());
        assertNull(tenant.get());
        assertNull(organization.get());
    }

    @Test
    void preservesOrganizationScopeForOperationalConfirmation() {
        TrustedDeviceSourceScopeService service = service(
                "ORGANIZATION", 7L, 8L);
        AtomicReference<String> kind = new AtomicReference<>();
        AtomicReference<Long> tenant = new AtomicReference<>();
        AtomicReference<Long> organization = new AtomicReference<>();

        service.resolverForBusinessConfirmation(
                        "test-device-1", CONFIRMATION_UID)
                .resolve((scopeKind, tenantId, organizationId) -> {
                    kind.set(scopeKind);
                    tenant.set(tenantId);
                    organization.set(organizationId);
                });

        assertEquals("ORGANIZATION", kind.get());
        assertEquals(7L, tenant.get());
        assertEquals(8L, organization.get());
    }

    @SuppressWarnings({"unchecked", "rawtypes"})
    private static TrustedDeviceSourceScopeService service(
            String scopeKind,
            Long tenantId,
            Long organizationId) {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.query(
                anyString(),
                any(RowMapper.class),
                any(Object[].class))).thenAnswer(invocation -> {
                    ResultSet resultSet = mock(ResultSet.class);
                    when(resultSet.getString("scope_kind"))
                            .thenReturn(scopeKind);
                    when(resultSet.getObject("tenant_id"))
                            .thenReturn(tenantId);
                    when(resultSet.getObject("organization_id"))
                            .thenReturn(organizationId);
                    RowMapper mapper = invocation.getArgument(1);
                    return List.of(mapper.mapRow(resultSet, 0));
                });
        return new TrustedDeviceSourceScopeService(jdbc);
    }
}
