package org.enveloping.ecobin.identity.application.security;

import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.persistence.DeliveryQueryOrganizationUserRef;
import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletQueryOwnerRef;
import org.enveloping.ecobin.identity.api.persistence.WalletQueryScopeRef;
import org.enveloping.ecobin.identity.api.query.WalletScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedWalletScope;
import org.enveloping.ecobin.identity.application.persistence.DeliveryIdentityQueryRefFactory;
import org.enveloping.ecobin.identity.application.security.DeliveryScopeAuthorizationRepository.Membership;
import org.enveloping.ecobin.identity.application.security.DeliveryScopeAuthorizationRepository.OrganizationUser;
import org.enveloping.ecobin.identity.application.security.DeliveryScopeAuthorizationRepository.Scope;
import org.enveloping.ecobin.identity.application.security.DeliveryScopeAuthorizationRepository.StaffActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.enveloping.ecobin.identity.application.web.WebAccountType;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
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
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class WalletQueryAuthorizationServiceTest {

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
    private static final String TENANT_CODE = "tenant-a";
    private static final String ORGANIZATION_CODE = "organization-a";

    @Mock
    private DeliveryScopeAuthorizationRepository repository;

    @Mock
    private DeliveryIdentityQueryRefFactory referenceFactory;

    @Mock
    private WalletQueryScopeRef scopeRef;

    @Mock
    private DeliveryWalletQueryOwnerRef walletOwnerRef;

    @Mock
    private DeliveryQueryOrganizationUserRef pendingRewardOwnerRef;

    private WalletQueryAuthorizationService service;

    @BeforeEach
    void setUp() {
        service = new WalletQueryAuthorizationService(
                repository,
                referenceFactory);
        lenient().when(referenceFactory.issueWalletScope(
                        anyLong(),
                        anyLong(),
                        any(),
                        any()))
                .thenReturn(scopeRef);
        lenient().when(referenceFactory.issueWalletQueryOwner(
                        anyLong(),
                        anyLong(),
                        anyLong(),
                        any()))
                .thenReturn(walletOwnerRef);
        lenient().when(referenceFactory.issueDeliveryQueryUser(
                        anyLong(),
                        anyLong(),
                        anyLong(),
                        any()))
                .thenReturn(pendingRewardOwnerRef);
    }

    @AfterEach
    void tearDown() {
        TargetWebActorContext.clear();
    }

    @Test
    void tenantWalletGrantCanReadTargetWalletWithoutDeliveryGrant() {
        TargetWebActorContext.set(staffActor());
        stubActorAndScope();
        when(repository.findTenantDeliveryCapabilities(
                TENANT_ID,
                STAFF_ID))
                .thenReturn(Set.of("wallet.read"));
        when(repository.findOrganizationUser(
                TENANT_ID,
                ORGANIZATION_ID,
                ORGANIZATION_USER_UID,
                false))
                .thenReturn(Optional.of(new OrganizationUser(
                        ORGANIZATION_USER_ID,
                        ORGANIZATION_USER_UID)));

        AuthorizedWalletScope result = service.authorize(
                personalQuery());

        assertThat(result.scopeRef()).isSameAs(scopeRef);
        assertThat(result.walletOwnerRef())
                .isSameAs(walletOwnerRef);
        assertThat(result.pendingRewardOwnerRef())
                .isSameAs(pendingRewardOwnerRef);
        verify(repository, never()).findMembership(
                anyLong(),
                anyLong(),
                anyLong());
    }

    @Test
    void deliveryReadAloneDoesNotGrantWalletRead() {
        TargetWebActorContext.set(staffActor());
        stubActorAndScope();
        when(repository.findTenantDeliveryCapabilities(
                TENANT_ID,
                STAFF_ID))
                .thenReturn(Set.of("delivery.read"));
        when(repository.findMembership(
                TENANT_ID,
                ORGANIZATION_ID,
                STAFF_ID))
                .thenReturn(Optional.of(new Membership(false, true)));
        when(repository.findOrganizationDeliveryCapabilities(
                TENANT_ID,
                ORGANIZATION_ID,
                STAFF_ID))
                .thenReturn(Set.of("delivery.read"));

        assertThatThrownBy(() ->
                service.authorize(personalQuery()))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        failure -> {
                            assertThat(failure.status()).isEqualTo(403);
                            assertThat(failure.code())
                                    .isEqualTo(
                                            "AUTH.CAPABILITY_REQUIRED");
                        });
        verify(repository, never()).findOrganizationUser(
                anyLong(),
                anyLong(),
                any(),
                anyBoolean());
    }

    @Test
    void walletAdjustGrantCanReadOnlyTheExactAdjustmentSummary() {
        TargetWebActorContext.set(staffActor());
        stubActorAndScope();
        when(repository.findTenantDeliveryCapabilities(
                TENANT_ID,
                STAFF_ID))
                .thenReturn(Set.of("wallet.adjust"));
        when(repository.findOrganizationUser(
                TENANT_ID,
                ORGANIZATION_ID,
                ORGANIZATION_USER_UID,
                false))
                .thenReturn(Optional.of(new OrganizationUser(
                        ORGANIZATION_USER_ID,
                        ORGANIZATION_USER_UID)));

        AuthorizedWalletScope result = service.authorize(
                new WalletScopeAuthorizationQuery(
                        false,
                        null,
                        ORGANIZATION_CODE,
                        ORGANIZATION_USER_UID,
                        true));

        assertThat(result.walletOwnerRef()).isSameAs(walletOwnerRef);
        assertThat(result.pendingRewardOwnerRef())
                .isSameAs(pendingRewardOwnerRef);
    }

    @Test
    void walletAdjustGrantDoesNotOpenWalletHistoryAuthorization() {
        TargetWebActorContext.set(staffActor());
        stubActorAndScope();
        when(repository.findTenantDeliveryCapabilities(
                TENANT_ID,
                STAFF_ID))
                .thenReturn(Set.of("wallet.adjust"));
        when(repository.findMembership(
                TENANT_ID,
                ORGANIZATION_ID,
                STAFF_ID))
                .thenReturn(Optional.of(new Membership(false, true)));
        when(repository.findOrganizationDeliveryCapabilities(
                TENANT_ID,
                ORGANIZATION_ID,
                STAFF_ID))
                .thenReturn(Set.of());

        assertThatThrownBy(() -> service.authorize(personalQuery()))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        failure -> assertThat(failure.code())
                                .isEqualTo("AUTH.CAPABILITY_REQUIRED"));
        verify(repository, never()).findOrganizationUser(
                anyLong(), anyLong(), any(), anyBoolean());
    }

    @Test
    void organizationManagerCanReadOrganizationTimeline() {
        TargetWebActorContext.set(staffActor());
        stubActorAndScope();
        when(repository.findTenantDeliveryCapabilities(
                TENANT_ID,
                STAFF_ID))
                .thenReturn(Set.of());
        when(repository.findMembership(
                TENANT_ID,
                ORGANIZATION_ID,
                STAFF_ID))
                .thenReturn(Optional.of(new Membership(true, true)));

        AuthorizedWalletScope result = service.authorize(
                new WalletScopeAuthorizationQuery(
                        false,
                        null,
                        ORGANIZATION_CODE,
                        null));

        assertThat(result.scopeRef()).isSameAs(scopeRef);
        assertThat(result.organizationUserUid()).isNull();
        assertThat(result.walletOwnerRef()).isNull();
        assertThat(result.pendingRewardOwnerRef()).isNull();
        verify(repository, never())
                .findOrganizationDeliveryCapabilities(
                        anyLong(),
                        anyLong(),
                        anyLong());
    }

    private void stubActorAndScope() {
        when(repository.findStaffActor(
                STAFF_ID,
                PRINCIPAL_UID,
                false))
                .thenReturn(Optional.of(new StaffActor(
                        STAFF_ID,
                        TENANT_ID,
                        "STAFF",
                        "工作人员",
                        true,
                        AUTH_VERSION,
                        TENANT_CODE,
                        true)));
        when(repository.findStaffScope(
                TENANT_ID,
                ORGANIZATION_CODE,
                false))
                .thenReturn(Optional.of(new Scope(
                        TENANT_ID,
                        TENANT_CODE,
                        ORGANIZATION_ID,
                        ORGANIZATION_CODE,
                        true)));
    }

    private static WalletScopeAuthorizationQuery personalQuery() {
        return new WalletScopeAuthorizationQuery(
                false,
                null,
                ORGANIZATION_CODE,
                ORGANIZATION_USER_UID);
    }

    private static TargetWebActor staffActor() {
        return new TargetWebActor(
                WebAccountType.STAFF,
                TrustedAudience.WEB_STAFF,
                STAFF_ID,
                PRINCIPAL_UID,
                TENANT_ID,
                TENANT_CODE,
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
