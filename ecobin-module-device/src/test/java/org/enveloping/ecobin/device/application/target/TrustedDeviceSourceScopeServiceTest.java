package org.enveloping.ecobin.device.application.target;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;

import java.sql.ResultSet;
import java.time.Instant;
import java.time.LocalDateTime;
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

    @Test
    void resolvesUnassignedPermanentAssetFactAsPlatform() {
        TrustedDeviceSourceScopeService service = assetFactService(
                null, null, null);

        ResolvedScope scope = resolve(service, Instant.parse(
                "2026-08-10T00:00:00Z"));

        assertEquals("PLATFORM", scope.kind());
        assertNull(scope.tenantId());
        assertNull(scope.organizationId());
    }

    @Test
    void keepsPreAssignmentFactPlatformScopedAfterAssignment() {
        TrustedDeviceSourceScopeService service = assetFactService(
                7L,
                8L,
                LocalDateTime.of(2026, 8, 10, 0, 5));

        ResolvedScope scope = resolve(service, Instant.parse(
                "2026-08-10T00:04:59Z"));

        assertEquals("PLATFORM", scope.kind());
        assertNull(scope.tenantId());
        assertNull(scope.organizationId());
    }

    @Test
    void resolvesPostAssignmentFactAsOrganization() {
        TrustedDeviceSourceScopeService service = assetFactService(
                7L,
                8L,
                LocalDateTime.of(2026, 8, 10, 0, 5));

        ResolvedScope scope = resolve(service, Instant.parse(
                "2026-08-10T00:05:00Z"));

        assertEquals("ORGANIZATION", scope.kind());
        assertEquals(7L, scope.tenantId());
        assertEquals(8L, scope.organizationId());
    }

    @Test
    void keepsFactWithoutTrustedOccurredAtPlatformScoped() {
        TrustedDeviceSourceScopeService service = assetFactService(
                7L,
                8L,
                LocalDateTime.of(2026, 8, 10, 0, 5));

        ResolvedScope scope = resolve(service, null);

        assertEquals("PLATFORM", scope.kind());
        assertNull(scope.tenantId());
        assertNull(scope.organizationId());
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

    @SuppressWarnings({"unchecked", "rawtypes"})
    private static TrustedDeviceSourceScopeService assetFactService(
            Long tenantId,
            Long organizationId,
            LocalDateTime organizationAssignedAt) {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.query(
                anyString(),
                any(RowMapper.class),
                any(Object[].class))).thenAnswer(invocation -> {
                    ResultSet resultSet = mock(ResultSet.class);
                    when(resultSet.getObject("tenant_id"))
                            .thenReturn(tenantId);
                    when(resultSet.getObject("organization_id"))
                            .thenReturn(organizationId);
                    when(resultSet.getObject(
                            "organization_assigned_at",
                            LocalDateTime.class))
                            .thenReturn(organizationAssignedAt);
                    RowMapper mapper = invocation.getArgument(1);
                    return List.of(mapper.mapRow(resultSet, 0));
                });
        return new TrustedDeviceSourceScopeService(jdbc);
    }

    private static ResolvedScope resolve(
            TrustedDeviceSourceScopeService service,
            Instant occurredAt) {
        AtomicReference<String> kind = new AtomicReference<>();
        AtomicReference<Long> tenant = new AtomicReference<>();
        AtomicReference<Long> organization = new AtomicReference<>();
        service.resolverForPermanentAssetFact(
                        "test-device-4", occurredAt)
                .resolve((scopeKind, tenantId, organizationId) -> {
                    kind.set(scopeKind);
                    tenant.set(tenantId);
                    organization.set(organizationId);
                });
        return new ResolvedScope(
                kind.get(), tenant.get(), organization.get());
    }

    private record ResolvedScope(
            String kind,
            Long tenantId,
            Long organizationId) {
    }
}
