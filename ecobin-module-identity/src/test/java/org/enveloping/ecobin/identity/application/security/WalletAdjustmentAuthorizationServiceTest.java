package org.enveloping.ecobin.identity.application.security;

import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.persistence.WalletAdjustmentScopeRef;
import org.enveloping.ecobin.identity.api.persistence.WalletAdjustmentTargetRef;
import org.enveloping.ecobin.identity.api.query.WalletAdjustmentAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedWalletAdjustment;
import org.enveloping.ecobin.identity.application.persistence.WalletAdjustmentRefFactory;
import org.enveloping.ecobin.identity.application.security.DeliveryScopeAuthorizationRepository.OrganizationUser;
import org.enveloping.ecobin.identity.application.security.DeliveryScopeAuthorizationRepository.Scope;
import org.enveloping.ecobin.identity.application.security.DeliveryScopeAuthorizationRepository.StaffActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.enveloping.ecobin.identity.application.web.WebAccountType;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class WalletAdjustmentAuthorizationServiceTest {

    private static final long STAFF_ID = 20;
    private static final long TENANT_ID = 30;
    private static final long ORGANIZATION_ID = 40;
    private static final long ORGANIZATION_USER_ID = 50;
    private static final long AUTH_VERSION = 7;
    private static final UUID PRINCIPAL_UID =
            UUID.fromString("11111111-1111-4111-8111-111111111111");
    private static final UUID SESSION_UID =
            UUID.fromString("22222222-2222-4222-8222-222222222222");
    private static final UUID ORGANIZATION_USER_UID =
            UUID.fromString("33333333-3333-4333-8333-333333333333");

    @Mock
    private DeliveryScopeAuthorizationRepository repository;

    @Mock
    private WalletAdjustmentRefFactory referenceFactory;

    @Mock
    private WalletAdjustmentScopeRef scopeRef;

    @Mock
    private WalletAdjustmentTargetRef targetRef;

    @AfterEach
    void tearDown() {
        TargetWebActorContext.clear();
    }

    @Test
    void exactWalletAdjustGrantAuthorizesAndLocksTheTarget() {
        WalletAdjustmentAuthorizationService service = service();
        TargetWebActorContext.set(staffActor());
        stubActorAndScope();
        when(repository.findTenantDeliveryCapabilities(TENANT_ID, STAFF_ID))
                .thenReturn(Set.of("wallet.adjust"));
        when(repository.findOrganizationUser(
                TENANT_ID, ORGANIZATION_ID, ORGANIZATION_USER_UID, true))
                .thenReturn(Optional.of(new OrganizationUser(
                        ORGANIZATION_USER_ID, ORGANIZATION_USER_UID)));
        when(referenceFactory.issueScope(TENANT_ID, ORGANIZATION_ID))
                .thenReturn(scopeRef);
        when(referenceFactory.issueTarget(
                TENANT_ID, ORGANIZATION_ID, ORGANIZATION_USER_ID,
                ORGANIZATION_USER_UID, null, STAFF_ID))
                .thenReturn(targetRef);

        AuthorizedWalletAdjustment result = service.authorize(query());

        assertThat(result.platformActor()).isFalse();
        assertThat(result.configurationScopeRef()).isSameAs(scopeRef);
        assertThat(result.fundsTargetRef()).isSameAs(targetRef);
        verify(repository, never()).findMembership(
                TENANT_ID, ORGANIZATION_ID, STAFF_ID);
    }

    @Test
    void walletReadDoesNotImplicitlyGrantWalletAdjustment() {
        WalletAdjustmentAuthorizationService service = service();
        TargetWebActorContext.set(staffActor());
        stubActorAndScope();
        when(repository.findTenantDeliveryCapabilities(TENANT_ID, STAFF_ID))
                .thenReturn(Set.of("wallet.read"));
        when(repository.findMembership(TENANT_ID, ORGANIZATION_ID, STAFF_ID))
                .thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.authorize(query()))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        failure -> assertThat(failure.code())
                                .isEqualTo("AUTH.CAPABILITY_REQUIRED"));
        verify(repository, never()).findOrganizationUser(
                TENANT_ID, ORGANIZATION_ID, ORGANIZATION_USER_UID, true);
    }

    private WalletAdjustmentAuthorizationService service() {
        return new WalletAdjustmentAuthorizationService(
                repository, referenceFactory);
    }

    private void stubActorAndScope() {
        when(repository.findStaffActor(STAFF_ID, PRINCIPAL_UID, true))
                .thenReturn(Optional.of(new StaffActor(
                        STAFF_ID, TENANT_ID, "STAFF", "工作人员",
                        true, AUTH_VERSION, "tenant-a", true)));
        when(repository.findStaffScope(TENANT_ID, "organization-a", true))
                .thenReturn(Optional.of(new Scope(
                        TENANT_ID, "tenant-a", ORGANIZATION_ID,
                        "organization-a", true)));
    }

    private static WalletAdjustmentAuthorizationQuery query() {
        return new WalletAdjustmentAuthorizationQuery(
                false, null, "organization-a", ORGANIZATION_USER_UID);
    }

    private static TargetWebActor staffActor() {
        return new TargetWebActor(
                WebAccountType.STAFF,
                TrustedAudience.WEB_STAFF,
                STAFF_ID,
                PRINCIPAL_UID,
                TENANT_ID,
                "tenant-a",
                "租户甲",
                SESSION_UID,
                "工作人员",
                null,
                1,
                AUTH_VERSION,
                Instant.parse("2030-01-01T00:00:00Z"),
                Set.of(),
                List.of());
    }
}
