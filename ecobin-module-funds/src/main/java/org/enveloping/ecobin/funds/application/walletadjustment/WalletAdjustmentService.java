package org.enveloping.ecobin.funds.application.walletadjustment;

import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.funds.api.command.AdjustWalletCommand;
import org.enveloping.ecobin.funds.api.port.WalletAdjustmentPort;
import org.enveloping.ecobin.funds.api.result.WalletAdjustmentResult;
import org.enveloping.ecobin.funds.api.result.WalletAdjustmentWithdrawalEffect;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.PreparedStatementCreator;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.sql.PreparedStatement;
import java.sql.Statement;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;

@Service
public class WalletAdjustmentService implements WalletAdjustmentPort {

    private static final long MAX_SAFE_SEQUENCE = 9_007_199_254_740_991L;
    private static final String ACTION = "wallet.adjust";
    private static final String TARGET_TYPE = "USER_WALLET";
    private static final String GATE_OPEN = "OPEN";
    private static final String GATE_MANUAL_RECOVERY =
            "MANUAL_RECOVERY_REQUIRED";

    private final JdbcTemplate jdbc;
    private final AuditPort audit;
    private final ObjectMapper objectMapper;

    public WalletAdjustmentService(
            JdbcTemplate jdbc,
            AuditPort audit,
            ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.audit = audit;
        this.objectMapper = objectMapper;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public WalletAdjustmentResult adjust(AdjustWalletCommand command) {
        requireWritableTransaction();
        Objects.requireNonNull(command, "command");
        if (command.operationUid().version() != 4) {
            throw validation("Idempotency-Key 必须是 UUIDv4");
        }
        return command.targetRef().withTargetOnce(
                (tenantId,
                 organizationId,
                 organizationUserId,
                 organizationUserUid,
                 platformAdminId,
                 staffAccountId) -> adjustInTarget(
                        command,
                        new Target(
                                tenantId,
                                organizationId,
                                organizationUserId,
                                organizationUserUid,
                                platformAdminId,
                                staffAccountId)));
    }

    private WalletAdjustmentResult adjustInTarget(
            AdjustWalletCommand command,
            Target target) {
        validateActor(command, target);
        byte[] requestDigest = requestDigest(command, target);
        String requestHex = HexFormat.of().formatHex(requestDigest);
        WalletRow wallet = lockWallet(target);
        Optional<SuccessfulAudit> prior = audit.findSuccessful(
                command.operationUid());
        if (prior.isPresent()) {
            return replay(
                    prior.orElseThrow(), command, target, wallet, requestHex);
        }
        if (wallet.lockVersion() != command.expectedWalletVersion()) {
            throw new TargetApiException(
                    409,
                    "WALLET.VERSION_CONFLICT",
                    "钱包余额版本已经变化，请载入最新余额后重新确认");
        }
        CounterRow counter = lockCounter(target);
        Optional<WithdrawalRow> withdrawal = lockActiveWithdrawal(
                target, wallet.id());

        long after;
        try {
            after = Math.addExact(
                    wallet.availableBalanceCent(), command.deltaCent());
        } catch (ArithmeticException exception) {
            throw outOfRange();
        }
        long nextEntrySequence = next(
                wallet.lastEntrySequenceNo(), "wallet entry sequence");
        long nextVisibilitySequence = next(
                counter.lastVisibilitySequenceNo(),
                "organization wallet visibility sequence");
        LocalDateTime occurredAt = LocalDateTime.ofInstant(
                command.occurredAt().truncatedTo(ChronoUnit.MILLIS),
                ZoneOffset.UTC);
        UUID entryUid = UUID.randomUUID();

        GatePlan gate = gatePlan(
                wallet,
                command.deltaCent(),
                after,
                command.currentStopThresholdCent(),
                occurredAt);
        WithdrawalPlan withdrawalPlan = withdrawalPlan(withdrawal, after);
        WalletAdjustmentResult response = new WalletAdjustmentResult(
                command.operationUid(),
                entryUid,
                command.deltaCent(),
                wallet.availableBalanceCent(),
                after,
                next(wallet.lockVersion(), "wallet lock version"),
                gate.state(),
                withdrawalPlan.effect(),
                command.occurredAt().truncatedTo(ChronoUnit.MILLIS));

        long auditId = appendAudit(
                command, target, wallet, requestHex, response);
        long adjustmentId = insertAdjustment(
                command, target, wallet, after,
                requestDigest, auditId, occurredAt);
        long entryId = insertWalletEntry(
                target, wallet, adjustmentId, entryUid,
                nextEntrySequence, nextVisibilitySequence,
                command.deltaCent(), after, occurredAt,
                command.operationUid().toString());

        advanceCounter(
                target, counter, nextVisibilitySequence, occurredAt);
        updateWallet(
                target, wallet, nextEntrySequence, after, gate,
                entryId, occurredAt);
        withdrawalPlan.update().ifPresent(update -> {
            updateWithdrawal(target, wallet.id(), update, occurredAt);
            if (withdrawalPlan.effect()
                    == WalletAdjustmentWithdrawalEffect
                    .RESUMED_BEFORE_CHANNEL) {
                wakeSubmitTask(update.withdrawalNo(), occurredAt);
            }
        });
        return response;
    }

    private WalletRow lockWallet(Target target) {
        return jdbc.query("""
                        SELECT id, wallet_uid, available_balance_cent,
                               frozen_withdrawal_cent,
                               last_entry_sequence_no,
                               delivery_gate_state,
                               delivery_gate_threshold_snapshot_cent,
                               delivery_gate_trigger_entry_id,
                               delivery_gate_latched_at,
                               lock_version
                        FROM fund_user_wallet
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND organization_user_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new WalletRow(
                        rs.getLong("id"),
                        UUID.fromString(rs.getString("wallet_uid")),
                        rs.getLong("available_balance_cent"),
                        rs.getLong("frozen_withdrawal_cent"),
                        rs.getLong("last_entry_sequence_no"),
                        rs.getString("delivery_gate_state"),
                        nullableLong(
                                rs.getObject(
                                        "delivery_gate_threshold_snapshot_cent")),
                        nullableLong(
                                rs.getObject(
                                        "delivery_gate_trigger_entry_id")),
                        rs.getObject(
                                "delivery_gate_latched_at",
                                LocalDateTime.class),
                        rs.getLong("lock_version")),
                target.tenantId(),
                target.organizationId(),
                target.organizationUserId())
                .stream()
                .findFirst()
                .orElseThrow(() -> new TargetApiException(
                        500,
                        "WALLET.NOT_INITIALIZED",
                        "机构用户钱包未初始化，无法调整余额"));
    }

    private CounterRow lockCounter(Target target) {
        return jdbc.query("""
                        SELECT last_visibility_sequence_no, lock_version
                        FROM fund_organization_wallet_entry_counter
                        WHERE tenant_id = ? AND organization_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new CounterRow(
                        rs.getLong("last_visibility_sequence_no"),
                        rs.getLong("lock_version")),
                target.tenantId(), target.organizationId())
                .stream()
                .findFirst()
                .orElseThrow(() -> invariant(
                        "organization wallet-entry counter is missing"));
    }

    private Optional<WithdrawalRow> lockActiveWithdrawal(
            Target target,
            long walletId) {
        List<Long> active = jdbc.query("""
                        SELECT withdrawal_order_id
                        FROM fund_active_withdrawal
                        WHERE tenant_id = ? AND organization_id = ?
                          AND wallet_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("withdrawal_order_id"),
                target.tenantId(), target.organizationId(), walletId);
        if (active.isEmpty()) return Optional.empty();
        if (active.size() != 1) {
            throw invariant("multiple active withdrawals for one wallet");
        }
        return jdbc.query("""
                        SELECT id, withdrawal_order_no,
                               negative_balance_pause,
                               post_boundary_risk, channel_boundary_at,
                               lock_version
                        FROM fund_withdrawal_order
                        WHERE tenant_id = ? AND organization_id = ?
                          AND wallet_id = ? AND id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new WithdrawalRow(
                        rs.getLong("id"),
                        rs.getString("withdrawal_order_no"),
                        rs.getBoolean("negative_balance_pause"),
                        rs.getBoolean("post_boundary_risk"),
                        rs.getObject("channel_boundary_at",
                                LocalDateTime.class),
                        rs.getLong("lock_version")),
                target.tenantId(), target.organizationId(), walletId,
                active.getFirst())
                .stream()
                .findFirst()
                .or(() -> {
                    throw invariant(
                            "active withdrawal points to a missing order");
                });
    }

    private long appendAudit(
            AdjustWalletCommand command,
            Target target,
            WalletRow wallet,
            String requestHex,
            WalletAdjustmentResult response) {
        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("fingerprint", requestHex);
        summary.put("response", response);
        summary.put("reasonPresent", command.reason() != null);
        try {
            return audit.append(new AuditEntry(
                    UUID.randomUUID(),
                    UUID.randomUUID(),
                    command.operationUid(),
                    AuditScopeKind.ORGANIZATION,
                    target.tenantId(),
                    target.organizationId(),
                    command.platformActor()
                            ? AuditActorKind.PLATFORM_ADMIN
                            : AuditActorKind.STAFF_ACCOUNT,
                    target.platformAdminId(),
                    target.staffAccountId(),
                    null,
                    null,
                    command.actorDisplayName(),
                    ACTION,
                    TARGET_TYPE,
                    wallet.walletUid().toString(),
                    "WEB",
                    "SUCCEEDED",
                    command.sessionUid(),
                    command.reason(),
                    writeJson(summary),
                    response.occurredAt()));
        } catch (DuplicateKeyException duplicate) {
            throw idempotencyConflict();
        }
    }

    private long insertAdjustment(
            AdjustWalletCommand command,
            Target target,
            WalletRow wallet,
            long after,
            byte[] requestDigest,
            long auditId,
            LocalDateTime occurredAt) {
        return insertReturningId(connection -> {
            PreparedStatement statement = connection.prepareStatement("""
                    INSERT INTO fund_wallet_adjustment (
                        adjustment_uid, tenant_id, organization_id, wallet_id,
                        expected_wallet_version, request_sha256,
                        amount_delta_cent, available_before_cent,
                        available_after_cent, actor_kind,
                        platform_admin_id, staff_account_id, reason,
                        succeeded_audit_id, occurred_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, Statement.RETURN_GENERATED_KEYS);
            int index = 1;
            statement.setString(index++, command.operationUid().toString());
            statement.setLong(index++, target.tenantId());
            statement.setLong(index++, target.organizationId());
            statement.setLong(index++, wallet.id());
            statement.setLong(index++, command.expectedWalletVersion());
            statement.setBytes(index++, requestDigest);
            statement.setLong(index++, command.deltaCent());
            statement.setLong(index++, wallet.availableBalanceCent());
            statement.setLong(index++, after);
            statement.setString(index++, command.platformActor()
                    ? "PLATFORM_ADMIN" : "STAFF_ACCOUNT");
            statement.setObject(index++, target.platformAdminId());
            statement.setObject(index++, target.staffAccountId());
            statement.setString(index++, command.reason());
            statement.setLong(index++, auditId);
            statement.setObject(index++, occurredAt);
            statement.setObject(index, occurredAt);
            return statement;
        }, "insert wallet adjustment");
    }

    private long insertWalletEntry(
            Target target,
            WalletRow wallet,
            long adjustmentId,
            UUID entryUid,
            long entrySequence,
            long visibilitySequence,
            long delta,
            long after,
            LocalDateTime occurredAt,
            String adjustmentUid) {
        return insertReturningId(connection -> {
            PreparedStatement statement = connection.prepareStatement("""
                    INSERT INTO fund_user_wallet_entry (
                        entry_uid, tenant_id, organization_id,
                        wallet_id, organization_user_id,
                        organization_user_uid,
                        entry_sequence_no, visibility_sequence_no,
                        event_type, available_delta_cent,
                        available_before_cent, available_after_cent,
                        frozen_delta_cent, frozen_before_cent,
                        frozen_after_cent, delivery_revision_id,
                        withdrawal_order_id, adjustment_id, fund_phase,
                        source_type, source_no, occurred_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'MANUAL_ADJUSTMENT',
                              ?, ?, ?, 0, ?, ?, NULL, NULL, ?, NULL,
                              'MANUAL_ADJUSTMENT', ?, ?, ?)
                    """, Statement.RETURN_GENERATED_KEYS);
            int index = 1;
            statement.setString(index++, entryUid.toString());
            statement.setLong(index++, target.tenantId());
            statement.setLong(index++, target.organizationId());
            statement.setLong(index++, wallet.id());
            statement.setLong(index++, target.organizationUserId());
            statement.setString(
                    index++, target.organizationUserUid().toString());
            statement.setLong(index++, entrySequence);
            statement.setLong(index++, visibilitySequence);
            statement.setLong(index++, delta);
            statement.setLong(index++, wallet.availableBalanceCent());
            statement.setLong(index++, after);
            statement.setLong(index++, wallet.frozenWithdrawalCent());
            statement.setLong(index++, wallet.frozenWithdrawalCent());
            statement.setLong(index++, adjustmentId);
            statement.setString(index++, adjustmentUid);
            statement.setObject(index++, occurredAt);
            statement.setObject(index, occurredAt);
            return statement;
        }, "insert manual wallet entry");
    }

    private void advanceCounter(
            Target target,
            CounterRow counter,
            long nextVisibility,
            LocalDateTime now) {
        requireSingle(jdbc.update("""
                        UPDATE fund_organization_wallet_entry_counter
                        SET last_visibility_sequence_no = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ? AND organization_id = ?
                          AND last_visibility_sequence_no = ?
                          AND lock_version = ?
                        """,
                nextVisibility, now,
                target.tenantId(), target.organizationId(),
                counter.lastVisibilitySequenceNo(), counter.lockVersion()),
                "advance wallet entry counter");
    }

    private void updateWallet(
            Target target,
            WalletRow wallet,
            long nextEntrySequence,
            long after,
            GatePlan gate,
            long entryId,
            LocalDateTime now) {
        Long triggerEntryId = gate.triggerEntryId();
        if (gate.latchWithCurrentEntry()) {
            triggerEntryId = entryId;
        }
        requireSingle(jdbc.update("""
                        UPDATE fund_user_wallet
                        SET available_balance_cent = ?,
                            last_entry_sequence_no = ?,
                            delivery_gate_state = ?,
                            delivery_gate_threshold_snapshot_cent = ?,
                            delivery_gate_trigger_entry_id = ?,
                            delivery_gate_latched_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ? AND organization_id = ?
                          AND organization_user_id = ? AND id = ?
                          AND last_entry_sequence_no = ?
                          AND lock_version = ?
                        """,
                after, nextEntrySequence, gate.state(),
                gate.thresholdSnapshotCent(), triggerEntryId,
                gate.latchedAt(), now,
                target.tenantId(), target.organizationId(),
                target.organizationUserId(), wallet.id(),
                wallet.lastEntrySequenceNo(), wallet.lockVersion()),
                "update wallet projection");
    }

    private void updateWithdrawal(
            Target target,
            long walletId,
            WithdrawalUpdate update,
            LocalDateTime now) {
        requireSingle(jdbc.update("""
                        UPDATE fund_withdrawal_order
                        SET negative_balance_pause = ?,
                            post_boundary_risk = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE tenant_id = ? AND organization_id = ?
                          AND wallet_id = ? AND id = ?
                          AND lock_version = ?
                        """,
                update.negativeBalancePause() ? 1 : 0,
                update.postBoundaryRisk() ? 1 : 0,
                now, target.tenantId(), target.organizationId(),
                walletId, update.withdrawalOrderId(),
                update.expectedVersion()),
                "update active withdrawal balance flags");
    }

    private void wakeSubmitTask(
            String withdrawalNo,
            LocalDateTime now) {
        jdbc.update("""
                UPDATE ops_reliable_task
                SET next_run_at = ?,
                    wake_version = wake_version + 1,
                    lock_version = lock_version + 1,
                    updated_at = ?
                WHERE execution_lane = 'FUNDS'
                  AND task_type = 'SUBMIT_MERCHANT_TRANSFER'
                  AND target_type = 'WITHDRAWAL_ORDER'
                  AND target_stable_key = ?
                  AND state = 'PENDING'
                  AND dispatch_wait_reason IS NULL
                """, now, now, withdrawalNo);
    }

    private WalletAdjustmentResult replay(
            SuccessfulAudit previous,
            AdjustWalletCommand command,
            Target target,
            WalletRow wallet,
            String requestHex) {
        JsonNode summary = readJson(previous.safeChangeSummaryJson());
        boolean sameActor = command.platformActor()
                ? previous.actorKind() == AuditActorKind.PLATFORM_ADMIN
                && Objects.equals(previous.platformAdminId(),
                target.platformAdminId())
                : previous.actorKind() == AuditActorKind.STAFF_ACCOUNT
                && Objects.equals(previous.staffAccountId(),
                target.staffAccountId());
        if (!sameActor
                || previous.scopeKind() != AuditScopeKind.ORGANIZATION
                || !Objects.equals(previous.tenantId(), target.tenantId())
                || !Objects.equals(
                previous.organizationId(), target.organizationId())
                || !ACTION.equals(previous.actionCode())
                || !TARGET_TYPE.equals(previous.targetType())
                || !wallet.walletUid().toString().equals(
                previous.targetStableKey())
                || !requestHex.equals(
                summary.path("fingerprint").asText())) {
            throw idempotencyConflict();
        }
        try {
            WalletAdjustmentResult result = objectMapper.treeToValue(
                    summary.path("response"), WalletAdjustmentResult.class);
            if (result == null
                    || !command.operationUid().equals(
                    result.adjustmentUid())) {
                throw idempotencyConflict();
            }
            return result;
        } catch (TargetApiException exception) {
            throw exception;
        } catch (Exception exception) {
            throw idempotencyConflict();
        }
    }

    private byte[] requestDigest(
            AdjustWalletCommand command,
            Target target) {
        Map<String, Object> canonical = new LinkedHashMap<>();
        canonical.put("principalUid", command.actorUid());
        canonical.put("action", ACTION);
        canonical.put("organizationUserUid", target.organizationUserUid());
        canonical.put("deltaCent", command.deltaCent());
        canonical.put("expectedWalletVersion",
                command.expectedWalletVersion());
        canonical.put("reason", command.reason());
        try {
            return MessageDigest.getInstance("SHA-256")
                    .digest(objectMapper.writeValueAsBytes(canonical));
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "wallet adjustment fingerprint cannot be encoded",
                    exception);
        }
    }

    private static GatePlan gatePlan(
            WalletRow wallet,
            long delta,
            long after,
            long currentThreshold,
            LocalDateTime occurredAt) {
        return switch (wallet.deliveryGateState()) {
            case GATE_OPEN -> {
                requireOpenGateFacts(wallet);
                if (after <= currentThreshold) {
                    yield new GatePlan(
                            GATE_MANUAL_RECOVERY,
                            currentThreshold,
                            null,
                            occurredAt,
                            true);
                }
                yield new GatePlan(GATE_OPEN, null, null, null, false);
            }
            case GATE_MANUAL_RECOVERY -> {
                requireLatchedGateFacts(wallet);
                if (delta > 0
                        && after
                        > wallet.deliveryGateThresholdSnapshotCent()) {
                    yield new GatePlan(
                            GATE_OPEN, null, null, null, false);
                }
                yield new GatePlan(
                        GATE_MANUAL_RECOVERY,
                        wallet.deliveryGateThresholdSnapshotCent(),
                        wallet.deliveryGateTriggerEntryId(),
                        wallet.deliveryGateLatchedAt(),
                        false);
            }
            default -> throw invariant("unknown delivery gate state");
        };
    }

    private static WithdrawalPlan withdrawalPlan(
            Optional<WithdrawalRow> current,
            long after) {
        if (current.isEmpty()) {
            return new WithdrawalPlan(Optional.empty(),
                    WalletAdjustmentWithdrawalEffect.NONE);
        }
        WithdrawalRow row = current.orElseThrow();
        if (after < 0) {
            if (row.channelBoundaryAt() == null) {
                if (row.negativeBalancePause()) return unchangedWithdrawal();
                return changedWithdrawal(
                        row, true, row.postBoundaryRisk(),
                        WalletAdjustmentWithdrawalEffect
                                .PAUSED_BEFORE_CHANNEL);
            }
            if (row.postBoundaryRisk()) return unchangedWithdrawal();
            return changedWithdrawal(
                    row, row.negativeBalancePause(), true,
                    WalletAdjustmentWithdrawalEffect
                            .RISK_MARKED_AFTER_CHANNEL);
        }
        if (row.channelBoundaryAt() == null
                && row.negativeBalancePause()) {
            return changedWithdrawal(
                    row, false, row.postBoundaryRisk(),
                    WalletAdjustmentWithdrawalEffect
                            .RESUMED_BEFORE_CHANNEL);
        }
        return unchangedWithdrawal();
    }

    private static WithdrawalPlan unchangedWithdrawal() {
        return new WithdrawalPlan(
                Optional.empty(), WalletAdjustmentWithdrawalEffect.NONE);
    }

    private static WithdrawalPlan changedWithdrawal(
            WithdrawalRow row,
            boolean negativePause,
            boolean postBoundaryRisk,
            WalletAdjustmentWithdrawalEffect effect) {
        return new WithdrawalPlan(
                Optional.of(new WithdrawalUpdate(
                        row.id(), row.withdrawalNo(),
                        negativePause, postBoundaryRisk,
                        row.lockVersion())),
                effect);
    }

    private static void requireOpenGateFacts(WalletRow wallet) {
        if (wallet.deliveryGateThresholdSnapshotCent() != null
                || wallet.deliveryGateTriggerEntryId() != null
                || wallet.deliveryGateLatchedAt() != null) {
            throw invariant("open delivery gate contains latch facts");
        }
    }

    private static void requireLatchedGateFacts(WalletRow wallet) {
        if (wallet.deliveryGateThresholdSnapshotCent() == null
                || wallet.deliveryGateThresholdSnapshotCent() >= 0
                || wallet.deliveryGateTriggerEntryId() == null
                || wallet.deliveryGateLatchedAt() == null) {
            throw invariant("manual-recovery gate lacks latch facts");
        }
    }

    private void validateActor(
            AdjustWalletCommand command,
            Target target) {
        if (command.platformActor()
                != (target.platformAdminId() != null)
                || command.platformActor()
                == (target.staffAccountId() != null)) {
            throw invariant("wallet adjustment actor reference mismatch");
        }
    }

    private long insertReturningId(
            PreparedStatementCreator creator,
            String operation) {
        KeyHolder holder = new GeneratedKeyHolder();
        int affected = jdbc.update(creator, holder);
        Number key = holder.getKey();
        if (affected != 1 || key == null || key.longValue() <= 0) {
            throw invariant(operation + " affected " + affected + " rows");
        }
        return key.longValue();
    }

    private static void requireSingle(int affected, String operation) {
        if (affected != 1) {
            throw invariant(operation + " affected " + affected + " rows");
        }
    }

    private static long next(long current, String name) {
        final long value;
        try {
            value = Math.addExact(current, 1L);
        } catch (ArithmeticException exception) {
            throw invariant(name + " overflowed");
        }
        if (value < 1 || value > MAX_SAFE_SEQUENCE) {
            throw invariant(name + " exhausted");
        }
        return value;
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "wallet adjustment audit cannot be encoded", exception);
        }
    }

    private JsonNode readJson(String value) {
        try {
            return objectMapper.readTree(value);
        } catch (Exception exception) {
            throw idempotencyConflict();
        }
    }

    private static Long nullableLong(Object value) {
        return value == null ? null : ((Number) value).longValue();
    }

    private static void requireWritableTransaction() {
        if (!TransactionSynchronizationManager.isActualTransactionActive()
                || TransactionSynchronizationManager
                .isCurrentTransactionReadOnly()) {
            throw new IllegalStateException(
                    "wallet adjustment requires an existing writable transaction");
        }
    }

    private static TargetApiException validation(String message) {
        return new TargetApiException(
                400, "COMMON.VALIDATION_FAILED", message);
    }

    private static TargetApiException outOfRange() {
        return new TargetApiException(
                422,
                "WALLET.ADJUSTMENT_OUT_OF_STORAGE_RANGE",
                "本次调整会使钱包余额超出可存储范围");
    }

    private static TargetApiException idempotencyConflict() {
        return new TargetApiException(
                409,
                "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                "该 Idempotency-Key 已用于不同的钱包调整请求");
    }

    private static IllegalStateException invariant(String message) {
        return new IllegalStateException(
                "funds invariant violated: " + message);
    }

    private record Target(
            long tenantId,
            long organizationId,
            long organizationUserId,
            UUID organizationUserUid,
            Long platformAdminId,
            Long staffAccountId) {
    }

    private record WalletRow(
            long id,
            UUID walletUid,
            long availableBalanceCent,
            long frozenWithdrawalCent,
            long lastEntrySequenceNo,
            String deliveryGateState,
            Long deliveryGateThresholdSnapshotCent,
            Long deliveryGateTriggerEntryId,
            LocalDateTime deliveryGateLatchedAt,
            long lockVersion) {
    }

    private record CounterRow(
            long lastVisibilitySequenceNo,
            long lockVersion) {
    }

    private record WithdrawalRow(
            long id,
            String withdrawalNo,
            boolean negativeBalancePause,
            boolean postBoundaryRisk,
            LocalDateTime channelBoundaryAt,
            long lockVersion) {
    }

    private record GatePlan(
            String state,
            Long thresholdSnapshotCent,
            Long triggerEntryId,
            LocalDateTime latchedAt,
            boolean latchWithCurrentEntry) {
    }

    private record WithdrawalUpdate(
            long withdrawalOrderId,
            String withdrawalNo,
            boolean negativeBalancePause,
            boolean postBoundaryRisk,
            long expectedVersion) {
    }

    private record WithdrawalPlan(
            Optional<WithdrawalUpdate> update,
            WalletAdjustmentWithdrawalEffect effect) {
    }
}
