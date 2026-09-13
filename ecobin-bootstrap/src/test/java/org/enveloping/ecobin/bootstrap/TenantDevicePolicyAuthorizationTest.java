package org.enveloping.ecobin.bootstrap;

import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.query.DeviceScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.persistence.DeviceScopePersistenceRef;
import org.enveloping.ecobin.identity.application.security.DeviceScopeAuthorizationService;
import org.enveloping.ecobin.identity.application.security.DeviceScopePersistenceRefFactory;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.enveloping.ecobin.identity.application.web.WebAccountType;
import org.enveloping.ecobin.identity.web.v1.auth.WebSessionView;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;

import java.time.Instant;
import java.util.List;
import java.util.Set;
import java.util.UUID;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

/** Uses the real permission SQL: flattened organization capabilities must not authorize tenant settings. */
class TenantDevicePolicyAuthorizationTest {
    private final UUID principal = UUID.randomUUID();
    private JdbcTemplate jdbc;
    private DeviceScopeAuthorizationService service;
    private DeviceScopePersistenceRefFactory refs;
    private static final String CAPABILITY = "device.configuration.manage";

    @BeforeEach
    void setup() {
        jdbc = new JdbcTemplate(new DriverManagerDataSource("jdbc:h2:mem:" + UUID.randomUUID() + ";MODE=MySQL;DB_CLOSE_DELAY=-1", "sa", ""));
        jdbc.execute("CREATE TABLE iam_tenant(id BIGINT, tenant_code VARCHAR, status VARCHAR)");
        jdbc.execute("CREATE TABLE iam_staff_account(id BIGINT, tenant_id BIGINT, staff_account_uid VARCHAR, account_kind VARCHAR, display_name VARCHAR, enabled BOOLEAN, auth_version BIGINT)");
        jdbc.execute("CREATE TABLE iam_permission_definition(id BIGINT, scope_kind VARCHAR, permission_code VARCHAR, enabled INT)");
        jdbc.execute("CREATE TABLE iam_staff_permission_grant(tenant_id BIGINT, staff_account_id BIGINT, organization_id BIGINT, scope_kind VARCHAR, permission_definition_id BIGINT, revoked_at TIMESTAMP)");
        jdbc.update("INSERT INTO iam_tenant VALUES (7, 'tenant-a', 'ENABLED'), (8, 'tenant-b', 'ENABLED')");
        jdbc.update("INSERT INTO iam_staff_account VALUES (2, 7, ?, 'STAFF', '测试员工', TRUE, 1)", principal.toString());
        jdbc.update("INSERT INTO iam_permission_definition VALUES (1, 'TENANT', ?, 1), (2, 'ORGANIZATION', ?, 1)", CAPABILITY, CAPABILITY);
        refs = mock(DeviceScopePersistenceRefFactory.class);
        when(refs.issue(any(), any(), any(), any())).thenReturn(mock(DeviceScopePersistenceRef.class));
        service = new DeviceScopeAuthorizationService(jdbc, refs);
        TargetWebActorContext.set(actor());
    }

    private TargetWebActor actor() {
        return new TargetWebActor(WebAccountType.STAFF, TrustedAudience.WEB_STAFF, 2, principal, 7L, "tenant-a", "租户甲",
                UUID.randomUUID(), "员工", null, 1, 1, Instant.parse("2030-01-01T00:00:00Z"), Set.of(), List.of());
    }

    @AfterEach void cleanup() { TargetWebActorContext.clear(); }

    @Test void organizationGrantCannotManageTenantPolicy() {
        jdbc.update("INSERT INTO iam_staff_permission_grant VALUES (7, 2, 9, 'ORGANIZATION', 2, NULL)");
        assertThatThrownBy(() -> service.authorize(query())).isInstanceOf(TargetApiException.class);
        verifyNoInteractions(refs);
    }

    @Test void tenantGrantIsRevalidatedAndReturnsOnlySignedInTenant() {
        jdbc.update("INSERT INTO iam_staff_permission_grant VALUES (7, 2, NULL, 'TENANT', 1, NULL)");
        var scope = service.authorize(query());
        assertThat(scope.tenantCode()).isEqualTo("tenant-a");
        assertThat(scope.organizationCode()).isNull();
        verify(refs).issue(7L, null, null, 2L);
        jdbc.update("UPDATE iam_staff_permission_grant SET revoked_at = CURRENT_TIMESTAMP");
        assertThatThrownBy(() -> service.authorize(query())).isInstanceOf(TargetApiException.class);
    }

    @Test void anotherTenantsGrantCannotAuthorizeAndClientCannotSelectAnotherTenant() {
        jdbc.update("INSERT INTO iam_staff_permission_grant VALUES (8, 2, NULL, 'TENANT', 1, NULL)");
        assertThatThrownBy(() -> service.authorize(query())).isInstanceOf(TargetApiException.class);
        assertThatThrownBy(() -> new DeviceScopeAuthorizationQuery(false, "tenant-b", null, CAPABILITY)).isInstanceOf(IllegalArgumentException.class);
    }

    @Test void tenantPrincipalNeedsNoExplicitGrantAndStaleSessionFails() {
        jdbc.update("UPDATE iam_staff_account SET account_kind = 'TENANT_PRINCIPAL'");
        assertThat(service.authorize(query()).tenantCode()).isEqualTo("tenant-a");
        jdbc.update("UPDATE iam_staff_account SET auth_version = 2");
        assertThatThrownBy(() -> service.authorize(query())).isInstanceOf(TargetApiException.class);
    }

    @Test void sessionExposesTenantCapabilitiesSeparately() {
        assertThat(WebSessionView.from(actor()).tenantCapabilities()).isEmpty();
    }

    private DeviceScopeAuthorizationQuery query() { return new DeviceScopeAuthorizationQuery(false, null, null, CAPABILITY); }
}
