package org.enveloping.ecobin.recycling.application.delivery;

import org.enveloping.ecobin.device.api.port.StartDeliveryDeviceParticipationPort;
import org.enveloping.ecobin.device.api.result.StartDeliveryDeviceResult;
import org.enveloping.ecobin.device.api.value.DeliveryRuleSnapshot;
import org.enveloping.ecobin.funds.api.id.WalletUid;
import org.enveloping.ecobin.funds.api.port.StartDeliveryWalletQualificationPort;
import org.enveloping.ecobin.funds.api.result.QualifiedDeliveryWallet;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.id.SessionUid;
import org.enveloping.ecobin.identity.api.persistence.DeliverySessionOrganizationUserRef;
import org.enveloping.ecobin.identity.api.persistence.StartDeliveryAuditActorRef;
import org.enveloping.ecobin.identity.api.persistence.StartDeliveryOrganizationScopeRef;
import org.enveloping.ecobin.identity.api.persistence.StartDeliveryWalletOwnerRef;
import org.enveloping.ecobin.identity.api.port.StartDeliveryIdentityParticipationPort;
import org.enveloping.ecobin.identity.api.result.LockedDeliveryOrganizationUser;
import org.enveloping.ecobin.identity.api.result.LockedMiniappDeliveryScope;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import tools.jackson.databind.ObjectMapper;

import java.sql.ResultSet;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class StartDeliverySessionServiceTest {

    private static final long TENANT_ID = 11L;
    private static final long ORGANIZATION_ID = 12L;
    private static final long ORGANIZATION_USER_ID = 13L;
    private static final UUID OPERATION_UID = UUID.fromString(
            "10000000-0000-4000-8000-000000000001");
    private static final UUID ORGANIZATION_USER_UID = UUID.fromString(
            "20000000-0000-4000-8000-000000000001");
    private static final UUID LOGIN_SESSION_UID = UUID.fromString(
            "30000000-0000-4000-8000-000000000001");
    private static final UUID DELIVERY_SESSION_UID = UUID.fromString(
            "40000000-0000-4000-8000-000000000001");
    private static final UUID COMMAND_UID = UUID.fromString(
            "50000000-0000-4000-8000-000000000001");
    private static final String DEPLOYMENT_CODE = "Dp_demo_01";
    private static final Instant AUTHORIZATION_EXPIRES_AT =
            Instant.parse("2026-07-29T03:01:00Z");
    private static final DeliveryRuleSnapshot DELIVERY_RULE =
            new DeliveryRuleSnapshot(
                    3L,
                    "aa".repeat(32),
                    -500L,
                    100_000L);

    @Mock
    private JdbcTemplate jdbc;
    @Mock
    private StartDeliveryIdentityParticipationPort identity;
    @Mock
    private StartDeliveryWalletQualificationPort wallet;
    @Mock
    private StartDeliveryDeviceParticipationPort device;
    @Mock
    private AuditPort audit;
    @Mock
    private StartDeliveryOrganizationScopeRef organizationScopeRef;
    @Mock
    private StartDeliveryWalletOwnerRef walletOwnerRef;
    @Mock
    private DeliverySessionOrganizationUserRef deliverySessionUserRef;
    @Mock
    private StartDeliveryAuditActorRef auditActorRef;

    private StartDeliverySessionService service;

    @BeforeEach
    @SuppressWarnings("unchecked")
    void setUp() {
        service = new StartDeliverySessionService(
                jdbc,
                identity,
                wallet,
                device,
                audit,
                new ObjectMapper());

        LockedMiniappDeliveryScope scope =
                new LockedMiniappDeliveryScope(
                        "tenant-demo",
                        "org-demo",
                        "wx-app-demo",
                        new SessionUid(LOGIN_SESSION_UID),
                        organizationScopeRef);
        LockedDeliveryOrganizationUser user =
                new LockedDeliveryOrganizationUser(
                        new OrganizationUserUid(
                                ORGANIZATION_USER_UID),
                        new SessionUid(LOGIN_SESSION_UID),
                        walletOwnerRef,
                        deliverySessionUserRef,
                        auditActorRef);
        when(identity.lockCurrentMiniappScope()).thenReturn(scope);
        when(identity.lockCurrentOrganizationUser(scope))
                .thenReturn(user);
        when(organizationScopeRef.withOrganizationScopeOnce(any()))
                .thenAnswer(invocation -> {
                    StartDeliveryOrganizationScopeRef
                            .OrganizationScopeFunction<?> function =
                            invocation.getArgument(0);
                    return function.apply(
                            TENANT_ID,
                            ORGANIZATION_ID);
                });
        when(auditActorRef.withAuditActorOnce(any()))
                .thenAnswer(invocation -> {
                    StartDeliveryAuditActorRef
                            .AuditActorFunction<?> function =
                            invocation.getArgument(0);
                    return function.apply(
                            TENANT_ID,
                            ORGANIZATION_ID,
                            ORGANIZATION_USER_ID);
                });
        when(jdbc.query(
                anyString(),
                any(RowMapper.class),
                eq(TENANT_ID),
                eq(ORGANIZATION_ID)))
                .thenAnswer(invocation -> {
                    ResultSet head = mock(ResultSet.class);
                    when(head.getLong("current_config_id"))
                            .thenReturn(91L);
                    when(head.getLong("current_version_no"))
                            .thenReturn(DELIVERY_RULE.version());
                    RowMapper<?> mapper = invocation.getArgument(1);
                    return List.of(mapper.mapRow(head, 0));
                });
        when(jdbc.query(
                anyString(),
                any(RowMapper.class),
                eq(TENANT_ID),
                eq(ORGANIZATION_ID),
                eq(91L),
                eq(DELIVERY_RULE.version())))
                .thenReturn(List.of(DELIVERY_RULE));
        when(wallet.lockAndRequireEligible(any()))
                .thenReturn(new QualifiedDeliveryWallet(
                        new WalletUid(UUID.fromString(
                                "60000000-0000-4000-8000-000000000001")),
                        0L,
                        -500L));
        when(device.start(any())).thenReturn(
                new StartDeliveryDeviceResult(
                        DELIVERY_SESSION_UID,
                        COMMAND_UID,
                        AUTHORIZATION_EXPIRES_AT,
                        DEPLOYMENT_CODE,
                        2));
        when(audit.findSuccessful(OPERATION_UID))
                .thenReturn(Optional.empty());
    }

    @Test
    void duplicateSuccessfulOperationAuditReturnsPublicConflict() {
        doThrow(new DuplicateKeyException(
                "succeeded operation is already owned"))
                .when(audit)
                .append(any());

        assertThatThrownBy(() -> service.start(
                OPERATION_UID,
                DEPLOYMENT_CODE,
                2))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        exception -> {
                            assertThat(exception.status())
                                    .isEqualTo(409);
                            assertThat(exception.code()).isEqualTo(
                                    "COMMON.IDEMPOTENCY_KEY_CONFLICT");
                        });

        verify(device).start(any());
        verify(audit).append(any());
    }

    @Test
    void sameActorAndRequestReplayTheStoredSuccessWithoutStartingAgain() {
        var first = service.start(
                OPERATION_UID,
                DEPLOYMENT_CODE,
                2);
        ArgumentCaptor<AuditEntry> auditCaptor =
                ArgumentCaptor.forClass(AuditEntry.class);
        verify(audit).append(auditCaptor.capture());
        AuditEntry stored = auditCaptor.getValue();
        when(audit.findSuccessful(OPERATION_UID)).thenReturn(
                Optional.of(successfulAudit(stored)));

        var replayed = service.start(
                OPERATION_UID,
                DEPLOYMENT_CODE,
                2);

        assertThat(replayed).isEqualTo(first);
        verify(wallet, times(1)).lockAndRequireEligible(any());
        verify(device, times(1)).start(any());
        verify(audit, times(1)).append(any());
    }

    private static SuccessfulAudit successfulAudit(
            AuditEntry entry) {
        return new SuccessfulAudit(
                entry.operationUid(),
                entry.actorKind(),
                entry.platformAdminId(),
                entry.staffAccountId(),
                entry.organizationUserId(),
                entry.scopeKind(),
                entry.tenantId(),
                entry.organizationId(),
                entry.actionCode(),
                entry.targetType(),
                entry.targetStableKey(),
                entry.safeChangeSummaryJson());
    }
}
