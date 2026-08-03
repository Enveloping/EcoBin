package org.enveloping.ecobin.funds.application.withdrawal;

import tools.jackson.databind.JsonNode;
import org.enveloping.ecobin.framework.audit.*;
import org.enveloping.ecobin.framework.web.TargetWebAuditRequestContext;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.funds.api.port.MerchantTransferChannelPort;
import org.enveloping.ecobin.funds.api.port.MerchantTransferChannelPort.MerchantTransferRequest;
import org.enveloping.ecobin.funds.api.port.MerchantTransferChannelPort.MerchantTransferResult;
import org.enveloping.ecobin.funds.api.port.FundsOperationalControlPort;
import org.enveloping.ecobin.funds.api.port.FundsOperationalControlPort.ReconciliationIssue;
import org.enveloping.ecobin.funds.api.port.ReliableFundsAttemptBoundaryPort;
import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskExecutorPort;
import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskRegistrationPort;
import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskRegistrationPort.ReliableFundsTaskRegistration;
import org.enveloping.ecobin.funds.application.access.FundsAccessService;
import org.enveloping.ecobin.funds.application.access.FundsAccessService.MiniappScope;
import org.enveloping.ecobin.funds.application.access.FundsAccessService.PlatformScope;
import org.enveloping.ecobin.funds.application.access.FundsAccessService.WebScope;
import org.enveloping.ecobin.funds.application.channel.WechatChannelEvidencePolicy;
import org.enveloping.ecobin.funds.application.channel.WechatChannelEvidencePolicy.Validation;
import org.enveloping.ecobin.funds.application.recharge.RechargeApplicationService;
import org.enveloping.ecobin.funds.web.v1.FundsModels.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.Duration;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.Locale;
import java.util.UUID;

@Service
public class WithdrawalApplicationService {

    private final JdbcTemplate jdbc;
    private final FundsAccessService access;
    private final ReliableFundsTaskRegistrationPort tasks;
    private final ReliableFundsAttemptBoundaryPort attemptBoundary;
    private final MerchantTransferChannelPort channel;
    private final TransactionTemplate transactions;
    private final AuditPort audit;
    private final FundsOperationalControlPort operationalControl;
    private final String notifyBaseUrl;

    public WithdrawalApplicationService(
            JdbcTemplate jdbc,
            FundsAccessService access,
            ReliableFundsTaskRegistrationPort tasks,
            ReliableFundsAttemptBoundaryPort attemptBoundary,
            MerchantTransferChannelPort channel,
            TransactionTemplate transactions,
            AuditPort audit,
            FundsOperationalControlPort operationalControl,
            @Value("${ecobin.funds.wechat-pay.notify-base-url:https://fake.invalid}")
            String notifyBaseUrl) {
        this.jdbc = jdbc;
        this.access = access;
        this.tasks = tasks;
        this.attemptBoundary = attemptBoundary;
        this.channel = channel;
        this.transactions = transactions;
        this.audit = audit;
        this.operationalControl = operationalControl;
        this.notifyBaseUrl = stripTrailingSlash(notifyBaseUrl);
    }

    @Transactional(readOnly = true)
    public WithdrawalConfigurationView configuration(
            boolean platformPath,
            String tenantCode,
            String organizationCode) {
        WebScope scope = authorizedEither(
                platformPath, tenantCode, organizationCode,
                "withdrawal.read", "withdrawal.configuration.manage", false);
        return currentConfig(scope.tenantId(), scope.organizationId(), false).view();
    }

    @Transactional(readOnly = true)
    public PayoutGateView payoutGate() {
        access.platformScope();
        return payoutGateView(currentPlatformGate(false));
    }

    @Transactional
    public PayoutGateView restorePayoutGate(
            UUID operationUid,
            RestorePayoutGateRequest request) {
        requireUuidV4(operationUid);
        PlatformScope actor = access.platformScope();
        if (request == null || request.expectedGateVersion() == null
                || request.pausedEventUid() == null) {
            throw validation(
                    "expectedGateVersion 和 pausedEventUid 不能为空");
        }
        String normalizedReason = trimTo(request.reason(), 500);
        byte[] requestHash = payoutRestoreRequestHash(
                request, normalizedReason);
        PayoutGateRestoreReplay replay = findPayoutGateRestore(operationUid);
        if (replay != null) {
            return replayPayoutGateRestore(replay, requestHash);
        }
        if (!Boolean.TRUE.equals(request.fundsReplenishedConfirmed())) {
            throw new TargetApiException(
                    422,
                    "FUNDS.PAYOUT_GATE_RESTORE_NOT_CONFIRMED",
                    "必须明确确认公司运营账户已经补足资金");
        }
        GateRow gate = currentPlatformGate(true);
        replay = findPayoutGateRestore(operationUid);
        if (replay != null) {
            return replayPayoutGateRestore(replay, requestHash);
        }
        if (gate.version() != request.expectedGateVersion()) {
            throw new TargetApiException(
                    409,
                    "FUNDS.PAYOUT_GATE_VERSION_CONFLICT",
                    "出款闸门版本已变化，请刷新后重试");
        }
        if (!"PAUSED_NOT_ENOUGH".equals(gate.state())
                || !request.pausedEventUid().equals(
                gate.currentPauseEventUid())) {
            throw new TargetApiException(
                    409,
                    "FUNDS.PAYOUT_GATE_TARGET_CHANGED",
                    "当前暂停事件已经变化，不能恢复旧目标");
        }
        LocalDateTime now = databaseNow();
        jdbc.update("""
                INSERT INTO fund_payout_gate_event (
                    event_uid, merchant_profile_id, event_type,
                    triggering_transfer_observation_id,
                    triggering_transfer_id, original_pause_event_id,
                    original_pause_event_type,
                    restored_by_platform_admin_id, restore_request_sha256,
                    gate_version_before, gate_version_after, note,
                    occurred_at, created_at
                ) VALUES (?, ?, 'RESTORED', NULL, NULL, ?, 'PAUSED', ?,
                          ?, ?, ?, ?, ?, ?)
                """, operationUid.toString(), gate.merchantId(),
                gate.currentPauseEventId(), actor.platformAdminId(),
                requestHash, gate.version(), gate.version() + 1,
                normalizedReason, now, now);
        int restored = jdbc.update("""
                UPDATE fund_payout_gate
                SET gate_state = 'OPEN', current_pause_event_id = NULL,
                    paused_at = NULL, lock_version = lock_version + 1,
                    updated_at = ?
                WHERE merchant_profile_id = ?
                  AND gate_state = 'PAUSED_NOT_ENOUGH'
                  AND current_pause_event_id = ? AND lock_version = ?
                """, now, gate.merchantId(), gate.currentPauseEventId(),
                gate.version());
        if (restored != 1) {
            throw new TargetApiException(
                    409,
                    "FUNDS.PAYOUT_GATE_TARGET_CHANGED",
                    "出款闸门已被其他操作修改");
        }
        operationalControl.resolvePayoutLiquidityPause(
                gate.merchantId(), gate.currentPauseEventUid(), now);
        int wokenTasks = operationalControl.wakePayoutTasks(
                gate.merchantId(), gate.currentPauseEventUid(), now);
        appendPlatformAudit(
                actor, operationUid, "payout-gate.restore", gate.mchid(),
                request.reason(),
                "{\"pausedEventUid\":\"" + gate.currentPauseEventUid()
                        + "\",\"restoredEventUid\":\"" + operationUid
                        + "\",\"wokenTasks\":" + wokenTasks + "}", now);
        TargetWebAuditRequestContext.describe(
                "payout-gate.restore", gate.mchid());
        return payoutGateView(currentPlatformGate(false));
    }

    @Transactional
    public WithdrawalConfigurationView releaseConfiguration(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            UUID operationUid,
            ReleaseWithdrawalConfigurationRequest request) {
        requireUuidV4(operationUid);
        WebScope scope = access.webScope(
                platformPath, tenantCode, organizationCode,
                "withdrawal.configuration.manage", true);
        if (request == null || request.expectedCurrentVersion() == null) {
            throw validation("expectedCurrentVersion 不能为空");
        }
        long hard = RechargeApplicationService.parseCent(
                request.hardLimitYuan(), "hardLimitYuan");
        long minimum = RechargeApplicationService.parseCent(
                request.manualMinimumYuan(), "manualMinimumYuan");
        long maximum = RechargeApplicationService.parseCent(
                request.manualMaximumYuan(), "manualMaximumYuan");
        if (minimum < 10 || minimum > maximum
                || maximum > hard || hard > 20_000) {
            throw validation(
                    "提现配置必须满足 0.10 <= 最低额 <= 最高额 <= 硬上限 <= 200.00");
        }
        ConfigRow current = currentConfig(
                scope.tenantId(), scope.organizationId(), true);
        if (current.version() != request.expectedCurrentVersion()) {
            throw new TargetApiException(
                    409, "WITHDRAWAL.CONFIG_VERSION_CONFLICT",
                    "提现配置已被其他操作更新");
        }
        long version = Math.addExact(current.version(), 1);
        String content = "hard=" + hard + ";min=" + minimum
                + ";max=" + maximum + ";reviewFree=0";
        LocalDateTime now = databaseNow();
        jdbc.update("""
                INSERT INTO fund_organization_withdraw_config (
                    tenant_id, organization_id, version_no, content_sha256,
                    hard_limit_cent, manual_min_cent, manual_max_cent,
                    manual_review_free_threshold_cent, publication_source,
                    published_by_staff_account_id, published_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'STAFF', ?, ?, ?)
                """, scope.tenantId(), scope.organizationId(), version,
                RechargeApplicationService.sha256(content), hard, minimum,
                maximum, scope.staffAccountId(), now, now);
        long configId = requiredLong("""
                SELECT id FROM fund_organization_withdraw_config
                WHERE tenant_id = ? AND organization_id = ? AND version_no = ?
                """, scope.tenantId(), scope.organizationId(), version);
        jdbc.update("""
                UPDATE fund_organization_withdraw_config_head
                SET current_config_id = ?, current_version_no = ?,
                    lock_version = lock_version + 1,
                    switched_at = ?, updated_at = ?
                WHERE tenant_id = ? AND organization_id = ?
                  AND current_version_no = ?
                """, configId, version, now, now, scope.tenantId(),
                scope.organizationId(), current.version());
        appendWebAudit(
                scope, operationUid,
                "withdrawal.configuration.release",
                scope.organizationCode(), null,
                "{\"versionNo\":" + version + "}", now);
        return new WithdrawalConfigurationView(
                version, money(hard), money(minimum), money(maximum),
                "0.00", instant(now));
    }

    @Transactional
    public WithdrawalView create(
            UUID operationUid,
            CreateWithdrawalRequest request) {
        requireUuidV4(operationUid);
        MiniappScope scope = access.miniappScope(true);
        long amount = RechargeApplicationService.parseCent(
                request == null ? null : request.amountYuan(), "amountYuan");
        String withdrawalNo = RechargeApplicationService.stableNo(
                "WD", operationUid);
        WithdrawalRow replay = findWithdrawal(
                scope.tenantId(), scope.organizationId(), withdrawalNo, false);
        if (replay != null) {
            if (replay.userId() != scope.organizationUserId()
                    || replay.amountCent() != amount) {
                throw idempotencyConflict();
            }
            return view(replay);
        }

        UserRow user = requiredUser(scope, true);
        if (!"ACTIVE".equals(user.status()) || user.phoneE164() == null) {
            throw new TargetApiException(
                    422, "WITHDRAWAL.USER_UNAVAILABLE",
                    "当前机构用户状态不允许提现");
        }
        GateRow gate = currentPlatformGate(true);
        if (!"OPEN".equals(gate.state())) {
            throw new TargetApiException(
                    422, "WITHDRAWAL.PAYOUT_GATE_PAUSED",
                    "平台出款暂时停止，请稍后再试");
        }
        ConfigRow config = currentConfig(
                scope.tenantId(), scope.organizationId(), true);
        BindingRow binding = requiredBinding(
                scope.tenantId(), scope.organizationId(), true);
        if (binding.merchantId() != gate.merchantId()) {
            throw stateConflict("商户绑定在提现创建期间发生变化");
        }
        if (amount < config.minimumCent()
                || amount > config.maximumCent()
                || amount > config.hardLimitCent()) {
            throw new TargetApiException(
                    422, "WITHDRAWAL.AMOUNT_OUT_OF_RANGE",
                    "提现金额不符合当前机构配置");
        }
        WalletRow wallet = requiredWallet(
                scope.tenantId(), scope.organizationId(),
                scope.organizationUserId(), true);
        if (wallet.availableCent() < 0 || wallet.availableCent() < amount) {
            throw new TargetApiException(
                    422, "WITHDRAWAL.USER_BALANCE_INSUFFICIENT",
                    "用户钱包可提现余额不足");
        }
        if (activeWithdrawalExists(wallet.id(), true)) {
            throw new TargetApiException(
                    409, "WITHDRAWAL.ACTIVE_WITHDRAWAL_EXISTS",
                    "当前钱包已有一笔处理中提现");
        }
        CounterRow counter = lockCounter(
                scope.tenantId(), scope.organizationId());
        AccountRow account = requiredAccount(
                scope.tenantId(), scope.organizationId(), true);
        if (account.availableCent() < amount) {
            throw new TargetApiException(
                    422, "WITHDRAWAL.ORGANIZATION_BALANCE_INSUFFICIENT",
                    "机构可用出款额度不足");
        }

        LocalDateTime now = databaseNow();
        jdbc.update("""
                INSERT INTO fund_withdrawal_order (
                    withdrawal_order_no, tenant_id, organization_id,
                    organization_user_id, wallet_id,
                    organization_payout_account_id,
                    withdraw_config_id, withdraw_config_version_no,
                    hard_limit_cent_snapshot, manual_min_cent_snapshot,
                    manual_max_cent_snapshot,
                    manual_review_free_threshold_cent_snapshot,
                    amount_cent, miniapp_merchant_binding_id,
                    organization_miniapp_id, merchant_profile_id,
                    mchid_snapshot, appid_snapshot, openid_snapshot,
                    business_state, negative_balance_pause,
                    post_boundary_risk, pre_channel_block_reason,
                    channel_boundary_at, long_unsettled_at, reviewed_at,
                    channel_terminal_at, ended_at, lock_version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?,
                          ?, ?, ?, 'PENDING_REVIEW', 0, 0, NULL,
                          NULL, NULL, NULL, NULL, NULL, 0, ?, ?)
                """, withdrawalNo, scope.tenantId(), scope.organizationId(),
                scope.organizationUserId(), wallet.id(), account.id(),
                config.id(), config.version(), config.hardLimitCent(),
                config.minimumCent(), config.maximumCent(), amount,
                binding.bindingId(), binding.miniappId(), binding.merchantId(),
                binding.mchid(), binding.appid(), user.openid(), now, now);
        long withdrawalId = requiredLong(
                "SELECT id FROM fund_withdrawal_order WHERE withdrawal_order_no = ?",
                withdrawalNo);
        jdbc.update("""
                INSERT INTO fund_active_withdrawal (
                    wallet_id, tenant_id, organization_id,
                    withdrawal_order_id, acquired_at
                ) VALUES (?, ?, ?, ?, ?)
                """, wallet.id(), scope.tenantId(), scope.organizationId(),
                withdrawalId, now);
        freezeWallet(
                scope, wallet, counter, withdrawalId, withdrawalNo,
                amount, now);
        freezeAccount(scope, account, withdrawalId, amount, now);
        appendMiniappAudit(
                scope, operationUid, "withdrawal.create", withdrawalNo,
                "{\"amountYuan\":\"" + money(amount) + "\"}", now);
        return view(requiredWithdrawal(
                scope.tenantId(), scope.organizationId(), withdrawalNo, false));
    }

    @Transactional(readOnly = true)
    public WithdrawalPage miniappList(String status, Integer limit) {
        MiniappScope scope = access.miniappScope(false);
        return list(scope.tenantId(), scope.organizationId(),
                scope.organizationUserId(), normalizeStatus(status), limit);
    }

    @Transactional(readOnly = true)
    public WithdrawalView miniappDetail(String withdrawalNo) {
        MiniappScope scope = access.miniappScope(false);
        WithdrawalRow row = requiredWithdrawal(
                scope.tenantId(), scope.organizationId(), withdrawalNo, false);
        if (row.userId() != scope.organizationUserId()) throw notFound();
        return view(row);
    }

    @Transactional(readOnly = true)
    public WithdrawalPage webList(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String status,
            Integer limit) {
        WebScope scope = access.webScope(
                platformPath, tenantCode, organizationCode,
                "withdrawal.read", false);
        return list(scope.tenantId(), scope.organizationId(),
                null, normalizeStatus(status), limit);
    }

    @Transactional(readOnly = true)
    public WithdrawalView webDetail(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String withdrawalNo) {
        WebScope scope = access.webScope(
                platformPath, tenantCode, organizationCode,
                "withdrawal.read", false);
        return view(requiredWithdrawal(
                scope.tenantId(), scope.organizationId(), withdrawalNo, false));
    }

    @Transactional
    public WithdrawalView review(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String withdrawalNo,
            UUID operationUid,
            ReviewWithdrawalRequest request) {
        requireUuidV4(operationUid);
        WebScope scope = access.webScope(
                platformPath, tenantCode, organizationCode,
                "review.execute", false);
        String decision = normalizeDecision(request == null ? null : request.decision());
        WithdrawalRow initial = requiredWithdrawal(
                scope.tenantId(), scope.organizationId(), withdrawalNo, false);
        requireExpectedVersion(initial,
                new VersionedWithdrawalRequest(
                        request == null ? null : request.expectedVersion()));
        WalletRow wallet = requiredWalletById(initial, true);
        CounterRow counter = lockCounter(initial.tenantId(), initial.organizationId());
        lockActive(initial.walletId());
        WithdrawalRow row = requiredWithdrawal(
                initial.tenantId(), initial.organizationId(), withdrawalNo, true);
        if (!"PENDING_REVIEW".equals(row.state())) {
            throw stateConflict("该提现已不在待审核状态");
        }
        if ("APPROVED".equals(decision) && row.negativePause()) {
            throw stateConflict("用户钱包负余额暂停期间不能审核通过");
        }
        AccountRow account = requiredAccountById(row, true);
        LocalDateTime now = databaseNow();
        if ("APPROVED".equals(decision)) {
            jdbc.update("""
                    UPDATE fund_withdrawal_order
                    SET business_state = 'READY_TO_SUBMIT', reviewed_at = ?,
                        lock_version = lock_version + 1, updated_at = ?
                    WHERE id = ? AND business_state = 'PENDING_REVIEW'
                    """, now, now, row.id());
        } else {
            release(row, wallet, counter, account,
                    "REJECTED", true, now);
        }
        long auditId = appendWebAudit(
                scope, operationUid, "withdrawal.review", withdrawalNo,
                request == null ? null : request.note(),
                "{\"decision\":\"" + decision + "\"}", now);
        jdbc.update("""
                INSERT INTO fund_withdrawal_review (
                    review_uid, tenant_id, organization_id,
                    withdrawal_order_id, decision, reviewer_kind,
                    platform_admin_id, staff_account_id, note,
                    succeeded_audit_id, reviewed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, UUID.randomUUID().toString(), row.tenantId(),
                row.organizationId(), row.id(), decision,
                scope.platformActor() ? "PLATFORM_ADMIN" : "STAFF_ACCOUNT",
                scope.platformAdminId(), scope.staffAccountId(),
                trimTo(request == null ? null : request.note(), 500),
                auditId, now, now);
        if ("APPROVED".equals(decision)) {
            registerTask(row, "SUBMIT_MERCHANT_TRANSFER",
                    "SUBMIT_MERCHANT_TRANSFER:" + withdrawalNo, now);
        }
        TargetWebAuditRequestContext.describe("withdrawal.review", withdrawalNo);
        return view(requiredWithdrawal(
                row.tenantId(), row.organizationId(), withdrawalNo, false));
    }

    @Transactional
    public WithdrawalView abortBeforeChannel(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String withdrawalNo,
            UUID operationUid,
            VersionedWithdrawalRequest request) {
        requireUuidV4(operationUid);
        WebScope scope = access.webScope(
                platformPath, tenantCode, organizationCode,
                "withdrawal.handle", false);
        WithdrawalRow initial = requiredWithdrawal(
                scope.tenantId(), scope.organizationId(), withdrawalNo, false);
        requireExpectedVersion(initial, request);
        WalletRow wallet = requiredWalletById(initial, true);
        CounterRow counter = lockCounter(initial.tenantId(), initial.organizationId());
        lockActive(initial.walletId());
        WithdrawalRow row = requiredWithdrawal(
                initial.tenantId(), initial.organizationId(), withdrawalNo, true);
        if (!"READY_TO_SUBMIT".equals(row.state()) || transferExists(row.id())) {
            throw stateConflict("微信转账边界已建立，不能在本地终止");
        }
        AccountRow account = requiredAccountById(row, true);
        LocalDateTime now = databaseNow();
        release(row, wallet, counter, account,
                "LOCAL_ABORTED_BEFORE_CHANNEL", false, now);
        appendWebAudit(
                scope, operationUid, "withdrawal.abort-before-channel",
                withdrawalNo, null,
                "{\"status\":\"LOCAL_ABORTED_BEFORE_CHANNEL\"}", now);
        return view(requiredWithdrawal(
                row.tenantId(), row.organizationId(), withdrawalNo, false));
    }

    @Transactional(readOnly = true)
    public MerchantTransferConfirmationView confirmation(String withdrawalNo) {
        MiniappScope scope = access.miniappScope(false);
        WithdrawalRow row = requiredWithdrawal(
                scope.tenantId(), scope.organizationId(), withdrawalNo, false);
        if (row.userId() != scope.organizationUserId()) throw notFound();
        if (!scope.appid().equals(row.appid())) {
            throw new TargetApiException(
                    409, "WITHDRAWAL.APPID_CONTEXT_CHANGED",
                    "当前小程序与提现收款身份不一致");
        }
        return jdbc.query("""
                SELECT package_info, channel_state
                FROM fund_wechat_transfer
                WHERE withdrawal_order_id = ?
                  AND channel_state = 'WAIT_USER_CONFIRM'
                  AND package_info IS NOT NULL
                """, (rs, ignored) -> new MerchantTransferConfirmationView(
                        withdrawalNo, row.appid(), row.mchid(),
                        rs.getString("package_info"),
                        rs.getString("channel_state")), row.id())
                .stream().findFirst()
                .orElseThrow(() -> new TargetApiException(
                        409, "WITHDRAWAL.CONFIRMATION_NOT_READY",
                        "该提现当前不需要用户确认收款"));
    }

    @Transactional
    public WithdrawalView requestChannelAction(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String withdrawalNo,
            UUID operationUid,
            VersionedWithdrawalRequest request) {
        requireUuidV4(operationUid);
        WebScope scope = access.webScope(
                platformPath, tenantCode, organizationCode,
                "withdrawal.handle", false);
        WithdrawalRow row = requiredWithdrawal(
                scope.tenantId(), scope.organizationId(), withdrawalNo, true);
        requireExpectedVersion(row, request);
        if (!"CHANNEL_PROCESSING".equals(row.state())
                || !transferExists(row.id())) {
            throw stateConflict("该提现尚未越过微信渠道边界");
        }
        String type = "QUERY_MERCHANT_TRANSFER";
        String key = type + ":" + withdrawalNo + ":"
                + operationUid.toString().replace("-", "").substring(0, 12)
                .toUpperCase(Locale.ROOT);
        registerTask(row, type, key, databaseNow());
        appendWebAudit(
                scope, operationUid,
                "withdrawal.channel-query",
                withdrawalNo, null,
                "{\"taskType\":\"" + type + "\"}", databaseNow());
        return view(row);
    }

    public ReliableFundsTaskExecutorPort.Result executeTask(
            ReliableFundsTaskExecutorPort.Command command) {
        return switch (command.taskType()) {
            case "SUBMIT_MERCHANT_TRANSFER" -> submitTransfer(command);
            case "QUERY_MERCHANT_TRANSFER" -> queryTransfer(command);
            // Historical cancel tasks can only observe the original transfer.
            // They must never invoke WeChat cancellation again.
            case "CANCEL_MERCHANT_TRANSFER" -> queryTransfer(command);
            default -> new ReliableFundsTaskExecutorPort.Result(
                    ReliableFundsTaskExecutorPort.Result.Outcome.BLOCKED,
                    "unsupported withdrawal task type");
        };
    }

    public boolean applyTrustedNotification(
            long sourceInboxId,
            long trustedTenantId,
            long trustedOrganizationId,
            JsonNode payload) {
        String outBillNo = requiredText(payload, "out_bill_no");
        TransferSnapshot known = transferByOutBillNo(outBillNo, false);
        String state = requiredText(payload, "state");
        String mchid = requiredText(payload, "mch_id");
        String transferBillNo = requiredText(payload, "transfer_bill_no");
        String openid = requiredText(payload, "openid");
        long amount = requiredLong(payload, "transfer_amount");
        String classification = switch (state) {
            case "SUCCESS" -> "SUCCESS";
            case "FAIL" -> "FAIL";
            case "CANCELLED" -> "CANCELLED";
            default -> throw new IllegalArgumentException(
                    "trusted transfer notification is not terminal");
        };
        if (known.tenantId() != trustedTenantId
                || known.organizationId() != trustedOrganizationId
                || !known.mchid().equals(mchid)
                || known.amountCent() != amount
                || !known.withdrawal().openid().equals(openid)
                || (known.transferBillNo() != null
                && !known.transferBillNo().equals(transferBillNo))) {
            throw new IllegalArgumentException(
                    "trusted transfer notification does not match the original bill");
        }
        Integer prior = jdbc.queryForObject("""
                SELECT COUNT(*) FROM fund_wechat_transfer_observation
                WHERE source_inbox_id = ?
                """, Integer.class, sourceInboxId);
        if (prior != null && prior > 0) return false;
        LocalDateTime now = databaseNow();
        if (!"NON_TERMINAL".equals(known.terminalClassification())) {
            TransferSnapshot locked = transferByOutBillNo(outBillNo, true);
            appendTransferInboxObservation(
                    sourceInboxId, locked, payload, state, transferBillNo,
                    amount, openid, now);
            markTerminalConflictIfNeeded(locked, classification, now);
            return false;
        }

        WithdrawalRow initial = known.withdrawal();
        WalletRow wallet = requiredWalletById(initial, true);
        CounterRow counter = lockCounter(
                initial.tenantId(), initial.organizationId());
        if (!activeWithdrawalExists(initial.walletId(), true)) {
            TransferSnapshot locked = transferByOutBillNo(outBillNo, true);
            appendTransferInboxObservation(
                    sourceInboxId, locked, payload, state, transferBillNo,
                    amount, openid, now);
            if (!"NON_TERMINAL".equals(locked.terminalClassification())) {
                markTerminalConflictIfNeeded(locked, classification, now);
                return false;
            }
            throw new IllegalStateException(
                    "non-terminal transfer lost its active withdrawal slot");
        }
        WithdrawalRow order = requiredWithdrawal(
                initial.tenantId(), initial.organizationId(),
                initial.withdrawalNo(), true);
        AccountRow account = requiredAccountById(order, true);
        TransferSnapshot locked = transferByOutBillNo(outBillNo, true);
        appendTransferInboxObservation(
                sourceInboxId, locked, payload, state, transferBillNo,
                amount, openid, now);
        if (!"NON_TERMINAL".equals(locked.terminalClassification())) {
            markTerminalConflictIfNeeded(locked, classification, now);
            return false;
        }
        jdbc.update("""
                UPDATE fund_wechat_transfer
                SET transfer_bill_no = COALESCE(transfer_bill_no, ?),
                    channel_state = ?, terminal_classification = ?,
                    last_api_error_code = NULL, terminal_fail_reason = ?,
                    submitted_at = COALESCE(submitted_at, ?),
                    channel_updated_at = ?, terminal_at = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE id = ? AND terminal_classification = 'NON_TERMINAL'
                """, transferBillNo, state, classification,
                trimTo(text(payload, "fail_reason"), 255),
                now, now, now, now, locked.transferId());
        if (!"CHANNEL_PROCESSING".equals(order.state())) {
            throw new IllegalStateException(
                    "terminal transfer notification found an invalid withdrawal state");
        }
        if ("SUCCESS".equals(classification)) {
            finalizeSuccess(order, wallet, counter, account, now);
        } else {
            release(order, wallet, counter, account,
                    "CANCELLED".equals(classification)
                            ? "CHANNEL_CANCELLED" : "CHANNEL_FAILED",
                    false, now);
        }
        return true;
    }

    private void appendTransferInboxObservation(
            long sourceInboxId,
            TransferSnapshot transfer,
            JsonNode payload,
            String state,
            String transferBillNo,
            long amount,
            String openid,
            LocalDateTime now) {
        jdbc.update("""
                INSERT INTO fund_wechat_transfer_observation (
                    observation_uid, tenant_id, organization_id, transfer_id,
                    observation_type, evidence_source_kind, source_scope_kind,
                    source_inbox_id, source_task_attempt_id,
                    raw_channel_state, api_error_code, terminal_fail_reason,
                    out_bill_no, transfer_bill_no, package_info, amount_cent,
                    openid, channel_occurred_at, content_sha256,
                    observed_at, created_at
                ) VALUES (?, ?, ?, ?, 'CALLBACK', 'INBOX', 'ORGANIZATION',
                          ?, NULL, ?, NULL, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
                """, UUID.randomUUID().toString(), transfer.tenantId(),
                transfer.organizationId(), transfer.transferId(),
                sourceInboxId, state, trimTo(text(payload, "fail_reason"), 255),
                transfer.outBillNo(), transferBillNo, amount, openid,
                databaseTime(parseInstant(requiredText(payload, "update_time"))),
                RechargeApplicationService.sha256(payload.toString()), now, now);
    }

    private void markTerminalConflictIfNeeded(
            TransferSnapshot locked,
            String classification,
            LocalDateTime now) {
        if (!classification.equals(locked.terminalClassification())) {
            jdbc.update("""
                    UPDATE fund_wechat_transfer
                    SET state_conflict = 1, lock_version = lock_version + 1,
                        updated_at = ? WHERE id = ?
                    """, now, locked.transferId());
        }
    }

    private ReliableFundsTaskExecutorPort.Result submitTransfer(
            ReliableFundsTaskExecutorPort.Command command) {
        TransferPreparation preparation = transactions.execute(
                status -> prepareTransfer(
                        command.targetStableKey(), command.taskUid()));
        if (preparation.gateWaitEventUid() != null) {
            return new ReliableFundsTaskExecutorPort.Result(
                    ReliableFundsTaskExecutorPort.Result.Outcome.WAITING,
                    "payout liquidity gate is waiting for exact restoration",
                    Duration.ofDays(30));
        }
        TransferSnapshot transfer = preparation.transfer();
        if (transfer == null) {
            return new ReliableFundsTaskExecutorPort.Result(
                    ReliableFundsTaskExecutorPort.Result.Outcome.WAITING,
                    "local withdrawal guard is waiting",
                    Duration.ofMinutes(1));
        }
        LocalDateTime now = databaseNow();
        if (!preparation.newlyCreated()
                && MerchantTransferPollingPolicy.isQueryWindowExpired(
                transfer.withdrawal().channelBoundaryAt(), now)) {
            return transactions.execute(status -> expireTransferQueryWindow(
                    command, transfer, now));
        }
        boolean submitting = preparation.newlyCreated()
                || shouldResubmitOriginal(transfer);
        MerchantTransferRequest originalRequest = submitting
                ? originalTransferRequest(transfer) : null;
        if (submitting && originalRequest == null) {
            return transactions.execute(status -> {
                TransferSnapshot locked = lockTransfer(
                        transfer.withdrawalNo());
                LocalDateTime observedAt = databaseNow();
                observeTransferIssue(
                        command, locked,
                        "FUNDS.MERCHANT_TRANSFER_ORIGINAL_REQUEST_UNAVAILABLE",
                        "CRITICAL", "ORIGINAL_REQUEST_DIGEST_MISMATCH",
                        null, observedAt);
                return new ReliableFundsTaskExecutorPort.Result(
                        ReliableFundsTaskExecutorPort.Result.Outcome.BLOCKED,
                        "original transfer parameters cannot be reproduced safely");
            });
        }
        attemptBoundary.markExternalCallMayHaveStarted(command.attemptUid());
        MerchantTransferResult response = submitting
                ? channel.submit(originalRequest)
                : channel.query(
                        new MerchantTransferChannelPort.MerchantTransferQuery(
                                transfer.mchid(), transfer.outBillNo()));
        return transactions.execute(status -> mergeTransferResult(
                command, transfer,
                submitting ? "SUBMIT_RESPONSE" : "QUERY",
                response, submitting, true));
    }

    private ReliableFundsTaskExecutorPort.Result queryTransfer(
            ReliableFundsTaskExecutorPort.Command command) {
        TransferSnapshot transfer = transferSnapshot(command.targetStableKey());
        if (!"NON_TERMINAL".equals(transfer.terminalClassification())) {
            return new ReliableFundsTaskExecutorPort.Result(
                    ReliableFundsTaskExecutorPort.Result.Outcome.DONE,
                    "transfer is already terminal");
        }
        LocalDateTime now = databaseNow();
        if (MerchantTransferPollingPolicy.isQueryWindowExpired(
                transfer.withdrawal().channelBoundaryAt(), now)) {
            return transactions.execute(status -> expireTransferQueryWindow(
                    command, transfer, now));
        }
        attemptBoundary.markExternalCallMayHaveStarted(command.attemptUid());
        MerchantTransferResult response = channel.query(
                new MerchantTransferChannelPort.MerchantTransferQuery(
                        transfer.mchid(), transfer.outBillNo()));
        return transactions.execute(status -> mergeTransferResult(
                command, transfer, "QUERY", response,
                false, false));
    }

    private TransferPreparation prepareTransfer(
            String withdrawalNo, UUID taskUid) {
        WithdrawalRow initial = requiredWithdrawalByNo(withdrawalNo, false);
        if ("CHANNEL_PROCESSING".equals(initial.state())) {
            TransferSnapshot existing = transferSnapshot(withdrawalNo);
            if ("NOT_ENOUGH".equals(existing.lastApiErrorCode())) {
                GateRow gate = requiredGate(existing.merchantId(), true);
                if (!"OPEN".equals(gate.state())) {
                    markPayoutTaskWaiting(taskUid, gate);
                    return TransferPreparation.waiting(
                            gate.currentPauseEventUid());
                }
            }
            if (shouldResubmitOriginal(existing)) {
                GateRow gate = requiredGate(existing.merchantId(), true);
                if (!"OPEN".equals(gate.state())) {
                    markPayoutTaskWaiting(taskUid, gate);
                    return TransferPreparation.waiting(
                            gate.currentPauseEventUid());
                }
                if (!lockAndVerifyCurrentBinding(existing.withdrawal())) {
                    return TransferPreparation.localWait();
                }
                existing = lockAndVerifyResubmissionFunds(existing);
            }
            return TransferPreparation.existing(existing);
        }
        if (!"READY_TO_SUBMIT".equals(initial.state())
                || initial.negativePause()) {
            return TransferPreparation.localWait();
        }
        GateRow gate = requiredGate(initial.merchantId(), true);
        if (!"OPEN".equals(gate.state())) {
            markPayoutTaskWaiting(taskUid, gate);
            return TransferPreparation.waiting(gate.currentPauseEventUid());
        }
        if (!lockAndVerifyCurrentBinding(initial)) {
            return TransferPreparation.localWait();
        }
        WalletRow wallet = requiredWalletById(initial, true);
        lockActive(initial.walletId());
        WithdrawalRow row = requiredWithdrawal(
                initial.tenantId(), initial.organizationId(), withdrawalNo, true);
        AccountRow account = requiredAccountById(row, true);
        if (row.negativePause()
                || !"READY_TO_SUBMIT".equals(row.state())) {
            return TransferPreparation.localWait();
        }
        if (wallet.frozenCent() < row.amountCent()
                || account.frozenCent() < row.amountCent()) {
            throw stateConflict("提现双方冻结事实不完整，已停止提交微信");
        }
        String outBillNo = "MT" + withdrawalNo.substring(2);
        String notifyUrl = notifyBaseUrl
                + "/api/v1/wechat-pay/notifications/merchant-transfers";
        String remark = "环保回收提现";
        MerchantTransferRequest originalRequest = new MerchantTransferRequest(
                row.mchid(), row.appid(), outBillNo, row.openid(),
                row.amountCent(), row.sceneId(), row.reportType(),
                row.reportContent(), remark, row.pageStyle(), notifyUrl);
        LocalDateTime now = databaseNow();
        jdbc.update("""
                INSERT INTO fund_wechat_transfer (
                    transfer_uid, tenant_id, organization_id,
                    withdrawal_order_id, merchant_profile_id,
                    miniapp_merchant_binding_id, organization_miniapp_id,
                    out_bill_no, transfer_bill_no, amount_cent,
                    mchid_snapshot, appid_snapshot, openid_snapshot,
                    scene_id_snapshot, report_type_snapshot,
                    report_content_snapshot, transfer_remark,
                    transfer_page_style_snapshot, notify_url_snapshot,
                    notify_url_sha256, request_sha256,
                    channel_state, terminal_classification,
                    package_info, last_api_error_code, terminal_fail_reason,
                    state_conflict, submitted_at, channel_updated_at,
                    terminal_at, lock_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, NULL, 'NON_TERMINAL', NULL, NULL, NULL,
                          0, NULL, NULL, NULL, 0, ?, ?)
                """, UUID.randomUUID().toString(), row.tenantId(),
                row.organizationId(), row.id(), row.merchantId(),
                row.bindingId(), row.miniappId(), outBillNo, row.amountCent(),
                row.mchid(), row.appid(), row.openid(), row.sceneId(),
                row.reportType(), row.reportContent(), remark,
                row.pageStyle(), notifyUrl,
                RechargeApplicationService.sha256(notifyUrl),
                transferRequestHash(originalRequest), now, now);
        jdbc.update("""
                UPDATE fund_withdrawal_order
                SET business_state = 'CHANNEL_PROCESSING',
                    channel_boundary_at = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE id = ? AND business_state = 'READY_TO_SUBMIT'
                """, now, now, row.id());
        return TransferPreparation.created(transferSnapshot(withdrawalNo));
    }

    private TransferSnapshot lockAndVerifyResubmissionFunds(
            TransferSnapshot transfer) {
        WithdrawalRow initial = transfer.withdrawal();
        WalletRow wallet = requiredWalletById(initial, true);
        lockActive(initial.walletId());
        WithdrawalRow order = requiredWithdrawal(
                initial.tenantId(), initial.organizationId(),
                initial.withdrawalNo(), true);
        AccountRow account = requiredAccountById(order, true);
        TransferSnapshot locked = lockTransfer(initial.withdrawalNo());
        if (!"CHANNEL_PROCESSING".equals(order.state())
                || !"NON_TERMINAL".equals(
                locked.terminalClassification())
                || !shouldResubmitOriginal(locked)) {
            throw stateConflict("原微信转账单已变化，已停止重复提交");
        }
        if (wallet.frozenCent() < order.amountCent()
                || account.frozenCent() < order.amountCent()) {
            throw stateConflict("提现双方冻结事实不完整，已停止续办微信原单");
        }
        return locked;
    }

    private void markPayoutTaskWaiting(UUID taskUid, GateRow gate) {
        if (gate.currentPauseEventUid() == null) {
            throw stateConflict("出款闸门暂停但缺少当前暂停事件");
        }
        operationalControl.markPayoutTaskWaiting(
                taskUid, gate.merchantId(), gate.currentPauseEventUid(),
                databaseNow());
    }

    private ReliableFundsTaskExecutorPort.Result mergeTransferResult(
            ReliableFundsTaskExecutorPort.Command command,
            TransferSnapshot known,
            String observationType,
            MerchantTransferResult result,
            boolean submitAttempt,
            boolean keepCurrentTask) {
        LocalDateTime now = databaseNow();
        if (result.outcome() == MerchantTransferResult.Outcome.NOT_ENOUGH) {
            requiredGate(known.merchantId(), true);
        }
        long observationId = appendTransferObservation(
                command, known, observationType, result, now);
        Validation evidence = validateTransferEvidence(
                known, observationType, result);
        if (!evidence.trusted()) {
            observeTransferIssue(
                    command, known,
                    "FUNDS.MERCHANT_TRANSFER_EVIDENCE_MISMATCH",
                    "CRITICAL", evidence.safeSummary(), result, now);
            return new ReliableFundsTaskExecutorPort.Result(
                    ReliableFundsTaskExecutorPort.Result.Outcome.BLOCKED,
                    "Wechat transfer evidence mismatch: "
                            + evidence.safeSummary());
        }
        String classification = classification(result.outcome());
        boolean submitTerminalNeedsQuery = classification != null
                && "SUBMIT_RESPONSE".equals(observationType);
        if (classification != null && !submitTerminalNeedsQuery) {
            if (!settleTerminal(
                    command, known, result, classification, now)) {
                return new ReliableFundsTaskExecutorPort.Result(
                        ReliableFundsTaskExecutorPort.Result.Outcome.BLOCKED,
                        "transfer terminal evidence conflicts with current projection");
            }
            return new ReliableFundsTaskExecutorPort.Result(
                    ReliableFundsTaskExecutorPort.Result.Outcome.DONE,
                    "trusted transfer terminal merged");
        }
        TransferSnapshot transfer = lockTransfer(known.withdrawalNo());
        if (!"NON_TERMINAL".equals(transfer.terminalClassification())) {
            return new ReliableFundsTaskExecutorPort.Result(
                    ReliableFundsTaskExecutorPort.Result.Outcome.DONE,
                    "late non-terminal observation preserved");
        }
        if (transferBillConflicts(transfer, result)) {
            markTransferConflictWhenNeeded(transfer, "CONFLICT", now);
            observeTransferIssue(
                    command, transfer,
                    "FUNDS.MERCHANT_TRANSFER_EVIDENCE_MISMATCH",
                    "CRITICAL", "TRANSFER_BILL_NO_MISMATCH", result, now);
            return new ReliableFundsTaskExecutorPort.Result(
                    ReliableFundsTaskExecutorPort.Result.Outcome.BLOCKED,
                    "Wechat transfer bill identity changed concurrently");
        }
        jdbc.update("""
                UPDATE fund_wechat_transfer
                SET transfer_bill_no = COALESCE(transfer_bill_no, ?),
                    package_info = CASE WHEN ? IS NOT NULL THEN ?
                                        ELSE package_info END,
                    channel_state = ?, last_api_error_code = ?,
                    submitted_at = CASE WHEN ? THEN COALESCE(submitted_at, ?)
                                        ELSE submitted_at END,
                    channel_updated_at = ?, lock_version = lock_version + 1,
                    updated_at = ?
                WHERE id = ? AND terminal_classification = 'NON_TERMINAL'
                """, result.transferBillNo(), result.packageInfo(),
                result.packageInfo(), result.channelState(), result.errorCode(),
                submitAttempt, now, now, now, transfer.transferId());
        if (result.outcome() == MerchantTransferResult.Outcome.NOT_ENOUGH) {
            UUID pausedEventUid = pausePayoutGate(
                    transfer, observationId, now);
            operationalControl.markPayoutTaskWaiting(
                    command.taskUid(), transfer.merchantId(),
                    pausedEventUid, now);
            return new ReliableFundsTaskExecutorPort.Result(
                    ReliableFundsTaskExecutorPort.Result.Outcome.WAITING,
                    "payout liquidity is paused; original submit task retained",
                    Duration.ofDays(30));
        }
        markLongUnsettledIfNeeded(command, transfer, result, now);
        if (result.outcome()
                == MerchantTransferResult.Outcome.UNKNOWN_STATE) {
            observeTransferIssue(
                    command, transfer,
                    "FUNDS.MERCHANT_TRANSFER_UNKNOWN_STATE",
                    "CRITICAL", "UNKNOWN_CHANNEL_STATE", result, now);
            return new ReliableFundsTaskExecutorPort.Result(
                    ReliableFundsTaskExecutorPort.Result.Outcome.BLOCKED,
                    "unknown Wechat transfer state requires reconciliation");
        }
        if (result.outcome()
                == MerchantTransferResult.Outcome.PERMANENT_FAILURE) {
            observeTransferIssue(
                    command, transfer,
                    "FUNDS.MERCHANT_TRANSFER_CHANNEL_CONFIGURATION",
                    "CRITICAL", "PERMANENT_CHANNEL_ERROR", result, now);
            return new ReliableFundsTaskExecutorPort.Result(
                    ReliableFundsTaskExecutorPort.Result.Outcome.BLOCKED,
                    "permanent transfer channel error requires recovery");
        }
        if (result.outcome() == MerchantTransferResult.Outcome.NOT_FOUND
                && !"QUERY".equals(observationType)) {
            observeTransferIssue(
                    command, transfer,
                    "FUNDS.MERCHANT_TRANSFER_UNEXPECTED_NOT_FOUND",
                    "CRITICAL", "NOT_FOUND_OUTSIDE_QUERY", result, now);
            return new ReliableFundsTaskExecutorPort.Result(
                    ReliableFundsTaskExecutorPort.Result.Outcome.BLOCKED,
                    "unexpected missing transfer result");
        }
        Duration delay = MerchantTransferPollingPolicy.nextDelay(
                transfer.withdrawal().channelBoundaryAt(), now);
        if (result.outcome()
                == MerchantTransferResult.Outcome.RETRYABLE_FAILURE) {
            return new ReliableFundsTaskExecutorPort.Result(
                    ReliableFundsTaskExecutorPort.Result.Outcome.RETRY,
                    "temporary transfer channel error", delay);
        }
        if (result.outcome() == MerchantTransferResult.Outcome.NOT_FOUND) {
            if (!keepCurrentTask) {
                return new ReliableFundsTaskExecutorPort.Result(
                        ReliableFundsTaskExecutorPort.Result.Outcome.DONE,
                        "manual query confirmed original bill absent; automatic submit workflow owns recovery");
            }
            return new ReliableFundsTaskExecutorPort.Result(
                    ReliableFundsTaskExecutorPort.Result.Outcome.WAITING,
                    "original bill confirmed absent; original parameters will be resubmitted",
                    Duration.ofSeconds(30));
        }
        if (!keepCurrentTask) {
            return new ReliableFundsTaskExecutorPort.Result(
                    ReliableFundsTaskExecutorPort.Result.Outcome.DONE,
                    "manual transfer observation merged");
        }
        return new ReliableFundsTaskExecutorPort.Result(
                ReliableFundsTaskExecutorPort.Result.Outcome.WAITING,
                "transfer remains non-terminal", delay);
    }

    private boolean settleTerminal(
            ReliableFundsTaskExecutorPort.Command command,
            TransferSnapshot known,
            MerchantTransferResult result,
            String classification,
            LocalDateTime now) {
        if (!"NON_TERMINAL".equals(known.terminalClassification())) {
            TransferSnapshot locked = lockTransfer(known.withdrawalNo());
            markTransferConflictWhenNeeded(
                    locked, classification, now);
            if (!classification.equals(locked.terminalClassification())) {
                observeTransferIssue(
                        command, locked,
                        "FUNDS.MERCHANT_TRANSFER_TERMINAL_CONFLICT",
                        "CRITICAL", "OPPOSITE_TERMINAL_STATE", result, now);
                return false;
            }
            return true;
        }
        WithdrawalRow initial = known.withdrawal();
        WalletRow wallet = requiredWalletById(initial, true);
        CounterRow counter = lockCounter(initial.tenantId(), initial.organizationId());
        TransferSnapshot latest = transferSnapshot(initial.withdrawalNo());
        if (!"NON_TERMINAL".equals(latest.terminalClassification())) {
            TransferSnapshot locked = lockTransfer(initial.withdrawalNo());
            markTransferConflictWhenNeeded(
                    locked, classification, now);
            if (!classification.equals(locked.terminalClassification())) {
                observeTransferIssue(
                        command, locked,
                        "FUNDS.MERCHANT_TRANSFER_TERMINAL_CONFLICT",
                        "CRITICAL", "OPPOSITE_TERMINAL_STATE", result, now);
                return false;
            }
            return true;
        }
        lockActive(initial.walletId());
        WithdrawalRow order = requiredWithdrawal(
                initial.tenantId(), initial.organizationId(),
                initial.withdrawalNo(), true);
        AccountRow account = requiredAccountById(order, true);
        TransferSnapshot locked = lockTransfer(initial.withdrawalNo());
        if (!"NON_TERMINAL".equals(locked.terminalClassification())) {
            markTransferConflictWhenNeeded(locked, classification, now);
            if (!classification.equals(locked.terminalClassification())) {
                observeTransferIssue(
                        command, locked,
                        "FUNDS.MERCHANT_TRANSFER_TERMINAL_CONFLICT",
                        "CRITICAL", "OPPOSITE_TERMINAL_STATE", result, now);
                return false;
            }
            return true;
        }
        if (transferBillConflicts(locked, result)) {
            markTransferConflictWhenNeeded(locked, "CONFLICT", now);
            observeTransferIssue(
                    command, locked,
                    "FUNDS.MERCHANT_TRANSFER_EVIDENCE_MISMATCH",
                    "CRITICAL", "TRANSFER_BILL_NO_MISMATCH", result, now);
            return false;
        }
        jdbc.update("""
                UPDATE fund_wechat_transfer
                SET transfer_bill_no = COALESCE(transfer_bill_no, ?),
                    channel_state = ?, terminal_classification = ?,
                    package_info = CASE WHEN ? IS NOT NULL THEN ?
                                        ELSE package_info END,
                    last_api_error_code = ?, terminal_fail_reason = ?,
                    submitted_at = COALESCE(submitted_at, ?),
                    channel_updated_at = ?, terminal_at = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE id = ? AND terminal_classification = 'NON_TERMINAL'
                """, result.transferBillNo(), result.channelState(),
                classification, result.packageInfo(), result.packageInfo(),
                result.errorCode(), trimTo(result.failReason(), 255),
                now, now, now, now, locked.transferId());
        if (!"CHANNEL_PROCESSING".equals(order.state())) return true;
        if ("SUCCESS".equals(classification)) {
            finalizeSuccess(order, wallet, counter, account, now);
        } else {
            release(order, wallet, counter, account,
                    "CANCELLED".equals(classification)
                            ? "CHANNEL_CANCELLED" : "CHANNEL_FAILED",
                    false, now);
        }
        return true;
    }

    private void markTransferConflictWhenNeeded(
            TransferSnapshot locked,
            String classification,
            LocalDateTime now) {
        if (!classification.equals(locked.terminalClassification())) {
            jdbc.update("""
                    UPDATE fund_wechat_transfer
                    SET state_conflict = 1, lock_version = lock_version + 1,
                        updated_at = ? WHERE id = ?
                    """, now, locked.transferId());
        }
    }

    private static boolean transferBillConflicts(
            TransferSnapshot transfer, MerchantTransferResult result) {
        return transfer.transferBillNo() != null
                && result.transferBillNo() != null
                && !transfer.transferBillNo().equals(
                result.transferBillNo());
    }

    private Validation validateTransferEvidence(
            TransferSnapshot transfer,
            String observationType,
            MerchantTransferResult result) {
        boolean authoritativeQuery = "QUERY".equals(observationType)
                && switch (result.outcome()) {
                    case PROCESSING, WAIT_USER_CONFIRM, SUCCESS, FAIL,
                            CANCELLED, UNKNOWN_STATE -> true;
                    default -> false;
                };
        if (authoritativeQuery) {
            return WechatChannelEvidencePolicy.validateTransferQuery(
                    transfer.mchid(), transfer.appid(), transfer.outBillNo(),
                    transfer.transferBillNo(), transfer.amountCent(),
                    transfer.openid(), result);
        }
        return WechatChannelEvidencePolicy.validateTransferResponseFields(
                transfer.mchid(), transfer.appid(), transfer.outBillNo(),
                transfer.transferBillNo(), transfer.amountCent(),
                transfer.openid(), result);
    }

    private ReliableFundsTaskExecutorPort.Result expireTransferQueryWindow(
            ReliableFundsTaskExecutorPort.Command command,
            TransferSnapshot known,
            LocalDateTime now) {
        TransferSnapshot transfer = lockTransfer(known.withdrawalNo());
        if (!"NON_TERMINAL".equals(transfer.terminalClassification())) {
            return new ReliableFundsTaskExecutorPort.Result(
                    ReliableFundsTaskExecutorPort.Result.Outcome.DONE,
                    "transfer became terminal before query-window stop");
        }
        markLongUnsettledIfNeeded(command, transfer, null, now);
        observeTransferIssue(
                command, transfer,
                "FUNDS.MERCHANT_TRANSFER_QUERY_WINDOW_EXPIRED",
                "CRITICAL", "WECHAT_QUERY_WINDOW_30_DAYS_EXPIRED",
                null, now);
        return new ReliableFundsTaskExecutorPort.Result(
                ReliableFundsTaskExecutorPort.Result.Outcome.BLOCKED,
                "Wechat transfer query window expired; reconciliation is required");
    }

    private void markLongUnsettledIfNeeded(
            ReliableFundsTaskExecutorPort.Command command,
            TransferSnapshot transfer,
            MerchantTransferResult result,
            LocalDateTime now) {
        WithdrawalRow order = transfer.withdrawal();
        if (order.longUnsettledAt() != null
                || !MerchantTransferPollingPolicy.isLongUnsettled(
                order.channelBoundaryAt(), now)) {
            return;
        }
        int updated = jdbc.update("""
                UPDATE fund_withdrawal_order
                SET long_unsettled_at = ?, lock_version = lock_version + 1,
                    updated_at = ?
                WHERE id = ? AND business_state = 'CHANNEL_PROCESSING'
                  AND long_unsettled_at IS NULL
                """, now, now, order.id());
        if (updated == 1) {
            observeTransferIssue(
                    command, transfer,
                    "FUNDS.MERCHANT_TRANSFER_LONG_UNSETTLED",
                    "WARNING", "NON_TERMINAL_FOR_AT_LEAST_30_MINUTES",
                    result, now);
        }
    }

    private void observeTransferIssue(
            ReliableFundsTaskExecutorPort.Command command,
            TransferSnapshot transfer,
            String issueCode,
            String severity,
            String reason,
            MerchantTransferResult result,
            LocalDateTime now) {
        String state = result == null ? null : result.channelState();
        String error = result == null ? null : result.errorCode();
        String evidence = issueCode + "|" + transfer.outBillNo() + "|"
                + safe(state) + "|" + safe(error) + "|" + reason;
        String summary = "reason=" + reason
                + "; channelState=" + safe(state)
                + "; errorCode=" + safe(error);
        operationalControl.observeReconciliationIssue(
                new ReconciliationIssue(
                        transfer.tenantId(), transfer.organizationId(),
                        command.sourceTaskAttemptId(), issueCode, severity,
                        "WECHAT_TRANSFER", transfer.outBillNo(),
                        RechargeApplicationService.sha256(evidence),
                        summary, now));
    }

    private long appendTransferObservation(
            ReliableFundsTaskExecutorPort.Command command,
            TransferSnapshot transfer,
            String type,
            MerchantTransferResult result,
            LocalDateTime now) {
        String content = type + "|" + safe(result.channelState()) + "|"
                + safe(result.transferBillNo()) + "|" + safe(result.errorCode())
                + "|" + safe(result.mchid()) + "|" + safe(result.appid())
                + "|" + safe(result.outBillNo()) + "|"
                + result.transferAmountCent() + "|" + safe(result.openid());
        jdbc.update("""
                INSERT INTO fund_wechat_transfer_observation (
                    observation_uid, tenant_id, organization_id, transfer_id,
                    observation_type, evidence_source_kind, source_scope_kind,
                    source_inbox_id, source_task_attempt_id,
                    raw_channel_state, api_error_code, terminal_fail_reason,
                    observed_mchid, observed_appid, out_bill_no,
                    observed_out_bill_no, transfer_bill_no, package_info,
                    amount_cent, openid, channel_occurred_at, content_sha256,
                    observed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, 'TASK_ATTEMPT', 'ORGANIZATION',
                          NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, UUID.randomUUID().toString(), transfer.tenantId(),
                transfer.organizationId(), transfer.transferId(), type,
                command.sourceTaskAttemptId(), result.channelState(),
                result.errorCode(), trimTo(result.failReason(), 255),
                result.mchid(), result.appid(), transfer.outBillNo(),
                result.outBillNo(), result.transferBillNo(),
                result.packageInfo(), result.transferAmountCent(),
                result.openid(),
                databaseTime(result.channelTime()),
                RechargeApplicationService.sha256(content), now, now);
        return requiredLong("""
                SELECT id FROM fund_wechat_transfer_observation
                WHERE source_task_attempt_id = ?
                """, command.sourceTaskAttemptId());
    }

    private UUID pausePayoutGate(
            TransferSnapshot transfer,
            long observationId,
            LocalDateTime now) {
        GateRow gate = requiredGate(transfer.merchantId(), true);
        if (!"OPEN".equals(gate.state())) {
            if (gate.currentPauseEventUid() != null) {
                operationalControl.observePayoutLiquidityPause(
                        transfer.merchantId(),
                        gate.currentPauseEventUid(), now);
                return gate.currentPauseEventUid();
            }
            throw stateConflict("出款闸门暂停但缺少当前暂停事件");
        }
        UUID eventUid = UUID.randomUUID();
        jdbc.update("""
                INSERT INTO fund_payout_gate_event (
                    event_uid, merchant_profile_id, event_type,
                    triggering_transfer_observation_id,
                    triggering_transfer_id, original_pause_event_id,
                    original_pause_event_type,
                    restored_by_platform_admin_id, note,
                    occurred_at, created_at
                ) VALUES (?, ?, 'PAUSED', ?, ?, NULL, NULL, NULL,
                          '微信返回 NOT_ENOUGH', ?, ?)
                """, eventUid.toString(), transfer.merchantId(), observationId,
                transfer.transferId(), now, now);
        long eventId = requiredLong(
                "SELECT id FROM fund_payout_gate_event WHERE event_uid = ?",
                eventUid.toString());
        jdbc.update("""
                UPDATE fund_payout_gate
                SET gate_state = 'PAUSED_NOT_ENOUGH',
                    current_pause_event_id = ?, paused_at = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE merchant_profile_id = ? AND gate_state = 'OPEN'
                """, eventId, now, now, transfer.merchantId());
        operationalControl.observePayoutLiquidityPause(
                transfer.merchantId(), eventUid, now);
        return eventUid;
    }

    private void freezeWallet(
            MiniappScope scope,
            WalletRow wallet,
            CounterRow counter,
            long withdrawalId,
            String withdrawalNo,
            long amount,
            LocalDateTime now) {
        long sequence = wallet.lastSequence() + 1;
        long visibility = counter.lastVisibilitySequence() + 1;
        long availableAfter = wallet.availableCent() - amount;
        long frozenAfter = wallet.frozenCent() + amount;
        jdbc.update("""
                UPDATE fund_organization_wallet_entry_counter
                SET last_visibility_sequence_no = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE organization_id = ? AND last_visibility_sequence_no = ?
                """, visibility, now, scope.organizationId(),
                counter.lastVisibilitySequence());
        jdbc.update("""
                UPDATE fund_user_wallet
                SET available_balance_cent = ?, frozen_withdrawal_cent = ?,
                    last_entry_sequence_no = ?, lock_version = lock_version + 1,
                    updated_at = ?
                WHERE id = ? AND lock_version = ?
                """, availableAfter, frozenAfter, sequence, now,
                wallet.id(), wallet.version());
        jdbc.update("""
                INSERT INTO fund_user_wallet_entry (
                    entry_uid, tenant_id, organization_id, wallet_id,
                    organization_user_id, organization_user_uid,
                    entry_sequence_no,
                    visibility_sequence_no, event_type,
                    available_delta_cent, available_before_cent,
                    available_after_cent, frozen_delta_cent,
                    frozen_before_cent, frozen_after_cent,
                    delivery_revision_id, withdrawal_order_id, adjustment_id,
                    fund_phase, source_type, source_no,
                    occurred_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'WITHDRAWAL_FREEZE',
                          ?, ?, ?, ?, ?, ?, NULL, ?, NULL, 'FREEZE',
                          'WITHDRAWAL_ORDER', ?, ?, ?)
                """, UUID.randomUUID().toString(), scope.tenantId(),
                scope.organizationId(), wallet.id(), scope.organizationUserId(),
                scope.organizationUserUid().toString(), sequence, visibility,
                -amount, wallet.availableCent(),
                availableAfter, amount, wallet.frozenCent(), frozenAfter,
                withdrawalId, withdrawalNo, now, now);
    }

    private void freezeAccount(
            MiniappScope scope,
            AccountRow account,
            long withdrawalId,
            long amount,
            LocalDateTime now) {
        long availableAfter = account.availableCent() - amount;
        long frozenAfter = account.frozenCent() + amount;
        jdbc.update("""
                UPDATE fund_organization_payout_account
                SET available_payout_cent = ?, frozen_withdrawal_cent = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE id = ? AND lock_version = ?
                """, availableAfter, frozenAfter, now,
                account.id(), account.version());
        insertPayoutFinalEntry(
                scope.tenantId(), scope.organizationId(), account.id(),
                withdrawalId, "WITHDRAWAL_FREEZE", "FREEZE",
                -amount, account.availableCent(), availableAfter,
                amount, account.frozenCent(), frozenAfter, now);
    }

    private void release(
            WithdrawalRow order,
            WalletRow wallet,
            CounterRow counter,
            AccountRow account,
            String terminalState,
            boolean reviewed,
            LocalDateTime now) {
        long sequence = wallet.lastSequence() + 1;
        long visibility = counter.lastVisibilitySequence() + 1;
        long availableAfter = wallet.availableCent() + order.amountCent();
        long frozenAfter = wallet.frozenCent() - order.amountCent();
        updateCounter(order, counter, visibility, now);
        updateWallet(wallet, availableAfter, frozenAfter, sequence, now);
        insertWalletFinalEntry(order, wallet, sequence, visibility,
                "WITHDRAWAL_RELEASED", order.amountCent(), availableAfter,
                -order.amountCent(), frozenAfter, now);
        long accountAvailableAfter = account.availableCent() + order.amountCent();
        long accountFrozenAfter = account.frozenCent() - order.amountCent();
        updateAccount(account, accountAvailableAfter, accountFrozenAfter, now);
        insertPayoutFinalEntry(
                order.tenantId(), order.organizationId(), account.id(),
                order.id(), "WITHDRAWAL_RELEASED", "FINAL",
                order.amountCent(), account.availableCent(), accountAvailableAfter,
                -order.amountCent(), account.frozenCent(), accountFrozenAfter, now);
        jdbc.update("DELETE FROM fund_active_withdrawal WHERE wallet_id = ?",
                order.walletId());
        jdbc.update("""
                UPDATE fund_withdrawal_order
                SET business_state = ?,
                    reviewed_at = CASE WHEN ? THEN COALESCE(reviewed_at, ?)
                                       ELSE reviewed_at END,
                    channel_terminal_at = CASE WHEN channel_boundary_at IS NOT NULL
                                               THEN ? ELSE NULL END,
                    ended_at = ?, lock_version = lock_version + 1,
                    updated_at = ?
                WHERE id = ?
                """, terminalState, reviewed, now, now, now, now, order.id());
    }

    private void finalizeSuccess(
            WithdrawalRow order,
            WalletRow wallet,
            CounterRow counter,
            AccountRow account,
            LocalDateTime now) {
        long sequence = wallet.lastSequence() + 1;
        long visibility = counter.lastVisibilitySequence() + 1;
        long frozenAfter = wallet.frozenCent() - order.amountCent();
        updateCounter(order, counter, visibility, now);
        updateWallet(wallet, wallet.availableCent(), frozenAfter, sequence, now);
        insertWalletFinalEntry(order, wallet, sequence, visibility,
                "WITHDRAWAL_SUCCEEDED", 0, wallet.availableCent(),
                -order.amountCent(), frozenAfter, now);
        long accountFrozenAfter = account.frozenCent() - order.amountCent();
        updateAccount(account, account.availableCent(), accountFrozenAfter, now);
        insertPayoutFinalEntry(
                order.tenantId(), order.organizationId(), account.id(),
                order.id(), "WITHDRAWAL_SUCCEEDED", "FINAL",
                0, account.availableCent(), account.availableCent(),
                -order.amountCent(), account.frozenCent(), accountFrozenAfter, now);
        jdbc.update("DELETE FROM fund_active_withdrawal WHERE wallet_id = ?",
                order.walletId());
        jdbc.update("""
                UPDATE fund_withdrawal_order
                SET business_state = 'SUCCEEDED', channel_terminal_at = ?,
                    ended_at = ?, lock_version = lock_version + 1,
                    updated_at = ?
                WHERE id = ? AND business_state = 'CHANNEL_PROCESSING'
                """, now, now, now, order.id());
    }

    private void updateCounter(
            WithdrawalRow order,
            CounterRow counter,
            long visibility,
            LocalDateTime now) {
        jdbc.update("""
                UPDATE fund_organization_wallet_entry_counter
                SET last_visibility_sequence_no = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE organization_id = ? AND last_visibility_sequence_no = ?
                """, visibility, now, order.organizationId(),
                counter.lastVisibilitySequence());
    }

    private void updateWallet(
            WalletRow wallet,
            long available,
            long frozen,
            long sequence,
            LocalDateTime now) {
        if (frozen < 0) throw new IllegalStateException("wallet frozen underflow");
        jdbc.update("""
                UPDATE fund_user_wallet
                SET available_balance_cent = ?, frozen_withdrawal_cent = ?,
                    last_entry_sequence_no = ?, lock_version = lock_version + 1,
                    updated_at = ?
                WHERE id = ? AND lock_version = ?
                """, available, frozen, sequence, now,
                wallet.id(), wallet.version());
    }

    private void updateAccount(
            AccountRow account,
            long available,
            long frozen,
            LocalDateTime now) {
        if (available < 0 || frozen < 0) {
            throw new IllegalStateException("organization payout underflow");
        }
        jdbc.update("""
                UPDATE fund_organization_payout_account
                SET available_payout_cent = ?, frozen_withdrawal_cent = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE id = ? AND lock_version = ?
                """, available, frozen, now, account.id(), account.version());
    }

    private void insertWalletFinalEntry(
            WithdrawalRow order,
            WalletRow wallet,
            long sequence,
            long visibility,
            String eventType,
            long availableDelta,
            long availableAfter,
            long frozenDelta,
            long frozenAfter,
            LocalDateTime now) {
        String organizationUserUid = jdbc.queryForObject("""
                SELECT organization_user_uid
                FROM fund_user_wallet_entry
                WHERE withdrawal_order_id = ? AND fund_phase = 'FREEZE'
                """, String.class, order.id());
        jdbc.update("""
                INSERT INTO fund_user_wallet_entry (
                    entry_uid, tenant_id, organization_id, wallet_id,
                    organization_user_id, organization_user_uid,
                    entry_sequence_no,
                    visibility_sequence_no, event_type,
                    available_delta_cent, available_before_cent,
                    available_after_cent, frozen_delta_cent,
                    frozen_before_cent, frozen_after_cent,
                    delivery_revision_id, withdrawal_order_id, adjustment_id,
                    fund_phase, source_type, source_no,
                    occurred_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          NULL, ?, NULL, 'FINAL', 'WITHDRAWAL_ORDER', ?, ?, ?)
                """, UUID.randomUUID().toString(), order.tenantId(),
                order.organizationId(), wallet.id(), order.userId(),
                organizationUserUid, sequence, visibility, eventType,
                availableDelta, wallet.availableCent(), availableAfter,
                frozenDelta, wallet.frozenCent(), frozenAfter, order.id(),
                order.withdrawalNo(), now, now);
    }

    private void insertPayoutFinalEntry(
            long tenantId,
            long organizationId,
            long accountId,
            long withdrawalId,
            String eventType,
            String phase,
            long availableDelta,
            long availableBefore,
            long availableAfter,
            long frozenDelta,
            long frozenBefore,
            long frozenAfter,
            LocalDateTime now) {
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
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?,
                          NULL, NULL, NULL, ?, ?)
                """, UUID.randomUUID().toString(), tenantId, organizationId,
                accountId, eventType, availableDelta, availableBefore,
                availableAfter, frozenDelta, frozenBefore, frozenAfter,
                withdrawalId, phase, now, now);
    }

    private WithdrawalPage list(
            long tenantId,
            long organizationId,
            Long userId,
            String state,
            Integer requestedLimit) {
        int limit = normalizeLimit(requestedLimit);
        List<WithdrawalView> items = jdbc.query("""
                SELECT w.*, t.channel_state, t.terminal_classification,
                       t.package_info,
                       m.scene_id, m.report_type, m.report_content,
                       m.transfer_page_style
                FROM fund_withdrawal_order w
                LEFT JOIN fund_wechat_transfer t
                  ON t.withdrawal_order_id = w.id
                JOIN fund_wechat_merchant_profile m
                  ON m.id = w.merchant_profile_id
                WHERE w.tenant_id = ? AND w.organization_id = ?
                  AND (? IS NULL OR w.organization_user_id = ?)
                  AND (? IS NULL OR w.business_state = ?)
                ORDER BY w.created_at DESC, w.withdrawal_order_no DESC
                LIMIT ?
                """, (rs, ignored) -> view(row(rs)), tenantId, organizationId,
                userId, userId, state, state, limit);
        return new WithdrawalPage(
                items, Instant.now().truncatedTo(ChronoUnit.MILLIS), null);
    }

    private WithdrawalRow requiredWithdrawal(
            long tenantId,
            long organizationId,
            String withdrawalNo,
            boolean lock) {
        WithdrawalRow row = findWithdrawal(
                tenantId, organizationId, withdrawalNo, lock);
        if (row == null) throw notFound();
        return row;
    }

    private WithdrawalRow requiredWithdrawalByNo(
            String withdrawalNo, boolean lock) {
        List<WithdrawalRow> rows = jdbc.query(withdrawalSql(
                        "w.withdrawal_order_no = ?", lock),
                (rs, ignored) -> row(rs), withdrawalNo);
        if (rows.isEmpty()) throw notFound();
        return rows.getFirst();
    }

    private WithdrawalRow findWithdrawal(
            long tenantId,
            long organizationId,
            String withdrawalNo,
            boolean lock) {
        List<WithdrawalRow> rows = jdbc.query(withdrawalSql("""
                        w.tenant_id = ? AND w.organization_id = ?
                        AND w.withdrawal_order_no = ?
                        """, lock),
                (rs, ignored) -> row(rs), tenantId, organizationId, withdrawalNo);
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private String withdrawalSql(String predicate, boolean lock) {
        return """
                SELECT w.*, t.channel_state, t.terminal_classification,
                       t.package_info,
                       m.scene_id, m.report_type, m.report_content,
                       m.transfer_page_style
                FROM fund_withdrawal_order w
                LEFT JOIN fund_wechat_transfer t
                  ON t.withdrawal_order_id = w.id
                JOIN fund_wechat_merchant_profile m
                  ON m.id = w.merchant_profile_id
                WHERE %s
                %s
                """.formatted(predicate, lock ? "FOR UPDATE" : "");
    }

    private WithdrawalRow row(java.sql.ResultSet rs)
            throws java.sql.SQLException {
        return new WithdrawalRow(
                rs.getLong("id"), rs.getString("withdrawal_order_no"),
                rs.getLong("tenant_id"), rs.getLong("organization_id"),
                rs.getLong("organization_user_id"), rs.getLong("wallet_id"),
                rs.getLong("organization_payout_account_id"),
                rs.getLong("amount_cent"), rs.getLong("withdraw_config_id"),
                rs.getLong("withdraw_config_version_no"),
                rs.getLong("miniapp_merchant_binding_id"),
                rs.getLong("organization_miniapp_id"),
                rs.getLong("merchant_profile_id"),
                rs.getString("mchid_snapshot"), rs.getString("appid_snapshot"),
                rs.getString("openid_snapshot"), rs.getString("business_state"),
                rs.getBoolean("negative_balance_pause"),
                rs.getBoolean("post_boundary_risk"),
                rs.getObject("channel_boundary_at", LocalDateTime.class),
                rs.getObject("long_unsettled_at", LocalDateTime.class),
                rs.getLong("lock_version"),
                rs.getObject("created_at", LocalDateTime.class),
                rs.getObject("reviewed_at", LocalDateTime.class),
                rs.getObject("ended_at", LocalDateTime.class),
                rs.getString("channel_state"), rs.getString("package_info"),
                rs.getString("scene_id"), rs.getString("report_type"),
                rs.getString("report_content"),
                rs.getString("transfer_page_style"));
    }

    private ConfigRow currentConfig(
            long tenantId, long organizationId, boolean lock) {
        ConfigHeadRow head = jdbc.queryForObject("""
                SELECT current_config_id, current_version_no
                FROM fund_organization_withdraw_config_head
                WHERE tenant_id = ? AND organization_id = ?
                """ + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> new ConfigHeadRow(
                        rs.getLong("current_config_id"),
                        rs.getLong("current_version_no")),
                tenantId, organizationId);
        return jdbc.queryForObject("""
                SELECT c.id, c.version_no, c.hard_limit_cent,
                       c.manual_min_cent, c.manual_max_cent,
                       c.published_at
                FROM fund_organization_withdraw_config c
                WHERE c.id = ? AND c.tenant_id = ?
                  AND c.organization_id = ? AND c.version_no = ?
                """,
                (rs, ignored) -> new ConfigRow(
                        rs.getLong("id"), rs.getLong("version_no"),
                        rs.getLong("hard_limit_cent"),
                        rs.getLong("manual_min_cent"),
                        rs.getLong("manual_max_cent"),
                        rs.getObject("published_at", LocalDateTime.class)),
                head.configId(), tenantId, organizationId, head.version());
    }

    private UserRow requiredUser(MiniappScope scope, boolean lock) {
        return jdbc.queryForObject("""
                SELECT id, openid, phone_e164, status
                FROM iam_organization_user
                WHERE tenant_id = ? AND organization_id = ? AND id = ?
                  AND organization_miniapp_id = ?
                """ + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> new UserRow(
                        rs.getLong("id"), rs.getString("openid"),
                        rs.getString("phone_e164"), rs.getString("status")),
                scope.tenantId(), scope.organizationId(),
                scope.organizationUserId(), scope.organizationMiniappId());
    }

    private BindingRow requiredBinding(
            long tenantId, long organizationId, boolean lock) {
        List<BindingRow> rows = jdbc.query("""
                SELECT b.id binding_id, b.organization_miniapp_id,
                       b.merchant_profile_id, b.appid, m.mchid
                FROM fund_miniapp_merchant_binding b
                JOIN fund_wechat_merchant_profile m
                  ON m.id = b.merchant_profile_id
                JOIN iam_organization_miniapp app
                  ON app.id = b.organization_miniapp_id
                 AND app.appid = b.appid
                WHERE b.tenant_id = ? AND b.organization_id = ?
                  AND b.status = 'VERIFIED' AND m.status = 'ENABLED'
                  AND app.login_enabled = 1
                """ + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> new BindingRow(
                        rs.getLong("binding_id"),
                        rs.getLong("organization_miniapp_id"),
                        rs.getLong("merchant_profile_id"),
                        rs.getString("appid"), rs.getString("mchid")),
                tenantId, organizationId);
        if (rows.isEmpty()) {
            throw new TargetApiException(
                    422, "FUNDS.MINIAPP_MERCHANT_BINDING_UNAVAILABLE",
                    "机构小程序尚未完成系统商户绑定核验");
        }
        return rows.getFirst();
    }

    private boolean lockAndVerifyCurrentBinding(WithdrawalRow row) {
        List<Long> rows = jdbc.query("""
                SELECT b.id
                FROM fund_miniapp_merchant_binding b
                JOIN fund_wechat_merchant_profile m
                  ON m.id = b.merchant_profile_id
                JOIN iam_organization_miniapp app
                  ON app.id = b.organization_miniapp_id
                 AND app.tenant_id = b.tenant_id
                 AND app.organization_id = b.organization_id
                 AND app.appid = b.appid
                WHERE b.id = ? AND b.tenant_id = ?
                  AND b.organization_id = ?
                  AND b.organization_miniapp_id = ?
                  AND b.merchant_profile_id = ?
                  AND b.appid = ? AND m.mchid = ?
                  AND b.status = 'VERIFIED'
                  AND m.status = 'ENABLED'
                  AND app.login_enabled = 1
                FOR UPDATE
                """, (rs, ignored) -> rs.getLong("id"), row.bindingId(),
                row.tenantId(), row.organizationId(), row.miniappId(),
                row.merchantId(), row.appid(), row.mchid());
        return rows.size() == 1;
    }

    private GateRow requiredGate(long merchantId, boolean lock) {
        List<GateStateRow> rows = jdbc.query("""
                SELECT merchant_profile_id, gate_state, lock_version,
                       current_pause_event_id, paused_at
                FROM fund_payout_gate
                WHERE merchant_profile_id = ?
                """ + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> new GateStateRow(
                        rs.getLong("merchant_profile_id"),
                        rs.getString("gate_state"),
                        rs.getLong("lock_version"),
                        rs.getObject("current_pause_event_id", Long.class),
                        rs.getObject("paused_at", LocalDateTime.class)),
                merchantId);
        if (rows.isEmpty()) {
            throw new TargetApiException(
                    422, "FUNDS.RECHARGE_CHANNEL_UNAVAILABLE",
                    "系统商户出款闸门尚未初始化");
        }
        return enrichGate(rows.getFirst());
    }

    private GateRow currentPlatformGate(boolean lock) {
        List<Long> rows = jdbc.query("""
                SELECT g.merchant_profile_id
                FROM fund_payout_gate g
                JOIN fund_wechat_merchant_profile m
                  ON m.id = g.merchant_profile_id
                 AND m.status = 'ENABLED'
                ORDER BY g.merchant_profile_id
                """, (rs, ignored) -> rs.getLong("merchant_profile_id"));
        if (rows.isEmpty()) {
            throw new TargetApiException(
                    404, "RESOURCE.NOT_FOUND", "平台出款闸门尚未初始化");
        }
        if (rows.size() != 1) {
            throw new TargetApiException(
                    409,
                    "FUNDS.PAYOUT_GATE_CONFIGURATION_CONFLICT",
                    "存在多个启用的系统商户，无法确定唯一出款闸门");
        }
        return requiredGate(rows.getFirst(), lock);
    }

    private PayoutGateRestoreReplay findPayoutGateRestore(
            UUID operationUid) {
        List<PayoutGateRestoreReplay> rows = jdbc.query("""
                SELECT e.restore_request_sha256, e.gate_version_after,
                       m.mchid
                FROM fund_payout_gate_event e
                JOIN fund_wechat_merchant_profile m
                  ON m.id = e.merchant_profile_id
                WHERE e.event_uid = ? AND e.event_type = 'RESTORED'
                """, (rs, ignored) -> new PayoutGateRestoreReplay(
                        rs.getBytes("restore_request_sha256"),
                        rs.getLong("gate_version_after"),
                        rs.getString("mchid")), operationUid.toString());
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private static PayoutGateView replayPayoutGateRestore(
            PayoutGateRestoreReplay replay, byte[] requestHash) {
        if (!java.security.MessageDigest.isEqual(
                replay.requestSha256(), requestHash)) {
            throw idempotencyConflict();
        }
        return new PayoutGateView(
                replay.mchid(), "OPEN", replay.gateVersionAfter(),
                null, null);
    }

    private GateRow enrichGate(GateStateRow state) {
        String mchid = jdbc.queryForObject("""
                SELECT mchid FROM fund_wechat_merchant_profile
                WHERE id = ?
                """, String.class, state.merchantId());
        String eventUid = state.currentPauseEventId() == null
                ? null
                : jdbc.queryForObject("""
                        SELECT event_uid FROM fund_payout_gate_event
                        WHERE id = ?
                        """, String.class, state.currentPauseEventId());
        return new GateRow(
                state.merchantId(), mchid, state.state(), state.version(),
                state.currentPauseEventId(),
                eventUid == null ? null : UUID.fromString(eventUid),
                state.pausedAt());
    }

    private static PayoutGateView payoutGateView(GateRow gate) {
        return new PayoutGateView(
                gate.mchid(), gate.state(), gate.version(),
                gate.currentPauseEventUid(), instant(gate.pausedAt()));
    }

    private WalletRow requiredWallet(
            long tenantId, long organizationId, long userId, boolean lock) {
        return jdbc.queryForObject("""
                SELECT id, available_balance_cent, frozen_withdrawal_cent,
                       last_entry_sequence_no, lock_version
                FROM fund_user_wallet
                WHERE tenant_id = ? AND organization_id = ?
                  AND organization_user_id = ?
                """ + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> new WalletRow(
                        rs.getLong("id"),
                        rs.getLong("available_balance_cent"),
                        rs.getLong("frozen_withdrawal_cent"),
                        rs.getLong("last_entry_sequence_no"),
                        rs.getLong("lock_version")),
                tenantId, organizationId, userId);
    }

    private WalletRow requiredWalletById(WithdrawalRow row, boolean lock) {
        return jdbc.queryForObject("""
                SELECT id, available_balance_cent, frozen_withdrawal_cent,
                       last_entry_sequence_no, lock_version
                FROM fund_user_wallet
                WHERE tenant_id = ? AND organization_id = ? AND id = ?
                """ + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> new WalletRow(
                        rs.getLong("id"),
                        rs.getLong("available_balance_cent"),
                        rs.getLong("frozen_withdrawal_cent"),
                        rs.getLong("last_entry_sequence_no"),
                        rs.getLong("lock_version")),
                row.tenantId(), row.organizationId(), row.walletId());
    }

    private AccountRow requiredAccount(
            long tenantId, long organizationId, boolean lock) {
        return jdbc.queryForObject("""
                SELECT id, available_payout_cent, frozen_withdrawal_cent,
                       lock_version
                FROM fund_organization_payout_account
                WHERE tenant_id = ? AND organization_id = ?
                """ + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> new AccountRow(
                        rs.getLong("id"), rs.getLong("available_payout_cent"),
                        rs.getLong("frozen_withdrawal_cent"),
                        rs.getLong("lock_version")),
                tenantId, organizationId);
    }

    private AccountRow requiredAccountById(WithdrawalRow row, boolean lock) {
        return jdbc.queryForObject("""
                SELECT id, available_payout_cent, frozen_withdrawal_cent,
                       lock_version
                FROM fund_organization_payout_account
                WHERE tenant_id = ? AND organization_id = ? AND id = ?
                """ + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> new AccountRow(
                        rs.getLong("id"), rs.getLong("available_payout_cent"),
                        rs.getLong("frozen_withdrawal_cent"),
                        rs.getLong("lock_version")),
                row.tenantId(), row.organizationId(), row.accountId());
    }

    private CounterRow lockCounter(long tenantId, long organizationId) {
        return jdbc.queryForObject("""
                SELECT last_visibility_sequence_no, lock_version
                FROM fund_organization_wallet_entry_counter
                WHERE tenant_id = ? AND organization_id = ?
                FOR UPDATE
                """, (rs, ignored) -> new CounterRow(
                        rs.getLong("last_visibility_sequence_no"),
                        rs.getLong("lock_version")), tenantId, organizationId);
    }

    private boolean activeWithdrawalExists(long walletId, boolean lock) {
        List<Long> rows = jdbc.query("""
                SELECT withdrawal_order_id FROM fund_active_withdrawal
                WHERE wallet_id = ?
                """ + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> rs.getLong("withdrawal_order_id"), walletId);
        return !rows.isEmpty();
    }

    private void lockActive(long walletId) {
        if (!activeWithdrawalExists(walletId, true)) {
            throw stateConflict("活动提现槽位已经释放");
        }
    }

    private boolean transferExists(long withdrawalId) {
        Integer count = jdbc.queryForObject(
                "SELECT COUNT(*) FROM fund_wechat_transfer WHERE withdrawal_order_id = ?",
                Integer.class, withdrawalId);
        return count != null && count > 0;
    }

    private TransferSnapshot transferSnapshot(String withdrawalNo) {
        return transactions.execute(status -> findTransfer(withdrawalNo, false));
    }

    private TransferSnapshot lockTransfer(String withdrawalNo) {
        return findTransfer(withdrawalNo, true);
    }

    private TransferSnapshot transferByOutBillNo(
            String outBillNo, boolean lock) {
        String withdrawalNo = jdbc.queryForObject("""
                SELECT w.withdrawal_order_no
                FROM fund_wechat_transfer t
                JOIN fund_withdrawal_order w ON w.id = t.withdrawal_order_id
                WHERE t.out_bill_no = ?
                """, String.class, outBillNo);
        return findTransfer(withdrawalNo, lock);
    }

    private TransferSnapshot findTransfer(String withdrawalNo, boolean lock) {
        return jdbc.queryForObject("""
                SELECT t.id transfer_id, t.out_bill_no, t.transfer_bill_no,
                       t.terminal_classification, t.last_api_error_code,
                       t.transfer_remark, t.notify_url_snapshot,
                       t.notify_url_sha256,
                       t.request_sha256,
                       w.*, t.channel_state transfer_channel_state,
                       t.package_info transfer_package_info,
                       t.scene_id_snapshot scene_id,
                       t.report_type_snapshot report_type,
                       t.report_content_snapshot report_content,
                       t.transfer_page_style_snapshot transfer_page_style
                FROM fund_withdrawal_order w
                JOIN fund_wechat_transfer t ON t.withdrawal_order_id = w.id
                WHERE w.withdrawal_order_no = ?
                """ + (lock ? " FOR UPDATE" : ""), (rs, ignored) -> {
                    WithdrawalRow order = new WithdrawalRow(
                            rs.getLong("id"), rs.getString("withdrawal_order_no"),
                            rs.getLong("tenant_id"), rs.getLong("organization_id"),
                            rs.getLong("organization_user_id"),
                            rs.getLong("wallet_id"),
                            rs.getLong("organization_payout_account_id"),
                            rs.getLong("amount_cent"),
                            rs.getLong("withdraw_config_id"),
                            rs.getLong("withdraw_config_version_no"),
                            rs.getLong("miniapp_merchant_binding_id"),
                            rs.getLong("organization_miniapp_id"),
                            rs.getLong("merchant_profile_id"),
                            rs.getString("mchid_snapshot"),
                            rs.getString("appid_snapshot"),
                            rs.getString("openid_snapshot"),
                            rs.getString("business_state"),
                            rs.getBoolean("negative_balance_pause"),
                            rs.getBoolean("post_boundary_risk"),
                            rs.getObject("channel_boundary_at", LocalDateTime.class),
                            rs.getObject("long_unsettled_at", LocalDateTime.class),
                            rs.getLong("lock_version"),
                            rs.getObject("created_at", LocalDateTime.class),
                            rs.getObject("reviewed_at", LocalDateTime.class),
                            rs.getObject("ended_at", LocalDateTime.class),
                            rs.getString("transfer_channel_state"),
                            rs.getString("transfer_package_info"),
                            rs.getString("scene_id"), rs.getString("report_type"),
                            rs.getString("report_content"),
                            rs.getString("transfer_page_style"));
                    return new TransferSnapshot(
                            rs.getLong("transfer_id"), rs.getString("out_bill_no"),
                            rs.getString("transfer_bill_no"),
                            rs.getString("terminal_classification"),
                            rs.getString("last_api_error_code"),
                            rs.getString("transfer_remark"),
                            rs.getString("notify_url_snapshot"),
                            rs.getBytes("notify_url_sha256"),
                            rs.getBytes("request_sha256"), order);
                }, withdrawalNo);
    }

    private void registerTask(
            WithdrawalRow row,
            String type,
            String key,
            LocalDateTime runAt) {
        String snapshot = "{\"withdrawalNo\":\""
                + row.withdrawalNo() + "\"}";
        tasks.register(new ReliableFundsTaskRegistration(
                row.tenantId(), row.organizationId(), type, key,
                "WITHDRAWAL_ORDER", row.withdrawalNo(), 1, snapshot,
                RechargeApplicationService.sha256(snapshot),
                "SUBMIT_MERCHANT_TRANSFER".equals(type) ? 500 : 20,
                runAt));
    }

    private long appendWebAudit(
            WebScope scope,
            UUID operationUid,
            String action,
            String target,
            String reason,
            String summary,
            LocalDateTime now) {
        try {
            return audit.append(new AuditEntry(
                    UUID.randomUUID(), UUID.randomUUID(), operationUid,
                    AuditScopeKind.ORGANIZATION,
                    scope.tenantId(), scope.organizationId(),
                    scope.platformActor()
                            ? AuditActorKind.PLATFORM_ADMIN
                            : AuditActorKind.STAFF_ACCOUNT,
                    scope.platformAdminId(), scope.staffAccountId(), null,
                    null, scope.actorDisplayName(), action,
                    "WITHDRAWAL", target, "WEB", "SUCCEEDED",
                    scope.sessionUid(), reason, summary, instant(now)));
        } catch (DuplicateKeyException duplicate) {
            throw idempotencyConflict();
        }
    }

    private long appendPlatformAudit(
            PlatformScope actor,
            UUID operationUid,
            String action,
            String target,
            String reason,
            String summary,
            LocalDateTime now) {
        try {
            return audit.append(new AuditEntry(
                    UUID.randomUUID(), UUID.randomUUID(), operationUid,
                    AuditScopeKind.PLATFORM, null, null,
                    AuditActorKind.PLATFORM_ADMIN,
                    actor.platformAdminId(), null, null, null,
                    actor.actorDisplayName(), action,
                    "PAYOUT_GATE", target, "WEB", "SUCCEEDED",
                    actor.sessionUid(), trimTo(reason, 500), summary,
                    instant(now)));
        } catch (DuplicateKeyException duplicate) {
            throw idempotencyConflict();
        }
    }

    private void appendMiniappAudit(
            MiniappScope scope,
            UUID operationUid,
            String action,
            String target,
            String summary,
            LocalDateTime now) {
        try {
            audit.append(new AuditEntry(
                    UUID.randomUUID(), UUID.randomUUID(), operationUid,
                    AuditScopeKind.ORGANIZATION,
                    scope.tenantId(), scope.organizationId(),
                    AuditActorKind.ORGANIZATION_USER,
                    null, null, scope.organizationUserId(), null,
                    scope.actorDisplayName(), action, "WITHDRAWAL", target,
                    "MINIAPP", "SUCCEEDED", scope.sessionUid(),
                    null, summary, instant(now)));
        } catch (DuplicateKeyException duplicate) {
            throw idempotencyConflict();
        }
    }

    private WebScope authorizedEither(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String first,
            String second,
            boolean writeRequiresStaff) {
        try {
            return access.webScope(
                    platformPath, tenantCode, organizationCode,
                    first, writeRequiresStaff);
        } catch (TargetApiException denied) {
            if (!"AUTH.CAPABILITY_REQUIRED".equals(denied.code())) throw denied;
            return access.webScope(
                    platformPath, tenantCode, organizationCode,
                    second, writeRequiresStaff);
        }
    }

    private WithdrawalView view(WithdrawalRow row) {
        return new WithdrawalView(
                row.withdrawalNo(), row.state(), row.version(),
                money(row.amountCent()), row.channelState(),
                "WAIT_USER_CONFIRM".equals(row.channelState())
                        && row.packageInfo() != null,
                false,
                row.channelBoundaryAt() != null,
                row.negativePause(), row.postBoundaryRisk(),
                instant(row.createdAt()), instant(row.reviewedAt()),
                instant(row.endedAt()));
    }

    private MerchantTransferRequest transferRequest(
            TransferSnapshot transfer) {
        WithdrawalRow row = transfer.withdrawal();
        String notifyUrl = transfer.notifyUrl() == null
                ? notifyBaseUrl
                + "/api/v1/wechat-pay/notifications/merchant-transfers"
                : transfer.notifyUrl();
        return new MerchantTransferRequest(
                row.mchid(), row.appid(), transfer.outBillNo(),
                row.openid(), row.amountCent(), row.sceneId(),
                row.reportType(), row.reportContent(), transfer.remark(),
                row.pageStyle(), notifyUrl);
    }

    private MerchantTransferRequest originalTransferRequest(
            TransferSnapshot transfer) {
        MerchantTransferRequest request = transferRequest(transfer);
        if (!java.security.MessageDigest.isEqual(
                transfer.notifyUrlSha256(),
                RechargeApplicationService.sha256(request.notifyUrl()))) {
            return null;
        }
        if (!java.security.MessageDigest.isEqual(
                transfer.requestSha256(),
                transferRequestHash(request))) {
            return null;
        }
        return request;
    }

    private static byte[] transferRequestHash(
            MerchantTransferRequest request) {
        String canonical = "TRANSFER_REQUEST_V2|"
                + requestField(request.mchid())
                + requestField(request.appid())
                + requestField(request.outBillNo())
                + requestField(request.openid())
                + request.amountCent() + "|"
                + requestField(request.sceneId())
                + requestField(request.reportType())
                + requestField(request.reportContent())
                + requestField(request.remark())
                + requestField(request.transferPageStyle())
                + java.util.HexFormat.of().formatHex(
                RechargeApplicationService.sha256(request.notifyUrl()));
        return RechargeApplicationService.sha256(canonical);
    }

    private static String requestField(String value) {
        return value.getBytes(StandardCharsets.UTF_8).length
                + ":" + value + "|";
    }

    private static boolean shouldResubmitOriginal(
            TransferSnapshot transfer) {
        return "NOT_FOUND".equals(transfer.lastApiErrorCode())
                || "ACCEPTED".equals(transfer.withdrawal().channelState());
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
    }

    private long requiredLong(String sql, Object... args) {
        Long value = jdbc.queryForObject(sql, Long.class, args);
        if (value == null) throw new IllegalStateException("required key missing");
        return value;
    }

    private static void requireExpectedVersion(
            WithdrawalRow row, VersionedWithdrawalRequest request) {
        if (request == null || request.expectedVersion() == null) {
            throw validation("expectedVersion 不能为空");
        }
        if (row.version() != request.expectedVersion()) {
            throw new TargetApiException(
                    409, "WITHDRAWAL.VERSION_CONFLICT",
                    "提现单已被其他操作更新");
        }
    }

    private static String normalizeDecision(String value) {
        String result = value == null ? "" : value.trim().toUpperCase(Locale.ROOT);
        if (!List.of("APPROVED", "REJECTED").contains(result)) {
            throw validation("decision 只允许 APPROVED 或 REJECTED");
        }
        return result;
    }

    private static String normalizeStatus(String value) {
        if (value == null || value.isBlank()) return null;
        String result = value.trim().toUpperCase(Locale.ROOT);
        if (!List.of(
                "PENDING_REVIEW", "READY_TO_SUBMIT", "CHANNEL_PROCESSING",
                "SUCCEEDED", "REJECTED", "LOCAL_CANCELLED",
                "LOCAL_ABORTED_BEFORE_CHANNEL", "CHANNEL_FAILED",
                "CHANNEL_CANCELLED").contains(result)) {
            throw validation("不支持的提现状态");
        }
        return result;
    }

    private static String classification(
            MerchantTransferResult.Outcome outcome) {
        return switch (outcome) {
            case SUCCESS -> "SUCCESS";
            case FAIL -> "FAIL";
            case CANCELLED -> "CANCELLED";
            default -> null;
        };
    }

    private static int normalizeLimit(Integer value) {
        int limit = value == null ? 20 : value;
        if (limit < 1 || limit > 100) {
            throw validation("limit 必须在 1 到 100 之间");
        }
        return limit;
    }

    private static void requireUuidV4(UUID value) {
        if (value == null || value.version() != 4) {
            throw validation("Idempotency-Key 必须是 UUIDv4");
        }
    }

    private static String money(long cent) {
        return RechargeApplicationService.money(cent);
    }

    private static String stripTrailingSlash(String value) {
        if (value == null) return "";
        String result = value.trim();
        while (result.endsWith("/")) {
            result = result.substring(0, result.length() - 1);
        }
        return result;
    }

    private static String safe(String value) {
        return value == null ? "" : value;
    }

    private static byte[] payoutRestoreRequestHash(
            RestorePayoutGateRequest request, String normalizedReason) {
        return RechargeApplicationService.sha256(
                "PAYOUT_GATE_RESTORE|"
                        + request.expectedGateVersion() + "|"
                        + request.pausedEventUid() + "|"
                        + request.fundsReplenishedConfirmed() + "|"
                        + safe(normalizedReason));
    }

    private static String requiredText(JsonNode node, String field) {
        String value = text(node, field);
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(
                    "trusted transfer notification lacks " + field);
        }
        return value;
    }

    private static String text(JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        return value == null || value.isNull() ? null : value.asText();
    }

    private static long requiredLong(JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        if (value == null || !value.canConvertToLong()) {
            throw new IllegalArgumentException(
                    "trusted transfer notification lacks " + field);
        }
        return value.asLong();
    }

    private static Instant parseInstant(String value) {
        return java.time.OffsetDateTime.parse(value).toInstant();
    }

    private static String trimTo(String value, int length) {
        if (value == null || value.isBlank()) return null;
        String result = value.trim();
        return result.length() <= length ? result : result.substring(0, length);
    }

    private static Instant instant(LocalDateTime value) {
        return value == null ? null : value.toInstant(ZoneOffset.UTC);
    }

    private static LocalDateTime databaseTime(Instant value) {
        return value == null ? null : LocalDateTime.ofInstant(value, ZoneOffset.UTC);
    }

    private static TargetApiException validation(String message) {
        return new TargetApiException(
                400, "COMMON.VALIDATION_FAILED", message);
    }

    private static TargetApiException idempotencyConflict() {
        return new TargetApiException(
                409, "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                "该 Idempotency-Key 已用于不同的提现操作");
    }

    private static TargetApiException stateConflict(String message) {
        return new TargetApiException(
                409, "WITHDRAWAL.STATE_CONFLICT", message);
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404, "RESOURCE.NOT_FOUND", "提现单不存在");
    }

    private record ConfigRow(
            long id, long version, long hardLimitCent,
            long minimumCent, long maximumCent,
            LocalDateTime publishedAt) {

        WithdrawalConfigurationView view() {
            return new WithdrawalConfigurationView(
                    version, money(hardLimitCent), money(minimumCent),
                    money(maximumCent), "0.00", instant(publishedAt));
        }
    }

    private record ConfigHeadRow(long configId, long version) {
    }

    private record UserRow(
            long id, String openid, String phoneE164, String status) {
    }

    private record BindingRow(
            long bindingId, long miniappId, long merchantId,
            String appid, String mchid) {
    }

    private record GateRow(
            long merchantId, String mchid, String state, long version,
            Long currentPauseEventId, UUID currentPauseEventUid,
            LocalDateTime pausedAt) {
    }

    private record GateStateRow(
            long merchantId, String state, long version,
            Long currentPauseEventId, LocalDateTime pausedAt) {
    }

    private record TransferPreparation(
            TransferSnapshot transfer,
            UUID gateWaitEventUid,
            boolean newlyCreated) {

        static TransferPreparation created(TransferSnapshot transfer) {
            return new TransferPreparation(transfer, null, true);
        }

        static TransferPreparation existing(TransferSnapshot transfer) {
            return new TransferPreparation(transfer, null, false);
        }

        static TransferPreparation waiting(UUID gateWaitEventUid) {
            return new TransferPreparation(null, gateWaitEventUid, false);
        }

        static TransferPreparation localWait() {
            return new TransferPreparation(null, null, false);
        }
    }

    private record PayoutGateRestoreReplay(
            byte[] requestSha256,
            long gateVersionAfter,
            String mchid) {
    }

    private record WalletRow(
            long id, long availableCent, long frozenCent,
            long lastSequence, long version) {
    }

    private record AccountRow(
            long id, long availableCent, long frozenCent, long version) {
    }

    private record CounterRow(long lastVisibilitySequence, long version) {
    }

    private record WithdrawalRow(
            long id, String withdrawalNo, long tenantId, long organizationId,
            long userId, long walletId, long accountId, long amountCent,
            long configId, long configVersion, long bindingId, long miniappId,
            long merchantId, String mchid, String appid, String openid,
            String state, boolean negativePause, boolean postBoundaryRisk,
            LocalDateTime channelBoundaryAt, LocalDateTime longUnsettledAt,
            long version,
            LocalDateTime createdAt, LocalDateTime reviewedAt,
            LocalDateTime endedAt, String channelState, String packageInfo,
            String sceneId, String reportType, String reportContent,
            String pageStyle) {
    }

    private record TransferSnapshot(
            long transferId, String outBillNo, String transferBillNo,
            String terminalClassification, String lastApiErrorCode,
            String remark, String notifyUrl, byte[] notifyUrlSha256,
            byte[] requestSha256,
            WithdrawalRow withdrawal) {

        private TransferSnapshot {
            notifyUrlSha256 = notifyUrlSha256.clone();
            requestSha256 = requestSha256.clone();
        }

        @Override
        public byte[] notifyUrlSha256() {
            return notifyUrlSha256.clone();
        }

        @Override
        public byte[] requestSha256() {
            return requestSha256.clone();
        }

        String withdrawalNo() { return withdrawal.withdrawalNo(); }
        long tenantId() { return withdrawal.tenantId(); }
        long organizationId() { return withdrawal.organizationId(); }
        long merchantId() { return withdrawal.merchantId(); }
        long amountCent() { return withdrawal.amountCent(); }
        String mchid() { return withdrawal.mchid(); }
        String appid() { return withdrawal.appid(); }
        String openid() { return withdrawal.openid(); }

    }
}
