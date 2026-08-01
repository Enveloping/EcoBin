package org.enveloping.ecobin.recycling.application.deliveryorder;

import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.funds.api.command.ApplyDeliveryRevisionDeltaCommand;
import org.enveloping.ecobin.funds.api.id.WalletEntryUid;
import org.enveloping.ecobin.funds.api.port.ApplyDeliveryRevisionDeltaPort;
import org.enveloping.ecobin.funds.api.result.AppliedDeliveryWalletDelta;
import org.enveloping.ecobin.funds.api.result.DeliveryGateEffect;
import org.enveloping.ecobin.funds.api.result.WithdrawalBalanceEffect;
import org.enveloping.ecobin.funds.api.value.DeliveryRevisionKind;
import org.enveloping.ecobin.identity.api.error.DeliveryIdentityFactMismatchException;
import org.enveloping.ecobin.identity.api.error.DeliveryIdentityFactMismatchException.Reason;
import org.enveloping.ecobin.identity.api.persistence.DeliveryScopePersistenceRef;
import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletEntryOwnerRef;
import org.enveloping.ecobin.identity.api.port.DeliveryScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.port.DeliveryWalletEntryOwnerResolverPort;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeliveryScope;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.DeliveryReviewResult;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.ReviewDeliveryOrderRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class DeliveryOrderReviewServiceTest {

    private static final long TENANT_ID = 11L;
    private static final long ORGANIZATION_ID = 22L;
    private static final long ORGANIZATION_USER_ID = 33L;
    private static final long STAFF_ACCOUNT_ID = 44L;
    private static final long REVISION_ID = 55L;
    private static final long STOP_THRESHOLD_CENT = -100L;
    private static final String ORDER_NO = "DO202607290001";
    private static final UUID OPERATION_UID = UUID.fromString(
            "10000000-0000-4000-8000-000000000001");
    private static final UUID PRINCIPAL_UID = UUID.fromString(
            "20000000-0000-4000-8000-000000000001");
    private static final UUID SESSION_UID = UUID.fromString(
            "30000000-0000-4000-8000-000000000001");
    private static final UUID ORGANIZATION_USER_UID = UUID.fromString(
            "40000000-0000-4000-8000-000000000001");
    private static final UUID WALLET_ENTRY_UID = UUID.fromString(
            "50000000-0000-4000-8000-000000000001");
    private static final Instant REVIEWED_AT =
            Instant.parse("2026-07-29T08:40:15.123Z");
    private static final LocalDateTime REVIEWED_AT_DATABASE =
            LocalDateTime.ofInstant(REVIEWED_AT, ZoneOffset.UTC);

    @Mock
    private DeliveryScopeAuthorizationPort authorization;
    @Mock
    private DeliveryWalletEntryOwnerResolverPort walletOwnerResolver;
    @Mock
    private DeliveryWalletEntryOwnerRef walletOwnerRef;
    @Mock
    private JdbcDeliveryOrderRepository repository;
    @Mock
    private ApplyDeliveryRevisionDeltaPort funds;
    @Mock
    private AuditPort audit;
    @Mock
    private DeliveryScopePersistenceRef persistenceRef;

    private DeliveryOrderReviewService service;
    private Object transactionResourceKey;

    @BeforeEach
    @SuppressWarnings("unchecked")
    void setUp() {
        transactionResourceKey = new Object();
        TransactionSynchronizationManager
                .setActualTransactionActive(true);
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(false);
        TransactionSynchronizationManager.initSynchronization();
        TransactionSynchronizationManager.bindResource(
                transactionResourceKey,
                new Object());

        service = new DeliveryOrderReviewService(
                authorization,
                walletOwnerResolver,
                repository,
                funds,
                audit,
                new ObjectMapper(),
                Clock.fixed(REVIEWED_AT, ZoneOffset.UTC));

        lenient().when(authorization.authorize(any()))
                .thenReturn(authorized(true, true, true));
        lenient().when(persistenceRef.withScopeOnce(any()))
                .thenAnswer(invocation -> {
                    DeliveryScopePersistenceRef.ScopeFunction<Object>
                            function = invocation.getArgument(0);
                    return function.apply(
                            TENANT_ID,
                            ORGANIZATION_ID,
                            null,
                            STAFF_ACCOUNT_ID);
                });
        lenient().when(audit.findSuccessful(any()))
                .thenReturn(Optional.empty());
        lenient().when(repository.lockCurrentOpenBalanceFloor(any()))
                .thenReturn(STOP_THRESHOLD_CENT);
        lenient().when(repository.insertRevision(any(), any(), any()))
                .thenAnswer(invocation -> {
                    DeliveryRevisionInsert insert =
                            invocation.getArgument(2);
                    return new InsertedDeliveryRevision(
                            REVISION_ID,
                            insert.revisionUid(),
                            insert.revisionNo());
                });
        lenient().when(walletOwnerResolver.resolve(any()))
                .thenReturn(walletOwnerRef);
        lenient().when(walletOwnerRef.withWalletEntryOwnerOnce(any()))
                .thenAnswer(invocation -> {
                    DeliveryWalletEntryOwnerRef
                            .WalletEntryOwnerFunction<Object> function =
                            invocation.getArgument(0);
                    return function.apply(
                            TENANT_ID,
                            ORGANIZATION_ID,
                            ORGANIZATION_USER_ID,
                            ORGANIZATION_USER_UID);
                });
        lenient().when(funds.applyDeliveryRevisionDelta(any()))
                .thenAnswer(invocation ->
                        applyFunds(invocation.getArgument(0)));
    }

    @AfterEach
    void tearDown() {
        if (TransactionSynchronizationManager
                .isSynchronizationActive()) {
            TransactionSynchronizationManager
                    .clearSynchronization();
        }
        if (TransactionSynchronizationManager
                .hasResource(transactionResourceKey)) {
            TransactionSynchronizationManager
                    .unbindResource(transactionResourceKey);
        }
        TransactionSynchronizationManager
                .setActualTransactionActive(false);
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(false);
    }

    @Test
    void firstReviewWithReliableOriginalFactsCreatesInitialRevision() {
        LockedDeliveryOrderRow order = pendingOrder(
                new BigDecimal("-1.25"),
                -100L,
                "RELIABLE");
        when(repository.lockOrder(any(), any()))
                .thenReturn(Optional.of(order));

        DeliveryReviewResult result = service.review(
                false,
                null,
                "org-demo",
                ORDER_NO,
                OPERATION_UID,
                request(
                        0,
                        "ORIGINAL_APPROVED",
                        null,
                        null));

        assertThat(result.reviewStatus()).isEqualTo("APPROVED");
        assertThat(result.decision())
                .isEqualTo("ORIGINAL_APPROVED");
        assertThat(result.finalWeightKg()).isEqualTo("-1.25");
        assertThat(result.finalAmountYuan()).isEqualTo("-1.00");
        assertThat(result.walletDeltaYuan()).isEqualTo("-1.00");
        assertThat(result.walletEffect()).isEqualTo("APPLIED");
        assertThat(result.reviewedAt()).isEqualTo(REVIEWED_AT);

        ArgumentCaptor<DeliveryRevisionInsert> revisionCaptor =
                ArgumentCaptor.forClass(DeliveryRevisionInsert.class);
        verify(repository).insertRevision(
                any(),
                any(),
                revisionCaptor.capture());
        DeliveryRevisionInsert insert = revisionCaptor.getValue();
        assertThat(insert.revisionNo()).isEqualTo(1);
        assertThat(insert.revisionType())
                .isEqualTo("INITIAL_REVIEW");
        assertThat(insert.decision())
                .isEqualTo("ORIGINAL_APPROVED");
        assertThat(insert.afterWeightKg())
                .isEqualByComparingTo("-1.25");
        assertThat(insert.afterAmountCent()).isEqualTo(-100);
        assertThat(insert.amountDeltaCent()).isEqualTo(-100);
        assertThat(insert.reviewedAt())
                .isEqualTo(REVIEWED_AT_DATABASE);

        ArgumentCaptor<ApplyDeliveryRevisionDeltaCommand>
                fundsCaptor =
                ArgumentCaptor.forClass(
                        ApplyDeliveryRevisionDeltaCommand.class);
        verify(funds).applyDeliveryRevisionDelta(
                fundsCaptor.capture());
        assertThat(fundsCaptor.getValue().revisionKind())
                .isEqualTo(DeliveryRevisionKind.INITIAL_REVIEW);
        assertThat(fundsCaptor.getValue().deltaCent())
                .isEqualTo(-100);
    }

    @Test
    void modifiedInitialReviewCallsFundsWithCalculatedDelta() {
        when(repository.lockOrder(any(), any()))
                .thenReturn(Optional.of(pendingOrder(
                        null,
                        null,
                        "INVALID")));

        DeliveryReviewResult result = service.review(
                false,
                null,
                "org-demo",
                ORDER_NO,
                OPERATION_UID,
                request(
                        0,
                        "MODIFIED_APPROVED",
                        "2.00",
                        "人工确认重量"));

        assertThat(result.finalWeightKg()).isEqualTo("2.00");
        assertThat(result.finalAmountYuan()).isEqualTo("1.60");
        assertThat(result.walletDeltaYuan()).isEqualTo("1.60");
        assertThat(result.walletEffect()).isEqualTo("APPLIED");

        ArgumentCaptor<ApplyDeliveryRevisionDeltaCommand> captor =
                ArgumentCaptor.forClass(
                        ApplyDeliveryRevisionDeltaCommand.class);
        verify(funds).applyDeliveryRevisionDelta(captor.capture());
        ApplyDeliveryRevisionDeltaCommand command =
                captor.getValue();
        assertThat(command.walletOwnerRef())
                .isSameAs(walletOwnerRef);
        assertThat(command.revisionKind())
                .isEqualTo(DeliveryRevisionKind.INITIAL_REVIEW);
        assertThat(command.deltaCent()).isEqualTo(160);
        assertThat(command.currentStopThresholdCent())
                .isEqualTo(STOP_THRESHOLD_CENT);
        assertThat(command.trustedOccurredAt())
                .isEqualTo(REVIEWED_AT);
        verify(walletOwnerResolver).resolve(any());
    }

    @Test
    void mismatchedWalletOwnerIsRejectedBeforeReviewWrites() {
        when(repository.lockOrder(any(), any()))
                .thenReturn(Optional.of(pendingOrder(
                        null,
                        null,
                        "INVALID")));
        when(walletOwnerResolver.resolve(any()))
                .thenThrow(new DeliveryIdentityFactMismatchException(
                        Reason.ORGANIZATION_USER_MISMATCH,
                        "内部用户编号与公开 UUID 不匹配"));

        assertThatThrownBy(() -> service.review(
                false,
                null,
                "org-demo",
                ORDER_NO,
                OPERATION_UID,
                request(
                        0,
                        "MODIFIED_APPROVED",
                        "2.00",
                        "人工确认重量")))
                .isInstanceOf(DeliveryIdentityFactMismatchException.class);

        verify(repository, never())
                .insertRevision(any(), any(), any());
        verify(repository, never()).updateCurrentRevision(
                any(),
                any(),
                any(),
                any(),
                anyLong(),
                any());
        verify(funds, never())
                .applyDeliveryRevisionDelta(any());
        verify(audit, never()).append(any());
    }

    @Test
    void zeroDeltaCreatesRevisionWithoutCallingFunds() {
        when(repository.lockOrder(any(), any()))
                .thenReturn(Optional.of(pendingOrder(
                        new BigDecimal("0.00"),
                        0L,
                        "RELIABLE")));

        DeliveryReviewResult result = service.review(
                false,
                null,
                "org-demo",
                ORDER_NO,
                OPERATION_UID,
                request(
                        0,
                        "ORIGINAL_APPROVED",
                        null,
                        null));

        assertThat(result.walletDeltaYuan()).isEqualTo("0.00");
        assertThat(result.walletEffect()).isEqualTo("NO_CHANGE");
        verify(repository).insertRevision(any(), any(), any());
        verify(repository).updateCurrentRevision(
                any(),
                any(),
                any(),
                any(),
                anyLong(),
                any());
        verify(walletOwnerResolver, never()).resolve(any());
        verify(funds, never())
                .applyDeliveryRevisionDelta(any());
    }

    @Test
    void correctionRejectsStaleExpectedRevision() {
        when(repository.lockOrder(any(), any()))
                .thenReturn(Optional.of(approvedOrder(2)));

        assertThatThrownBy(() -> service.correct(
                false,
                null,
                "org-demo",
                ORDER_NO,
                OPERATION_UID,
                request(
                        1,
                        "MODIFIED_APPROVED",
                        "2.00",
                        null)))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        failure -> {
                            assertThat(failure.status())
                                    .isEqualTo(409);
                            assertThat(failure.code()).isEqualTo(
                                    "DELIVERY.REVISION_VERSION_CONFLICT");
                        });

        verify(repository, never())
                .insertRevision(any(), any(), any());
        verify(funds, never())
                .applyDeliveryRevisionDelta(any());
        verify(audit, never()).append(any());
    }

    @Test
    void initialReviewRejectsAlreadyApprovedOrder() {
        when(repository.lockOrder(any(), any()))
                .thenReturn(Optional.of(approvedOrder(1)));

        assertThatThrownBy(() -> service.review(
                false,
                null,
                "org-demo",
                ORDER_NO,
                OPERATION_UID,
                request(
                        1,
                        "ORIGINAL_APPROVED",
                        null,
                        null)))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        failure -> assertThat(failure.code()).isEqualTo(
                                "DELIVERY.ORDER_ALREADY_APPROVED"));
    }

    @Test
    void correctionRejectsPendingOrder() {
        when(repository.lockOrder(any(), any()))
                .thenReturn(Optional.of(pendingOrder(
                        new BigDecimal("1.00"),
                        80L,
                        "RELIABLE")));

        assertThatThrownBy(() -> service.correct(
                false,
                null,
                "org-demo",
                ORDER_NO,
                OPERATION_UID,
                request(
                        0,
                        "MODIFIED_APPROVED",
                        "1.00",
                        null)))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        failure -> assertThat(failure.code()).isEqualTo(
                                "DELIVERY.ORDER_NOT_APPROVED"));
    }

    @Test
    void missingReviewAndCorrectionCapabilitiesReturnForbidden() {
        when(authorization.authorize(any()))
                .thenReturn(
                        authorized(true, false, true),
                        authorized(true, true, false));

        assertCapabilityRequired(() -> service.review(
                false,
                null,
                "org-demo",
                ORDER_NO,
                OPERATION_UID,
                request(
                        0,
                        "ORIGINAL_APPROVED",
                        null,
                        null)));
        assertCapabilityRequired(() -> service.correct(
                false,
                null,
                "org-demo",
                ORDER_NO,
                OPERATION_UID,
                request(
                        1,
                        "MODIFIED_APPROVED",
                        "1.00",
                        null)));

        verify(persistenceRef, never()).withScopeOnce(any());
        verify(repository, never())
                .lockCurrentOpenBalanceFloor(any());
    }

    @Test
    void sameIdempotencyKeyReplaysSuccessWithoutRepeatingWrites() {
        when(repository.lockOrder(any(), any()))
                .thenReturn(Optional.of(pendingOrder(
                        new BigDecimal("0.00"),
                        0L,
                        "RELIABLE")));
        ReviewDeliveryOrderRequest request = request(
                0,
                "ORIGINAL_APPROVED",
                null,
                null);

        DeliveryReviewResult first = service.review(
                false,
                null,
                "org-demo",
                ORDER_NO,
                OPERATION_UID,
                request);
        ArgumentCaptor<AuditEntry> auditCaptor =
                ArgumentCaptor.forClass(AuditEntry.class);
        verify(audit).append(auditCaptor.capture());
        when(audit.findSuccessful(OPERATION_UID))
                .thenReturn(Optional.of(
                        successfulAudit(auditCaptor.getValue())));

        DeliveryReviewResult replayed = service.review(
                false,
                null,
                "org-demo",
                ORDER_NO,
                OPERATION_UID,
                request);

        assertThat(replayed).isEqualTo(first);
        verify(repository, times(1))
                .lockCurrentOpenBalanceFloor(any());
        verify(repository, times(1))
                .lockOrder(any(), any());
        verify(repository, times(1))
                .insertRevision(any(), any(), any());
        verify(repository, times(1)).updateCurrentRevision(
                any(),
                any(),
                any(),
                any(),
                anyLong(),
                any());
        verify(audit, times(1)).append(any());
        verify(funds, never())
                .applyDeliveryRevisionDelta(any());
    }

    @Test
    void concurrentDuplicateReplaysAfterWaitingForOrganizationLock() {
        when(repository.lockOrder(any(), any()))
                .thenReturn(Optional.of(pendingOrder(
                        new BigDecimal("0.00"),
                        0L,
                        "RELIABLE")));
        ReviewDeliveryOrderRequest request = request(
                0,
                "ORIGINAL_APPROVED",
                null,
                null);

        DeliveryReviewResult first = service.review(
                false,
                null,
                "org-demo",
                ORDER_NO,
                OPERATION_UID,
                request);
        ArgumentCaptor<AuditEntry> auditCaptor =
                ArgumentCaptor.forClass(AuditEntry.class);
        verify(audit).append(auditCaptor.capture());
        SuccessfulAudit committed =
                successfulAudit(auditCaptor.getValue());
        when(audit.findSuccessful(OPERATION_UID))
                .thenReturn(
                        Optional.empty(),
                        Optional.of(committed));

        DeliveryReviewResult replayed = service.review(
                false,
                null,
                "org-demo",
                ORDER_NO,
                OPERATION_UID,
                request);

        assertThat(replayed).isEqualTo(first);
        verify(repository, times(2))
                .lockCurrentOpenBalanceFloor(any());
        verify(repository, times(1))
                .lockOrder(any(), any());
        verify(repository, times(1))
                .insertRevision(any(), any(), any());
        verify(repository, times(1)).updateCurrentRevision(
                any(),
                any(),
                any(),
                any(),
                anyLong(),
                any());
        verify(audit, times(1)).append(any());
        verify(funds, never())
                .applyDeliveryRevisionDelta(any());
    }

    @Test
    void sameIdempotencyKeyWithDifferentFingerprintConflicts() {
        when(repository.lockOrder(any(), any()))
                .thenReturn(Optional.of(pendingOrder(
                        new BigDecimal("0.00"),
                        0L,
                        "RELIABLE")));

        service.review(
                false,
                null,
                "org-demo",
                ORDER_NO,
                OPERATION_UID,
                request(
                        0,
                        "ORIGINAL_APPROVED",
                        null,
                        null));
        ArgumentCaptor<AuditEntry> auditCaptor =
                ArgumentCaptor.forClass(AuditEntry.class);
        verify(audit).append(auditCaptor.capture());
        when(audit.findSuccessful(OPERATION_UID))
                .thenReturn(Optional.of(
                        successfulAudit(auditCaptor.getValue())));

        assertThatThrownBy(() -> service.review(
                false,
                null,
                "org-demo",
                ORDER_NO,
                OPERATION_UID,
                request(
                        0,
                        "ORIGINAL_APPROVED",
                        null,
                        "不同原因")))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        failure -> {
                            assertThat(failure.status())
                                    .isEqualTo(409);
                            assertThat(failure.code()).isEqualTo(
                                    "COMMON.IDEMPOTENCY_KEY_CONFLICT");
                        });

        verify(repository, times(1))
                .insertRevision(any(), any(), any());
        verify(repository, times(1)).updateCurrentRevision(
                any(),
                any(),
                any(),
                any(),
                anyLong(),
                any());
        verify(audit, times(1)).append(any());
    }

    private AuthorizedDeliveryScope authorized(
            boolean deliveryRead,
            boolean reviewExecute,
            boolean deliveryCorrect) {
        return new AuthorizedDeliveryScope(
                false,
                PRINCIPAL_UID,
                SESSION_UID,
                "审核员",
                "tenant-demo",
                "org-demo",
                deliveryRead,
                reviewExecute,
                deliveryCorrect,
                false,
                false,
                false,
                persistenceRef);
    }

    private static AppliedDeliveryWalletDelta applyFunds(
            ApplyDeliveryRevisionDeltaCommand command) {
        command.walletOwnerRef().withWalletEntryOwnerOnce(
                (tenantKey,
                 organizationKey,
                 organizationUserKey,
                 organizationUserUid) -> {
                    assertThat(tenantKey).isEqualTo(TENANT_ID);
                    assertThat(organizationKey)
                            .isEqualTo(ORGANIZATION_ID);
                    assertThat(organizationUserKey)
                            .isEqualTo(ORGANIZATION_USER_ID);
                    assertThat(organizationUserUid)
                            .isEqualTo(ORGANIZATION_USER_UID);
                    return null;
                });
        command.revisionRef().withWalletEntryForeignKeysOnce(keys -> {
            assertThat(keys.tenantKey()).isEqualTo(TENANT_ID);
            assertThat(keys.organizationKey())
                    .isEqualTo(ORGANIZATION_ID);
            assertThat(keys.deliveryRevisionKey())
                    .isEqualTo(REVISION_ID);
            return null;
        });
        return new AppliedDeliveryWalletDelta(
                0,
                command.deltaCent(),
                command.deltaCent(),
                new WalletEntryUid(WALLET_ENTRY_UID),
                DeliveryGateEffect.UNCHANGED,
                WithdrawalBalanceEffect.UNCHANGED);
    }

    private static ReviewDeliveryOrderRequest request(
            long expectedRevisionNo,
            String decision,
            String finalWeightKg,
            String reason) {
        return new ReviewDeliveryOrderRequest(
                expectedRevisionNo,
                decision,
                finalWeightKg,
                reason);
    }

    private static LockedDeliveryOrderRow pendingOrder(
            BigDecimal rawWeight,
            Long rawAmount,
            String rawStatus) {
        return new LockedDeliveryOrderRow(
                10L,
                ORDER_NO,
                ORGANIZATION_USER_ID,
                new BigDecimal("0.8000"),
                100_000L,
                rawWeight,
                rawAmount,
                rawStatus,
                false,
                "PENDING",
                0L,
                null,
                null,
                null,
                null);
    }

    private static LockedDeliveryOrderRow approvedOrder(
            long revisionNo) {
        return new LockedDeliveryOrderRow(
                10L,
                ORDER_NO,
                ORGANIZATION_USER_ID,
                new BigDecimal("0.8000"),
                100_000L,
                new BigDecimal("1.00"),
                80L,
                "RELIABLE",
                false,
                "APPROVED",
                revisionNo,
                900L,
                new BigDecimal("1.00"),
                80L,
                REVIEWED_AT_DATABASE.minusDays(1));
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

    private static void assertCapabilityRequired(Runnable action) {
        assertThatThrownBy(action::run)
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        failure -> {
                            assertThat(failure.status())
                                    .isEqualTo(403);
                            assertThat(failure.code()).isEqualTo(
                                    "AUTH.CAPABILITY_REQUIRED");
                        });
    }
}
