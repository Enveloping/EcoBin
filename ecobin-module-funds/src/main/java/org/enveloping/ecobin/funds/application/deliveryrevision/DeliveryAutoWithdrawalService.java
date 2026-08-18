package org.enveloping.ecobin.funds.application.deliveryrevision;

import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.funds.api.command.ApplyDeliveryRevisionDeltaCommand;
import org.enveloping.ecobin.funds.api.port.FundsOperationalControlPort;
import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskRegistrationPort;
import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskRegistrationPort.ReliableFundsTaskRegistration;
import org.enveloping.ecobin.funds.api.value.DeliveryRevisionKind;
import org.enveloping.ecobin.funds.application.authorization.MerchantTransferAuthorizationApplicationService;
import org.enveloping.ecobin.funds.application.authorization.MerchantTransferAuthorizationApplicationService.ActiveAuthorizationSnapshot;
import org.enveloping.ecobin.funds.application.recharge.RechargeApplicationService;
import org.enveloping.ecobin.identity.api.port.FundsIdentityAccessPort;
import org.enveloping.ecobin.identity.api.port.FundsIdentityAccessPort.WithdrawalTransferIdentity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.UUID;

/**
 * 在首次投递返现事务内评估并创建一次性的自动提现。
 *
 * <p>prepare 在钱包锁之前锁住用户身份、平台闸门、机构配置、商户绑定和微信授权；
 * complete 在投递入账已经写入后复用同一钱包锁，再锁机构出款账户并完成双侧冻结。</p>
 */
@Service
class DeliveryAutoWithdrawalService {

    private final JdbcTemplate jdbc;
    private final MerchantTransferAuthorizationApplicationService authorization;
    private final ReliableFundsTaskRegistrationPort tasks;
    private final AuditPort audit;
    private final FundsOperationalControlPort operationalControl;
    private final FundsIdentityAccessPort identityAccess;

    DeliveryAutoWithdrawalService(
            JdbcTemplate jdbc,
            MerchantTransferAuthorizationApplicationService authorization,
            ReliableFundsTaskRegistrationPort tasks,
            AuditPort audit,
            FundsOperationalControlPort operationalControl,
            FundsIdentityAccessPort identityAccess) {
        this.jdbc = jdbc;
        this.authorization = authorization;
        this.tasks = tasks;
        this.audit = audit;
        this.operationalControl = operationalControl;
        this.identityAccess = identityAccess;
    }

    Plan prepare(
            ApplyDeliveryRevisionDeltaCommand command,
            long tenantId,
            long organizationId,
            long organizationUserId,
            long deliveryRevisionId) {
        if (command.revisionKind() != DeliveryRevisionKind.INITIAL_REVIEW
                || command.deltaCent() <= 0) {
            return Plan.disabled();
        }
        AutoConfig observed = currentConfig(
                tenantId, organizationId, false);
        if (observed == null || !observed.enabled()) {
            return Plan.disabled();
        }

        UserIdentity observedUser = findUser(
                tenantId, organizationId, organizationUserId, false);
        boolean userUnavailable = !usableUser(observedUser);
        if (!userUnavailable) {
            userUnavailable = !identityAccess.lockWithdrawalTransferIdentity(
                    new WithdrawalTransferIdentity(
                            tenantId,
                            organizationId,
                            observedUser.miniappChannelId(),
                            observedUser.appid(),
                            organizationUserId,
                            observedUser.openid()));
        }
        UserIdentity user = userUnavailable
                ? observedUser
                : findUser(
                        tenantId,
                        organizationId,
                        organizationUserId,
                        true);
        userUnavailable = userUnavailable || !usableUser(user);

        List<Long> merchants = jdbc.query("""
                SELECT gate.merchant_profile_id
                FROM fund_payout_gate gate
                JOIN fund_wechat_merchant_profile merchant
                  ON merchant.id = gate.merchant_profile_id
                 AND merchant.status = 'ENABLED'
                ORDER BY gate.merchant_profile_id
                """, (rs, ignored) -> rs.getLong("merchant_profile_id"));
        Gate gate = merchants.size() == 1
                ? lockGate(merchants.getFirst())
                : null;
        AutoConfig config = currentConfig(
                tenantId, organizationId, true);
        if (config == null || !config.enabled()) {
            return Plan.disabled();
        }
        if (userUnavailable) {
            return Plan.skipped(
                    config,
                    deliveryRevisionId,
                    "USER_UNAVAILABLE");
        }
        if (gate == null) {
            return Plan.skipped(
                    config,
                    deliveryRevisionId,
                    "PAYOUT_CHANNEL_UNAVAILABLE");
        }
        if (!"OPEN".equals(gate.state())) {
            return Plan.skipped(
                    config,
                    deliveryRevisionId,
                    "PAYOUT_GATE_PAUSED");
        }
        if (command.deltaCent() < config.minimumCent()
                || command.deltaCent() > config.maximumCent()
                || command.deltaCent() > config.hardLimitCent()) {
            return Plan.skipped(
                    config,
                    deliveryRevisionId,
                    "AMOUNT_OUT_OF_RANGE");
        }

        Binding binding = lockBinding(
                tenantId,
                organizationId,
                user.miniappChannelId(),
                gate.merchantProfileId(),
                user.appid());
        if (binding == null) {
            return Plan.skipped(
                    config,
                    deliveryRevisionId,
                    "MERCHANT_BINDING_UNAVAILABLE");
        }

        ActiveAuthorizationSnapshot activeAuthorization;
        try {
            activeAuthorization = authorization.lockCurrentActive(
                    tenantId,
                    organizationId,
                    organizationUserId,
                    user.wechatSubjectId(),
                    binding.merchantProfileId(),
                    binding.bindingId(),
                    binding.miniappChannelId(),
                    binding.mchid(),
                    binding.appid(),
                    user.openid(),
                    binding.sceneId());
        } catch (TargetApiException unavailable) {
            if (!List.of(
                    "WITHDRAWAL.AUTO_COLLECTION_AUTHORIZATION_REQUIRED",
                    "WITHDRAWAL.AUTO_COLLECTION_AUTHORIZATION_UNRESOLVED")
                    .contains(unavailable.code())) {
                throw unavailable;
            }
            return Plan.skipped(
                    config,
                    deliveryRevisionId,
                    "AUTHORIZATION_UNAVAILABLE");
        }
        return Plan.ready(
                config,
                deliveryRevisionId,
                user,
                binding,
                activeAuthorization);
    }

    void complete(
            Plan plan,
            ApplyDeliveryRevisionDeltaCommand command,
            UUID organizationUserUid,
            DeliveryRevisionDeltaRepository.WalletRow walletBeforeCredit,
            DeliveryRevisionDeltaRepository.OrganizationCounterRow
                    counterBeforeCredit,
            boolean activeWithdrawalPresent,
            LocalDateTime occurredAt) {
        if (!plan.enabled()) {
            return;
        }
        String skip = plan.skipReason();
        if (skip == null && walletBeforeCredit.availableBalanceCent() < 0) {
            skip = "PREVIOUS_WALLET_BALANCE_NEGATIVE";
        }
        if (skip == null && activeWithdrawalPresent) {
            skip = "ACTIVE_WITHDRAWAL_EXISTS";
        }
        if (skip != null) {
            insertDecision(
                    plan,
                    command,
                    "SKIPPED",
                    skip,
                    null,
                    occurredAt);
            return;
        }

        Account account = lockAccount(
                plan.user().tenantId(),
                plan.user().organizationId());
        if (account.availableCent() < command.deltaCent()) {
            operationalControl
                    .observeAutoWithdrawalOrganizationLiquidityShortage(
                            plan.user().tenantId(),
                            plan.user().organizationId(),
                            command.deltaCent(),
                            occurredAt);
            insertDecision(
                    plan,
                    command,
                    "SKIPPED",
                    "ORGANIZATION_BALANCE_INSUFFICIENT",
                    null,
                    occurredAt);
            return;
        }

        operationalControl
                .resolveAutoWithdrawalOrganizationLiquidityShortageIfCovered(
                        plan.user().tenantId(),
                        plan.user().organizationId(),
                        account.availableCent(),
                        occurredAt);
        boolean reviewRequired = command.deltaCent()
                > plan.config().reviewFreeCent();
        String withdrawalNo = RechargeApplicationService.stableNo(
                "AW", command.revisionUid());
        String businessState = reviewRequired
                ? "PENDING_REVIEW"
                : "READY_TO_SUBMIT";
        jdbc.update("""
                INSERT INTO fund_withdrawal_order (
                    withdrawal_order_no, source_type,
                    source_delivery_order_no, review_required_at_creation,
                    tenant_id, organization_id,
                    organization_user_id, wallet_id,
                    organization_payout_account_id,
                    withdraw_config_id, withdraw_config_version_no,
                    hard_limit_cent_snapshot, effective_min_cent_snapshot,
                    effective_max_cent_snapshot,
                    effective_review_free_threshold_cent_snapshot,
                    amount_cent, miniapp_merchant_binding_id,
                    miniapp_channel_id, merchant_profile_id,
                    mchid_snapshot, appid_snapshot, openid_snapshot,
                    collection_mode_snapshot, transfer_authorization_id,
                    out_authorization_no_snapshot,
                    authorization_id_snapshot,
                    business_state, negative_balance_pause,
                    post_boundary_risk, pre_channel_block_reason,
                    channel_boundary_at, long_unsettled_at, reviewed_at,
                    channel_terminal_at, ended_at, lock_version,
                    created_at, updated_at
                ) VALUES (
                    ?, 'DELIVERY_AUTO', ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, 'AUTHORIZED', ?, ?, ?, ?, 0, 0, NULL,
                    NULL, NULL, ?, NULL, NULL, 0, ?, ?
                )
                """,
                withdrawalNo,
                command.deliveryOrderNo(),
                reviewRequired ? 1 : 0,
                plan.user().tenantId(),
                plan.user().organizationId(),
                plan.user().organizationUserId(),
                walletBeforeCredit.id(),
                account.id(),
                plan.config().id(),
                plan.config().version(),
                plan.config().hardLimitCent(),
                plan.config().minimumCent(),
                plan.config().maximumCent(),
                plan.config().reviewFreeCent(),
                command.deltaCent(),
                plan.binding().bindingId(),
                plan.binding().miniappChannelId(),
                plan.binding().merchantProfileId(),
                plan.binding().mchid(),
                plan.binding().appid(),
                plan.user().openid(),
                plan.authorization().id(),
                plan.authorization().outAuthorizationNo(),
                plan.authorization().authorizationId(),
                businessState,
                reviewRequired ? null : occurredAt,
                occurredAt,
                occurredAt);
        long withdrawalId = requiredLong(
                "SELECT id FROM fund_withdrawal_order "
                        + "WHERE withdrawal_order_no = ?",
                withdrawalNo);
        jdbc.update("""
                INSERT INTO fund_active_withdrawal (
                    wallet_id, tenant_id, organization_id,
                    withdrawal_order_id, acquired_at
                ) VALUES (?, ?, ?, ?, ?)
                """, walletBeforeCredit.id(),
                plan.user().tenantId(), plan.user().organizationId(),
                withdrawalId, occurredAt);
        freezeWallet(
                plan,
                organizationUserUid,
                walletBeforeCredit,
                counterBeforeCredit,
                withdrawalId,
                withdrawalNo,
                command.deltaCent(),
                occurredAt);
        freezeAccount(
                plan,
                account,
                withdrawalId,
                command.deltaCent(),
                occurredAt);
        insertDecision(
                plan,
                command,
                reviewRequired
                        ? "CREATED_REVIEW_REQUIRED"
                        : "CREATED_READY_TO_SUBMIT",
                null,
                withdrawalId,
                occurredAt);
        if (!reviewRequired) {
            recordSystemApproval(
                    plan,
                    withdrawalId,
                    withdrawalNo,
                    occurredAt);
        }
    }

    private void freezeWallet(
            Plan plan,
            UUID organizationUserUid,
            DeliveryRevisionDeltaRepository.WalletRow wallet,
            DeliveryRevisionDeltaRepository.OrganizationCounterRow counter,
            long withdrawalId,
            String withdrawalNo,
            long amount,
            LocalDateTime now) {
        long sequence = Math.addExact(wallet.lastEntrySequenceNo(), 2);
        long visibility = Math.addExact(
                counter.lastVisibilitySequenceNo(), 2);
        long availableAfterCredit = Math.addExact(
                wallet.availableBalanceCent(), amount);
        long frozenAfter = Math.addExact(
                wallet.frozenWithdrawalCent(), amount);
        requireOne(jdbc.update("""
                UPDATE fund_organization_wallet_entry_counter
                SET last_visibility_sequence_no = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE tenant_id = ? AND organization_id = ?
                  AND last_visibility_sequence_no = ?
                  AND lock_version = ?
                """, visibility, now,
                plan.user().tenantId(), plan.user().organizationId(),
                counter.lastVisibilitySequenceNo() + 1,
                counter.lockVersion() + 1),
                "advance auto-withdrawal wallet counter");
        requireOne(jdbc.update("""
                UPDATE fund_user_wallet
                SET available_balance_cent = ?, frozen_withdrawal_cent = ?,
                    last_entry_sequence_no = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE id = ? AND last_entry_sequence_no = ?
                  AND lock_version = ?
                """, wallet.availableBalanceCent(), frozenAfter,
                sequence, now, wallet.id(),
                wallet.lastEntrySequenceNo() + 1,
                wallet.lockVersion() + 1),
                "freeze automatic withdrawal wallet");
        jdbc.update("""
                INSERT INTO fund_user_wallet_entry (
                    entry_uid, tenant_id, organization_id, wallet_id,
                    organization_user_id, organization_user_uid,
                    entry_sequence_no, visibility_sequence_no, event_type,
                    available_delta_cent, available_before_cent,
                    available_after_cent, frozen_delta_cent,
                    frozen_before_cent, frozen_after_cent,
                    delivery_revision_id, withdrawal_order_id, adjustment_id,
                    fund_phase, source_type, source_no,
                    occurred_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'WITHDRAWAL_FREEZE',
                          ?, ?, ?, ?, ?, ?, NULL, ?, NULL, 'FREEZE',
                          'WITHDRAWAL_ORDER', ?, ?, ?)
                """, UUID.randomUUID().toString(),
                plan.user().tenantId(), plan.user().organizationId(),
                wallet.id(), plan.user().organizationUserId(),
                organizationUserUid.toString(), sequence, visibility,
                -amount, availableAfterCredit,
                wallet.availableBalanceCent(), amount,
                wallet.frozenWithdrawalCent(), frozenAfter,
                withdrawalId, withdrawalNo, now, now);
    }

    private void freezeAccount(
            Plan plan,
            Account account,
            long withdrawalId,
            long amount,
            LocalDateTime now) {
        long availableAfter = Math.subtractExact(
                account.availableCent(), amount);
        long frozenAfter = Math.addExact(account.frozenCent(), amount);
        requireOne(jdbc.update("""
                UPDATE fund_organization_payout_account
                SET available_payout_cent = ?, frozen_withdrawal_cent = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE id = ? AND lock_version = ?
                """, availableAfter, frozenAfter, now,
                account.id(), account.lockVersion()),
                "freeze automatic withdrawal payout account");
        jdbc.update("""
                INSERT INTO fund_organization_payout_entry (
                    entry_uid, tenant_id, organization_id,
                    payout_account_id, event_type,
                    available_delta_cent, available_before_cent,
                    available_after_cent, frozen_delta_cent,
                    frozen_before_cent, frozen_after_cent,
                    recharge_order_id, withdrawal_order_id, fund_phase,
                    recharge_gross_cent, recharge_fee_cent,
                    recharge_net_cent, occurred_at, created_at
                ) VALUES (?, ?, ?, ?, 'WITHDRAWAL_FREEZE',
                          ?, ?, ?, ?, ?, ?, NULL, ?, 'FREEZE',
                          NULL, NULL, NULL, ?, ?)
                """, UUID.randomUUID().toString(),
                plan.user().tenantId(), plan.user().organizationId(),
                account.id(), -amount, account.availableCent(),
                availableAfter, amount, account.frozenCent(), frozenAfter,
                withdrawalId, now, now);
    }

    private void recordSystemApproval(
            Plan plan,
            long withdrawalId,
            String withdrawalNo,
            LocalDateTime now) {
        String reason = "自动提现金额未超过机构免审阈值";
        long auditId = audit.append(new AuditEntry(
                UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID(),
                AuditScopeKind.ORGANIZATION,
                plan.user().tenantId(), plan.user().organizationId(),
                AuditActorKind.SYSTEM,
                null, null, null,
                "WITHDRAWAL_REVIEW_FREE",
                "提现免审规则",
                "withdrawal.auto_review",
                "WITHDRAWAL",
                withdrawalNo,
                "SYSTEM_TASK",
                "SUCCEEDED",
                null,
                reason,
                "{\"decision\":\"APPROVED\"}",
                now.toInstant(ZoneOffset.UTC)));
        jdbc.update("""
                INSERT INTO fund_withdrawal_review (
                    review_uid, tenant_id, organization_id,
                    withdrawal_order_id, decision, reviewer_kind,
                    platform_admin_id, staff_account_id, note,
                    succeeded_audit_id, reviewed_at, created_at
                ) VALUES (?, ?, ?, ?, 'APPROVED', 'SYSTEM',
                          NULL, NULL, ?, ?, ?, ?)
                """, UUID.randomUUID().toString(),
                plan.user().tenantId(), plan.user().organizationId(),
                withdrawalId, reason, auditId, now, now);
        String snapshot = "{\"withdrawalNo\":\""
                + withdrawalNo + "\"}";
        tasks.register(new ReliableFundsTaskRegistration(
                plan.user().tenantId(),
                plan.user().organizationId(),
                "SUBMIT_MERCHANT_TRANSFER",
                "SUBMIT_MERCHANT_TRANSFER:" + withdrawalNo,
                "WITHDRAWAL_ORDER",
                withdrawalNo,
                1,
                snapshot,
                RechargeApplicationService.sha256(snapshot),
                500,
                now));
    }

    private void insertDecision(
            Plan plan,
            ApplyDeliveryRevisionDeltaCommand command,
            String outcome,
            String skipReason,
            Long withdrawalId,
            LocalDateTime now) {
        jdbc.update("""
                INSERT INTO fund_delivery_auto_withdrawal_decision (
                    decision_uid, tenant_id, organization_id,
                    delivery_revision_id, source_delivery_order_no,
                    withdraw_config_id, withdraw_config_version_no,
                    reward_amount_cent, outcome, skip_reason,
                    withdrawal_order_id, decided_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, UUID.randomUUID().toString(),
                plan.config().tenantId(), plan.config().organizationId(),
                plan.deliveryRevisionId(), command.deliveryOrderNo(),
                plan.config().id(), plan.config().version(),
                command.deltaCent(), outcome, skipReason,
                withdrawalId, now, now);
    }

    private AutoConfig currentConfig(
            long tenantId,
            long organizationId,
            boolean lock) {
        List<ConfigHead> heads = jdbc.query("""
                SELECT current_config_id, current_version_no
                FROM fund_organization_withdraw_config_head
                WHERE tenant_id = ? AND organization_id = ?
                """ + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> new ConfigHead(
                        rs.getLong("current_config_id"),
                        rs.getLong("current_version_no")),
                tenantId, organizationId);
        if (heads.size() != 1) {
            return null;
        }
        ConfigHead head = heads.getFirst();
        return jdbc.query("""
                SELECT id, version_no, hard_limit_cent,
                       auto_withdrawal_enabled, auto_min_cent,
                       auto_max_cent, auto_review_free_threshold_cent
                FROM fund_organization_withdraw_config
                WHERE tenant_id = ? AND organization_id = ?
                  AND id = ? AND version_no = ?
                """, (rs, ignored) -> new AutoConfig(
                        tenantId,
                        organizationId,
                        rs.getLong("id"),
                        rs.getLong("version_no"),
                        rs.getLong("hard_limit_cent"),
                        rs.getBoolean("auto_withdrawal_enabled"),
                        nullableLong(rs, "auto_min_cent"),
                        nullableLong(rs, "auto_max_cent"),
                        nullableLong(
                                rs,
                                "auto_review_free_threshold_cent")),
                tenantId, organizationId,
                head.configId(), head.version()).stream().findFirst()
                .orElse(null);
    }

    private UserIdentity findUser(
            long tenantId,
            long organizationId,
            long organizationUserId,
            boolean lock) {
        return jdbc.query("""
                SELECT user.id, user.miniapp_channel_id,
                       user.wechat_subject_id, user.phone_e164,
                       user.status, subject.openid, channel.appid
                FROM iam_organization_user user
                JOIN iam_wechat_subject subject
                  ON subject.id = user.wechat_subject_id
                 AND subject.miniapp_channel_id = user.miniapp_channel_id
                JOIN iam_miniapp_channel channel
                  ON channel.id = user.miniapp_channel_id
                WHERE user.tenant_id = ? AND user.organization_id = ?
                  AND user.id = ?
                """ + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> new UserIdentity(
                        tenantId,
                        organizationId,
                        rs.getLong("id"),
                        rs.getLong("miniapp_channel_id"),
                        rs.getLong("wechat_subject_id"),
                        rs.getString("phone_e164"),
                        rs.getString("status"),
                        rs.getString("openid"),
                        rs.getString("appid")),
                tenantId, organizationId, organizationUserId)
                .stream().findFirst().orElse(null);
    }

    private static boolean usableUser(UserIdentity user) {
        return user != null
                && "ACTIVE".equals(user.status())
                && user.phoneE164() != null
                && user.openid() != null
                && !user.openid().isBlank()
                && user.appid() != null
                && !user.appid().isBlank();
    }

    private Gate lockGate(long merchantProfileId) {
        return jdbc.queryForObject("""
                SELECT merchant_profile_id, gate_state
                FROM fund_payout_gate
                WHERE merchant_profile_id = ?
                FOR UPDATE
                """, (rs, ignored) -> new Gate(
                        rs.getLong("merchant_profile_id"),
                        rs.getString("gate_state")),
                merchantProfileId);
    }

    private Binding lockBinding(
            long tenantId,
            long organizationId,
            long miniappChannelId,
            long merchantProfileId,
            String appid) {
        return jdbc.query("""
                SELECT binding.id, binding.miniapp_channel_id,
                       binding.merchant_profile_id, binding.appid,
                       merchant.mchid, merchant.scene_id
                FROM fund_miniapp_merchant_binding binding
                JOIN fund_wechat_merchant_profile merchant
                  ON merchant.id = binding.merchant_profile_id
                JOIN iam_miniapp_channel channel
                  ON channel.id = binding.miniapp_channel_id
                 AND channel.appid = binding.appid
                WHERE binding.tenant_id = ?
                  AND binding.organization_id = ?
                  AND binding.miniapp_channel_id = ?
                  AND binding.merchant_profile_id = ?
                  AND binding.appid = ?
                  AND binding.status = 'VERIFIED'
                  AND merchant.status = 'ENABLED'
                  AND channel.login_enabled = 1
                FOR UPDATE
                """, (rs, ignored) -> new Binding(
                        rs.getLong("id"),
                        rs.getLong("miniapp_channel_id"),
                        rs.getLong("merchant_profile_id"),
                        rs.getString("appid"),
                        rs.getString("mchid"),
                        rs.getString("scene_id")),
                tenantId, organizationId, miniappChannelId,
                merchantProfileId, appid).stream().findFirst().orElse(null);
    }

    private Account lockAccount(long tenantId, long organizationId) {
        return jdbc.queryForObject("""
                SELECT id, available_payout_cent,
                       frozen_withdrawal_cent, lock_version
                FROM fund_organization_payout_account
                WHERE tenant_id = ? AND organization_id = ?
                FOR UPDATE
                """, (rs, ignored) -> new Account(
                        rs.getLong("id"),
                        rs.getLong("available_payout_cent"),
                        rs.getLong("frozen_withdrawal_cent"),
                        rs.getLong("lock_version")),
                tenantId, organizationId);
    }

    private long requiredLong(String sql, Object... arguments) {
        Long value = jdbc.queryForObject(sql, Long.class, arguments);
        if (value == null) {
            throw new IllegalStateException("required key is missing");
        }
        return value;
    }

    private static Long nullableLong(
            java.sql.ResultSet resultSet,
            String column) throws java.sql.SQLException {
        long value = resultSet.getLong(column);
        return resultSet.wasNull() ? null : value;
    }

    private static void requireOne(int affected, String operation) {
        if (affected != 1) {
            throw new IllegalStateException(
                    operation + " affected " + affected + " rows");
        }
    }

    record Plan(
            boolean enabled,
            AutoConfig config,
            long deliveryRevisionId,
            UserIdentity user,
            Binding binding,
            ActiveAuthorizationSnapshot authorization,
            String skipReason) {

        static Plan disabled() {
            return new Plan(false, null, 0, null, null, null, null);
        }

        static Plan skipped(
                AutoConfig config,
                long deliveryRevisionId,
                String reason) {
            return new Plan(
                    true, config, deliveryRevisionId,
                    null, null, null, reason);
        }

        static Plan ready(
                AutoConfig config,
                long deliveryRevisionId,
                UserIdentity user,
                Binding binding,
                ActiveAuthorizationSnapshot authorization) {
            return new Plan(
                    true, config, deliveryRevisionId,
                    user, binding, authorization, null);
        }
    }

    record AutoConfig(
            long tenantId,
            long organizationId,
            long id,
            long version,
            long hardLimitCent,
            boolean enabled,
            Long minimumCent,
            Long maximumCent,
            Long reviewFreeCent) {

        AutoConfig {
            if (enabled && (minimumCent == null
                    || maximumCent == null
                    || reviewFreeCent == null)) {
                throw new IllegalStateException(
                        "enabled automatic withdrawal configuration is incomplete");
            }
            if (!enabled && (minimumCent != null
                    || maximumCent != null
                    || reviewFreeCent != null)) {
                throw new IllegalStateException(
                        "disabled automatic withdrawal configuration contains amounts");
            }
        }
    }

    private record ConfigHead(long configId, long version) {
    }

    record UserIdentity(
            long tenantId,
            long organizationId,
            long organizationUserId,
            long miniappChannelId,
            long wechatSubjectId,
            String phoneE164,
            String status,
            String openid,
            String appid) {
    }

    record Binding(
            long bindingId,
            long miniappChannelId,
            long merchantProfileId,
            String appid,
            String mchid,
            String sceneId) {
    }

    private record Gate(long merchantProfileId, String state) {
    }

    private record Account(
            long id,
            long availableCent,
            long frozenCent,
            long lockVersion) {
    }
}
