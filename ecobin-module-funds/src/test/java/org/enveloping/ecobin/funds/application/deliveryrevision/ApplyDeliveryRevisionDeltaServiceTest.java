package org.enveloping.ecobin.funds.application.deliveryrevision;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.funds.api.command.ApplyDeliveryRevisionDeltaCommand;
import org.enveloping.ecobin.funds.api.persistence.DeliveryRevisionWalletEntryRef;
import org.enveloping.ecobin.funds.api.result.AppliedDeliveryWalletDelta;
import org.enveloping.ecobin.funds.api.result.DeliveryGateEffect;
import org.enveloping.ecobin.funds.api.result.WithdrawalBalanceEffect;
import org.enveloping.ecobin.funds.api.value.DeliveryRevisionKind;
import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.time.Instant;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import java.util.function.Function;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ApplyDeliveryRevisionDeltaServiceTest {

    private static final Instant OCCURRED_AT =
            Instant.parse("2026-07-29T08:40:15.123456Z");
    private static final LocalDateTime OCCURRED_AT_UTC =
            LocalDateTime.parse("2026-07-29T08:40:15.123");

    @BeforeEach
    void beginWritableTransaction() {
        TransactionSynchronizationManager
                .setActualTransactionActive(true);
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(false);
    }

    @AfterEach
    void endTransaction() {
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(false);
        TransactionSynchronizationManager
                .setActualTransactionActive(false);
    }

    @Test
    void locksInCanonicalOrderThenLatchesGateAndPausesWithdrawal() {
        FakeRepository repository = new FakeRepository();
        repository.wallet = Optional.of(wallet(
                -20,
                30,
                3,
                "OPEN",
                null,
                null,
                null));
        repository.counter = Optional.of(
                new DeliveryRevisionDeltaRepository
                        .OrganizationCounterRow(10, 7));
        repository.activeWithdrawal = Optional.of(
                new DeliveryRevisionDeltaRepository
                        .ActiveWithdrawalRow(99));
        repository.withdrawal = Optional.of(
                new DeliveryRevisionDeltaRepository
                        .WithdrawalOrderRow(
                        99,
                        "READY_TO_SUBMIT",
                        false,
                        false,
                        null,
                        null,
                        11));

        AppliedDeliveryWalletDelta result =
                new ApplyDeliveryRevisionDeltaService(repository)
                        .applyDeliveryRevisionDelta(command(
                                DeliveryRevisionKind.CORRECTION,
                                -80,
                                -100));

        assertEquals(
                List.of(
                        "lock-wallet",
                        "lock-counter",
                        "lock-active-withdrawal",
                        "lock-withdrawal-order",
                        "insert-wallet-entry",
                        "advance-counter",
                        "update-wallet",
                        "update-withdrawal-order"),
                repository.calls);
        assertEquals(-20, result.beforeBalanceCent());
        assertEquals(-100, result.afterBalanceCent());
        assertEquals(-80, result.deltaCent());
        assertNotNull(result.entryUid());
        assertEquals(DeliveryGateEffect.LATCHED, result.gateEffect());
        assertEquals(
                WithdrawalBalanceEffect.PAUSED_BEFORE_CHANNEL,
                result.withdrawalEffect());

        DeliveryRevisionDeltaRepository.WalletEntryInsert entry =
                repository.insert;
        assertEquals("DELIVERY_CORRECTION", entry.eventType());
        assertEquals(4, entry.entrySequenceNo());
        assertEquals(11, entry.visibilitySequenceNo());
        assertEquals(-20, entry.availableBeforeCent());
        assertEquals(-100, entry.availableAfterCent());
        assertEquals(30, entry.frozenBeforeCent());
        assertEquals(444, entry.deliveryRevisionId());
        assertEquals(OCCURRED_AT_UTC, entry.occurredAt());

        DeliveryRevisionDeltaRepository.WalletUpdate walletUpdate =
                repository.walletUpdate;
        assertEquals(
                "MANUAL_RECOVERY_REQUIRED",
                walletUpdate.deliveryGateState());
        assertEquals(
                -100,
                walletUpdate.deliveryGateThresholdSnapshotCent());
        assertEquals(
                repository.generatedEntryId,
                walletUpdate.deliveryGateTriggerEntryId());
        assertEquals(
                OCCURRED_AT_UTC,
                walletUpdate.deliveryGateLatchedAt());

        assertTrue(
                repository.withdrawalUpdate
                        .negativeBalancePause());
        assertFalse(
                repository.withdrawalUpdate
                        .postBoundaryRisk());
    }

    @Test
    void positiveInitialReviewLeavesOpenGateAndSkipsWithdrawalWrite() {
        FakeRepository repository = new FakeRepository();
        repository.wallet = Optional.of(wallet(
                100,
                0,
                8,
                "OPEN",
                null,
                null,
                null));
        repository.counter = Optional.of(
                new DeliveryRevisionDeltaRepository
                        .OrganizationCounterRow(15, 2));

        AppliedDeliveryWalletDelta result =
                new ApplyDeliveryRevisionDeltaService(repository)
                        .applyDeliveryRevisionDelta(command(
                                DeliveryRevisionKind.INITIAL_REVIEW,
                                50,
                                -100));

        assertEquals(150, result.afterBalanceCent());
        assertEquals(
                DeliveryGateEffect.UNCHANGED,
                result.gateEffect());
        assertEquals(
                WithdrawalBalanceEffect.UNCHANGED,
                result.withdrawalEffect());
        assertEquals(
                "DELIVERY_INITIAL_REVIEW",
                repository.insert.eventType());
        assertEquals("OPEN", repository.walletUpdate.deliveryGateState());
        assertEquals(
                null,
                repository.walletUpdate
                        .deliveryGateThresholdSnapshotCent());
        assertFalse(
                repository.calls.contains(
                        "lock-withdrawal-order"));
        assertFalse(
                repository.calls.contains(
                        "update-withdrawal-order"));
    }

    @Test
    void positiveDeltaRetainsManualGateButClearsPreChannelPause() {
        LocalDateTime originalLatch =
                LocalDateTime.parse("2026-07-28T07:00:00.123");
        FakeRepository repository = new FakeRepository();
        repository.wallet = Optional.of(wallet(
                -10,
                25,
                2,
                "MANUAL_RECOVERY_REQUIRED",
                -100L,
                77L,
                originalLatch));
        repository.counter = Optional.of(
                new DeliveryRevisionDeltaRepository
                        .OrganizationCounterRow(5, 6));
        repository.activeWithdrawal = Optional.of(
                new DeliveryRevisionDeltaRepository
                        .ActiveWithdrawalRow(99));
        repository.withdrawal = Optional.of(
                new DeliveryRevisionDeltaRepository
                        .WithdrawalOrderRow(
                        99,
                        "READY_TO_SUBMIT",
                        true,
                        false,
                        "OTHER_BLOCKER",
                        null,
                        4));

        AppliedDeliveryWalletDelta result =
                new ApplyDeliveryRevisionDeltaService(repository)
                        .applyDeliveryRevisionDelta(command(
                                DeliveryRevisionKind.CORRECTION,
                                20,
                                -200));

        assertEquals(10, result.afterBalanceCent());
        assertEquals(
                DeliveryGateEffect.RETAINED,
                result.gateEffect());
        assertEquals(
                WithdrawalBalanceEffect.PAUSE_CLEARED,
                result.withdrawalEffect());
        assertEquals(
                "MANUAL_RECOVERY_REQUIRED",
                repository.walletUpdate.deliveryGateState());
        assertEquals(
                -100,
                repository.walletUpdate
                        .deliveryGateThresholdSnapshotCent());
        assertEquals(
                77,
                repository.walletUpdate
                        .deliveryGateTriggerEntryId());
        assertEquals(
                originalLatch,
                repository.walletUpdate.deliveryGateLatchedAt());
        assertFalse(
                repository.withdrawalUpdate
                        .negativeBalancePause());
    }

    @Test
    void negativeBalanceAfterChannelOnlyMarksRisk() {
        FakeRepository repository = new FakeRepository();
        repository.wallet = Optional.of(wallet(
                10,
                40,
                1,
                "OPEN",
                null,
                null,
                null));
        repository.counter = Optional.of(
                new DeliveryRevisionDeltaRepository
                        .OrganizationCounterRow(1, 1));
        repository.activeWithdrawal = Optional.of(
                new DeliveryRevisionDeltaRepository
                        .ActiveWithdrawalRow(99));
        repository.withdrawal = Optional.of(
                new DeliveryRevisionDeltaRepository
                        .WithdrawalOrderRow(
                        99,
                        "CHANNEL_PROCESSING",
                        false,
                        false,
                        null,
                        LocalDateTime.parse(
                                "2026-07-29T08:00:00.000"),
                        3));

        AppliedDeliveryWalletDelta result =
                new ApplyDeliveryRevisionDeltaService(repository)
                        .applyDeliveryRevisionDelta(command(
                                DeliveryRevisionKind.CORRECTION,
                                -20,
                                -100));

        assertEquals(-10, result.afterBalanceCent());
        assertEquals(
                DeliveryGateEffect.UNCHANGED,
                result.gateEffect());
        assertEquals(
                WithdrawalBalanceEffect
                        .RISK_MARKED_AFTER_CHANNEL,
                result.withdrawalEffect());
        assertFalse(
                repository.withdrawalUpdate
                        .negativeBalancePause());
        assertTrue(
                repository.withdrawalUpdate
                        .postBoundaryRisk());
    }

    @Test
    void balanceUsesExactArithmeticAndDoesNotWriteOnOverflow() {
        FakeRepository repository = new FakeRepository();
        repository.wallet = Optional.of(wallet(
                Long.MAX_VALUE,
                0,
                1,
                "OPEN",
                null,
                null,
                null));
        repository.counter = Optional.of(
                new DeliveryRevisionDeltaRepository
                        .OrganizationCounterRow(1, 1));

        TargetApiException failure = assertThrows(
                TargetApiException.class,
                () -> new ApplyDeliveryRevisionDeltaService(repository)
                        .applyDeliveryRevisionDelta(command(
                                DeliveryRevisionKind.CORRECTION,
                                1,
                                -100)));

        assertEquals(
                "WALLET.BALANCE_OUT_OF_RANGE",
                failure.code());
        assertEquals(
                List.of(
                        "lock-wallet",
                        "lock-counter",
                        "lock-active-withdrawal"),
                repository.calls);
    }

    @Test
    void refusesToAdvancePastJavascriptSafeSequence() {
        FakeRepository repository = new FakeRepository();
        repository.wallet = Optional.of(wallet(
                0,
                0,
                ApplyDeliveryRevisionDeltaService
                        .MAX_SAFE_SEQUENCE,
                "OPEN",
                null,
                null,
                null));
        repository.counter = Optional.of(
                new DeliveryRevisionDeltaRepository
                        .OrganizationCounterRow(1, 1));

        IllegalStateException failure = assertThrows(
                IllegalStateException.class,
                () -> new ApplyDeliveryRevisionDeltaService(repository)
                        .applyDeliveryRevisionDelta(command(
                                DeliveryRevisionKind.CORRECTION,
                                1,
                                -100)));

        assertTrue(failure.getMessage().contains("exhausted"));
        assertFalse(
                repository.calls.contains(
                        "insert-wallet-entry"));
    }

    @Test
    void organizationVisibilitySequenceHasTheSameSafeLimit() {
        FakeRepository repository = new FakeRepository();
        repository.wallet = Optional.of(wallet(
                0,
                0,
                1,
                "OPEN",
                null,
                null,
                null));
        repository.counter = Optional.of(
                new DeliveryRevisionDeltaRepository
                        .OrganizationCounterRow(
                        ApplyDeliveryRevisionDeltaService
                                .MAX_SAFE_SEQUENCE,
                        1));

        IllegalStateException failure = assertThrows(
                IllegalStateException.class,
                () -> new ApplyDeliveryRevisionDeltaService(repository)
                        .applyDeliveryRevisionDelta(command(
                                DeliveryRevisionKind.CORRECTION,
                                1,
                                -100)));

        assertTrue(failure.getMessage().contains("exhausted"));
        assertFalse(
                repository.calls.contains(
                        "insert-wallet-entry"));
    }

    @Test
    void portRequiresAnExistingWritableOuterTransaction()
            throws NoSuchMethodException {
        Transactional annotation =
                ApplyDeliveryRevisionDeltaService.class
                        .getMethod(
                                "applyDeliveryRevisionDelta",
                                ApplyDeliveryRevisionDeltaCommand.class)
                        .getAnnotation(Transactional.class);

        assertEquals(
                Propagation.MANDATORY,
                annotation.propagation());

        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(true);
        IllegalStateException failure = assertThrows(
                IllegalStateException.class,
                () -> new ApplyDeliveryRevisionDeltaService(
                        new FakeRepository())
                        .applyDeliveryRevisionDelta(command(
                                DeliveryRevisionKind.CORRECTION,
                                1,
                                -100)));
        assertTrue(failure.getMessage().contains("writable"));
    }

    private static ApplyDeliveryRevisionDeltaCommand command(
            DeliveryRevisionKind kind,
            long deltaCent,
            long thresholdCent) {
        return new ApplyDeliveryRevisionDeltaCommand(
                new OrganizationUserUid(UUID.randomUUID()),
                UUID.randomUUID(),
                new TestRevisionRef(
                        new DeliveryRevisionWalletEntryRef
                                .WalletEntryForeignKeys(
                                11,
                                22,
                                33,
                                444)),
                kind,
                deltaCent,
                thresholdCent,
                OCCURRED_AT);
    }

    private static DeliveryRevisionDeltaRepository.WalletRow wallet(
            long availableBalanceCent,
            long frozenWithdrawalCent,
            long lastEntrySequenceNo,
            String gateState,
            Long thresholdSnapshotCent,
            Long triggerEntryId,
            LocalDateTime latchedAt) {
        return new DeliveryRevisionDeltaRepository.WalletRow(
                44,
                availableBalanceCent,
                frozenWithdrawalCent,
                lastEntrySequenceNo,
                gateState,
                thresholdSnapshotCent,
                triggerEntryId,
                latchedAt,
                5);
    }

    private static final class TestRevisionRef
            implements DeliveryRevisionWalletEntryRef {

        private final WalletEntryForeignKeys keys;
        private boolean consumed;

        private TestRevisionRef(WalletEntryForeignKeys keys) {
            this.keys = keys;
        }

        @Override
        public synchronized <T> T withWalletEntryForeignKeysOnce(
                Function<WalletEntryForeignKeys, T> function) {
            if (consumed) {
                throw new IllegalStateException(
                        "test revision ref already consumed");
            }
            consumed = true;
            return function.apply(keys);
        }

        @Override
        public String toString() {
            return "DeliveryRevisionWalletEntryRef[REDACTED]";
        }
    }

    private static final class FakeRepository
            implements DeliveryRevisionDeltaRepository {

        private final List<String> calls = new ArrayList<>();
        private Optional<WalletRow> wallet = Optional.of(wallet(
                0,
                0,
                0,
                "OPEN",
                null,
                null,
                null));
        private Optional<OrganizationCounterRow> counter =
                Optional.of(new OrganizationCounterRow(0, 0));
        private Optional<ActiveWithdrawalRow> activeWithdrawal =
                Optional.empty();
        private Optional<WithdrawalOrderRow> withdrawal =
                Optional.empty();
        private long generatedEntryId = 700;
        private WalletEntryInsert insert;
        private OrganizationCounterUpdate counterUpdate;
        private WalletUpdate walletUpdate;
        private WithdrawalOrderUpdate withdrawalUpdate;

        @Override
        public Optional<WalletRow> lockWallet(
                long tenantId,
                long organizationId,
                long organizationUserId) {
            calls.add("lock-wallet");
            assertEquals(11, tenantId);
            assertEquals(22, organizationId);
            assertEquals(33, organizationUserId);
            return wallet;
        }

        @Override
        public Optional<OrganizationCounterRow>
        lockOrganizationCounter(
                long tenantId,
                long organizationId) {
            calls.add("lock-counter");
            return counter;
        }

        @Override
        public Optional<ActiveWithdrawalRow> lockActiveWithdrawal(
                long tenantId,
                long organizationId,
                long walletId) {
            calls.add("lock-active-withdrawal");
            assertEquals(44, walletId);
            return activeWithdrawal;
        }

        @Override
        public Optional<WithdrawalOrderRow> lockWithdrawalOrder(
                long tenantId,
                long organizationId,
                long walletId,
                long withdrawalOrderId) {
            calls.add("lock-withdrawal-order");
            assertEquals(99, withdrawalOrderId);
            return withdrawal;
        }

        @Override
        public long insertWalletEntry(WalletEntryInsert value) {
            calls.add("insert-wallet-entry");
            insert = value;
            return generatedEntryId;
        }

        @Override
        public void advanceOrganizationCounter(
                OrganizationCounterUpdate update) {
            calls.add("advance-counter");
            counterUpdate = update;
        }

        @Override
        public void updateWallet(WalletUpdate update) {
            calls.add("update-wallet");
            walletUpdate = update;
        }

        @Override
        public void updateWithdrawalOrder(
                WithdrawalOrderUpdate update) {
            calls.add("update-withdrawal-order");
            withdrawalUpdate = update;
        }
    }
}
