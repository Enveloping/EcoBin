package org.enveloping.ecobin.funds.application.deliveryrevision;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.funds.api.command.ApplyDeliveryRevisionDeltaCommand;
import org.enveloping.ecobin.funds.api.id.WalletEntryUid;
import org.enveloping.ecobin.funds.api.persistence.DeliveryRevisionWalletEntryRef;
import org.enveloping.ecobin.funds.api.port.ApplyDeliveryRevisionDeltaPort;
import org.enveloping.ecobin.funds.api.result.AppliedDeliveryWalletDelta;
import org.enveloping.ecobin.funds.api.result.DeliveryGateEffect;
import org.enveloping.ecobin.funds.api.result.WithdrawalBalanceEffect;
import org.enveloping.ecobin.funds.api.value.DeliveryRevisionKind;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;

@Service
public class ApplyDeliveryRevisionDeltaService
        implements ApplyDeliveryRevisionDeltaPort {

    static final long MAX_SAFE_SEQUENCE = 9_007_199_254_740_991L;

    private static final String GATE_OPEN = "OPEN";
    private static final String GATE_MANUAL_RECOVERY =
            "MANUAL_RECOVERY_REQUIRED";

    private final DeliveryRevisionDeltaRepository repository;

    ApplyDeliveryRevisionDeltaService(
            DeliveryRevisionDeltaRepository repository) {
        this.repository = repository;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public AppliedDeliveryWalletDelta applyDeliveryRevisionDelta(
            ApplyDeliveryRevisionDeltaCommand command) {
        requireWritableTransaction();
        Objects.requireNonNull(command, "command");
        if (command.deltaCent() == 0) {
            throw new IllegalArgumentException(
                    "deltaCent must not be zero");
        }
        return command.revisionRef()
                .withWalletEntryForeignKeysOnce(
                        keys -> applyLocked(command, keys));
    }

    private AppliedDeliveryWalletDelta applyLocked(
            ApplyDeliveryRevisionDeltaCommand command,
            DeliveryRevisionWalletEntryRef.WalletEntryForeignKeys keys) {
        DeliveryRevisionDeltaRepository.WalletRow wallet =
                repository.lockWallet(
                                keys.tenantKey(),
                                keys.organizationKey(),
                                keys.organizationUserKey())
                        .orElseThrow(
                                ApplyDeliveryRevisionDeltaService
                                        ::walletMissing);

        DeliveryRevisionDeltaRepository.OrganizationCounterRow counter =
                repository.lockOrganizationCounter(
                                keys.tenantKey(),
                                keys.organizationKey())
                        .orElseThrow(() -> invariant(
                                "organization wallet-entry counter "
                                        + "is missing"));

        Optional<DeliveryRevisionDeltaRepository.ActiveWithdrawalRow>
                activeWithdrawal =
                repository.lockActiveWithdrawal(
                        keys.tenantKey(),
                        keys.organizationKey(),
                        wallet.id());

        Optional<DeliveryRevisionDeltaRepository.WithdrawalOrderRow>
                withdrawal = activeWithdrawal.map(active ->
                repository.lockWithdrawalOrder(
                                keys.tenantKey(),
                                keys.organizationKey(),
                                wallet.id(),
                                active.withdrawalOrderId())
                        .orElseThrow(() -> invariant(
                                "active withdrawal points to a "
                                        + "missing withdrawal order")));

        long afterBalance = addBalance(
                wallet.availableBalanceCent(),
                command.deltaCent());
        long nextEntrySequence = nextSequence(
                wallet.lastEntrySequenceNo(),
                "wallet entry sequence");
        long nextVisibilitySequence = nextSequence(
                counter.lastVisibilitySequenceNo(),
                "organization wallet-entry visibility sequence");
        LocalDateTime occurredAt = utc(command.trustedOccurredAt());
        UUID entryUid = UUID.randomUUID();

        GatePlan gatePlan = planGate(
                wallet,
                afterBalance,
                command.currentStopThresholdCent(),
                occurredAt);
        WithdrawalPlan withdrawalPlan = planWithdrawal(
                withdrawal,
                afterBalance);

        long entryId = repository.insertWalletEntry(
                new DeliveryRevisionDeltaRepository.WalletEntryInsert(
                        entryUid,
                        keys.tenantKey(),
                        keys.organizationKey(),
                        wallet.id(),
                        keys.organizationUserKey(),
                        nextEntrySequence,
                        nextVisibilitySequence,
                        eventType(command.revisionKind()),
                        command.deltaCent(),
                        wallet.availableBalanceCent(),
                        afterBalance,
                        wallet.frozenWithdrawalCent(),
                        keys.deliveryRevisionKey(),
                        occurredAt,
                        occurredAt));

        repository.advanceOrganizationCounter(
                new DeliveryRevisionDeltaRepository
                        .OrganizationCounterUpdate(
                        keys.tenantKey(),
                        keys.organizationKey(),
                        counter.lastVisibilitySequenceNo(),
                        nextVisibilitySequence,
                        counter.lockVersion(),
                        occurredAt));

        repository.updateWallet(
                new DeliveryRevisionDeltaRepository.WalletUpdate(
                        keys.tenantKey(),
                        keys.organizationKey(),
                        keys.organizationUserKey(),
                        wallet.id(),
                        wallet.lastEntrySequenceNo(),
                        nextEntrySequence,
                        afterBalance,
                        gatePlan.state(),
                        gatePlan.thresholdSnapshotCent(),
                        gatePlan.effect()
                                == DeliveryGateEffect.LATCHED
                                ? Long.valueOf(entryId)
                                : gatePlan.triggerEntryId(),
                        gatePlan.latchedAt(),
                        wallet.lockVersion(),
                        occurredAt));

        withdrawalPlan.update().ifPresent(update ->
                repository.updateWithdrawalOrder(
                        new DeliveryRevisionDeltaRepository
                                .WithdrawalOrderUpdate(
                                keys.tenantKey(),
                                keys.organizationKey(),
                                wallet.id(),
                                update.withdrawalOrderId(),
                                update.negativeBalancePause(),
                                update.postBoundaryRisk(),
                                update.expectedLockVersion(),
                                occurredAt)));

        return new AppliedDeliveryWalletDelta(
                wallet.availableBalanceCent(),
                afterBalance,
                command.deltaCent(),
                new WalletEntryUid(entryUid),
                gatePlan.effect(),
                withdrawalPlan.effect());
    }

    private static GatePlan planGate(
            DeliveryRevisionDeltaRepository.WalletRow wallet,
            long afterBalance,
            long currentStopThresholdCent,
            LocalDateTime occurredAt) {
        return switch (wallet.deliveryGateState()) {
            case GATE_OPEN -> {
                if (wallet.deliveryGateThresholdSnapshotCent() != null
                        || wallet.deliveryGateTriggerEntryId() != null
                        || wallet.deliveryGateLatchedAt() != null) {
                    throw invariant(
                            "open delivery gate contains latch facts");
                }
                if (afterBalance <= currentStopThresholdCent) {
                    yield new GatePlan(
                            GATE_MANUAL_RECOVERY,
                            currentStopThresholdCent,
                            null,
                            occurredAt,
                            DeliveryGateEffect.LATCHED);
                }
                yield new GatePlan(
                        GATE_OPEN,
                        null,
                        null,
                        null,
                        DeliveryGateEffect.UNCHANGED);
            }
            case GATE_MANUAL_RECOVERY -> {
                Long threshold =
                        wallet.deliveryGateThresholdSnapshotCent();
                Long trigger =
                        wallet.deliveryGateTriggerEntryId();
                LocalDateTime latchedAt =
                        wallet.deliveryGateLatchedAt();
                if (threshold == null
                        || threshold >= 0
                        || trigger == null
                        || latchedAt == null) {
                    throw invariant(
                            "manual-recovery delivery gate "
                                    + "is missing latch facts");
                }
                yield new GatePlan(
                        GATE_MANUAL_RECOVERY,
                        threshold,
                        trigger,
                        latchedAt,
                        DeliveryGateEffect.RETAINED);
            }
            default -> throw invariant(
                    "unknown delivery gate state");
        };
    }

    private static WithdrawalPlan planWithdrawal(
            Optional<DeliveryRevisionDeltaRepository.WithdrawalOrderRow>
                    withdrawal,
            long afterBalance) {
        if (withdrawal.isEmpty()) {
            return new WithdrawalPlan(
                    Optional.empty(),
                    WithdrawalBalanceEffect.UNCHANGED);
        }
        DeliveryRevisionDeltaRepository.WithdrawalOrderRow row =
                withdrawal.orElseThrow();
        if (afterBalance < 0) {
            if (row.channelBoundaryAt() == null) {
                if (row.negativeBalancePause()) {
                    return unchangedWithdrawal();
                }
                return changedWithdrawal(
                        row,
                        true,
                        row.postBoundaryRisk(),
                        WithdrawalBalanceEffect
                                .PAUSED_BEFORE_CHANNEL);
            }
            if (row.postBoundaryRisk()) {
                return unchangedWithdrawal();
            }
            return changedWithdrawal(
                    row,
                    row.negativeBalancePause(),
                    true,
                    WithdrawalBalanceEffect
                            .RISK_MARKED_AFTER_CHANNEL);
        }
        if (row.channelBoundaryAt() == null
                && row.negativeBalancePause()) {
            return changedWithdrawal(
                    row,
                    false,
                    row.postBoundaryRisk(),
                    WithdrawalBalanceEffect.PAUSE_CLEARED);
        }
        return unchangedWithdrawal();
    }

    private static WithdrawalPlan unchangedWithdrawal() {
        return new WithdrawalPlan(
                Optional.empty(),
                WithdrawalBalanceEffect.UNCHANGED);
    }

    private static WithdrawalPlan changedWithdrawal(
            DeliveryRevisionDeltaRepository.WithdrawalOrderRow row,
            boolean negativeBalancePause,
            boolean postBoundaryRisk,
            WithdrawalBalanceEffect effect) {
        return new WithdrawalPlan(
                Optional.of(new WithdrawalUpdate(
                        row.id(),
                        negativeBalancePause,
                        postBoundaryRisk,
                        row.lockVersion())),
                effect);
    }

    private static long addBalance(long before, long delta) {
        try {
            return Math.addExact(before, delta);
        } catch (ArithmeticException exception) {
            throw new TargetApiException(
                    422,
                    "WALLET.BALANCE_OUT_OF_RANGE",
                    "投递修订会使钱包余额超出可表示范围");
        }
    }

    private static long nextSequence(
            long current,
            String name) {
        final long next;
        try {
            next = Math.addExact(current, 1L);
        } catch (ArithmeticException exception) {
            throw invariant(name + " overflowed");
        }
        if (next < 1 || next > MAX_SAFE_SEQUENCE) {
            throw invariant(name + " exhausted");
        }
        return next;
    }

    private static String eventType(DeliveryRevisionKind kind) {
        return switch (kind) {
            case INITIAL_REVIEW -> "DELIVERY_INITIAL_REVIEW";
            case CORRECTION -> "DELIVERY_CORRECTION";
        };
    }

    private static LocalDateTime utc(Instant instant) {
        return LocalDateTime.ofInstant(
                instant.truncatedTo(ChronoUnit.MILLIS),
                ZoneOffset.UTC);
    }

    private static void requireWritableTransaction() {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()
                || TransactionSynchronizationManager
                .isCurrentTransactionReadOnly()) {
            throw new IllegalStateException(
                    "delivery revision wallet delta requires "
                            + "an existing writable transaction");
        }
    }

    private static TargetApiException walletMissing() {
        return new TargetApiException(
                500,
                "WALLET.NOT_INITIALIZED",
                "机构用户钱包未初始化，无法应用投递修订");
    }

    private static IllegalStateException invariant(String message) {
        return new IllegalStateException(
                "funds invariant violated: " + message);
    }

    private record GatePlan(
            String state,
            Long thresholdSnapshotCent,
            Long triggerEntryId,
            LocalDateTime latchedAt,
            DeliveryGateEffect effect) {
    }

    private record WithdrawalUpdate(
            long withdrawalOrderId,
            boolean negativeBalancePause,
            boolean postBoundaryRisk,
            long expectedLockVersion) {
    }

    private record WithdrawalPlan(
            Optional<WithdrawalUpdate> update,
            WithdrawalBalanceEffect effect) {
    }
}
