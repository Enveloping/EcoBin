package org.enveloping.ecobin.recycling.application.walletquery;

import org.enveloping.ecobin.funds.api.port.WalletQueryPort;
import org.enveloping.ecobin.funds.api.query.PersonalWalletEntryAudience;
import org.enveloping.ecobin.funds.api.result.WalletEntryPage;
import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.persistence.DeliveryQueryOrganizationUserRef;
import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletQueryOwnerRef;
import org.enveloping.ecobin.identity.api.persistence.WalletQueryScopeRef;
import org.enveloping.ecobin.identity.api.port.MiniappWalletIdentityQueryPort;
import org.enveloping.ecobin.identity.api.port.WalletQueryAuthorizationPort;
import org.enveloping.ecobin.identity.api.result.AuthorizedWalletScope;
import org.enveloping.ecobin.identity.api.result.CurrentMiniappWalletIdentity;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.List;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class WalletReadApplicationServiceTest {

    private static final UUID USER_UID =
            UUID.fromString("11111111-1111-4111-8111-111111111111");
    private static final Instant NOW =
            Instant.parse("2026-08-21T10:00:00Z");

    @Mock
    private MiniappWalletIdentityQueryPort miniappIdentity;

    @Mock
    private WalletQueryAuthorizationPort webAuthorization;

    @Mock
    private WalletQueryPort funds;

    @Mock
    private PendingDeliveryRewardRepository pendingRewards;

    @Mock
    private DeliveryWalletQueryOwnerRef miniappOwner;

    @Mock
    private DeliveryWalletQueryOwnerRef auditOwner;

    @Mock
    private DeliveryQueryOrganizationUserRef pendingOwner;

    @Mock
    private WalletQueryScopeRef scopeRef;

    private WalletReadApplicationService service;

    @BeforeEach
    void setUp() {
        service = new WalletReadApplicationService(
                miniappIdentity,
                webAuthorization,
                funds,
                pendingRewards,
                Clock.fixed(NOW, ZoneOffset.UTC));
        when(funds.personalEntries(any(), any(), any(), any()))
                .thenReturn(new WalletEntryPage(List.of(), NOW, null));
    }

    @Test
    void routesMiniappToOrdinaryViewAndWebToAuditView() {
        when(miniappIdentity.current()).thenReturn(
                new CurrentMiniappWalletIdentity(
                        new OrganizationUserUid(USER_UID),
                        pendingOwner,
                        miniappOwner));
        when(webAuthorization.authorize(any())).thenReturn(
                new AuthorizedWalletScope(
                        false,
                        "TENANT",
                        "ORG",
                        USER_UID,
                        scopeRef,
                        auditOwner,
                        pendingOwner));

        service.miniappEntries(null, 20);
        service.webPersonalEntries(
                false,
                null,
                "ORG",
                USER_UID,
                null,
                20);

        verify(funds).personalEntries(
                miniappOwner,
                PersonalWalletEntryAudience.ORDINARY_USER,
                null,
                20);
        verify(funds).personalEntries(
                auditOwner,
                PersonalWalletEntryAudience.AUDIT,
                null,
                20);
    }
}
