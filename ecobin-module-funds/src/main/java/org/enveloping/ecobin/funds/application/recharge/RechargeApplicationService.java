package org.enveloping.ecobin.funds.application.recharge;

import tools.jackson.databind.JsonNode;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.funds.api.port.NativePaymentChannelPort;
import org.enveloping.ecobin.funds.api.port.FundsOperationalControlPort;
import org.enveloping.ecobin.funds.api.port.FundsOperationalControlPort.ReconciliationIssue;
import org.enveloping.ecobin.funds.api.port.NativePaymentChannelPort.NativePaymentRequest;
import org.enveloping.ecobin.funds.api.port.NativePaymentChannelPort.NativePaymentResult;
import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskExecutorPort;
import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskExecutorPort.Command;
import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskExecutorPort.Result;
import org.enveloping.ecobin.funds.api.port.ReliableFundsAttemptBoundaryPort;
import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskRegistrationPort;
import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskRegistrationPort.ReliableFundsTaskRegistration;
import org.enveloping.ecobin.funds.application.access.FundsAccessService;
import org.enveloping.ecobin.funds.application.access.FundsAccessService.WebScope;
import org.enveloping.ecobin.funds.application.channel.WechatChannelEvidencePolicy;
import org.enveloping.ecobin.funds.application.channel.WechatChannelEvidencePolicy.Validation;
import org.enveloping.ecobin.funds.application.pagination.FundsListCursorCodec;
import org.enveloping.ecobin.funds.web.v1.FundsModels.PayoutAccountView;
import org.enveloping.ecobin.funds.web.v1.FundsModels.PayoutEntryPage;
import org.enveloping.ecobin.funds.web.v1.FundsModels.PayoutEntryView;
import org.enveloping.ecobin.funds.web.v1.FundsModels.RechargePage;
import org.enveloping.ecobin.funds.web.v1.FundsModels.RechargeView;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.time.Duration;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.HexFormat;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.UUID;

@Service
public class RechargeApplicationService {

    private static final long MIN_GROSS_CENT = 100;
    private static final long MAX_GROSS_CENT = 20_000_000;
    private static final int FEE_RATE_PPM = 6_000;

    private final JdbcTemplate jdbc;
    private final FundsAccessService access;
    private final ReliableFundsTaskRegistrationPort tasks;
    private final NativePaymentChannelPort channel;
    private final FundsOperationalControlPort operationalControl;
    private final ReliableFundsAttemptBoundaryPort attemptBoundary;
    private final TransactionTemplate transactions;
    private final FundsListCursorCodec cursorCodec;
    private final String notifyBaseUrl;

    public RechargeApplicationService(
            JdbcTemplate jdbc,
            FundsAccessService access,
            ReliableFundsTaskRegistrationPort tasks,
            NativePaymentChannelPort channel,
            FundsOperationalControlPort operationalControl,
            ReliableFundsAttemptBoundaryPort attemptBoundary,
            TransactionTemplate transactions,
            FundsListCursorCodec cursorCodec,
            @Value("${ecobin.funds.wechat-pay.notify-base-url:https://fake.invalid}")
            String notifyBaseUrl) {
        this.jdbc = jdbc;
        this.access = access;
        this.tasks = tasks;
        this.channel = channel;
        this.operationalControl = operationalControl;
        this.attemptBoundary = attemptBoundary;
        this.transactions = transactions;
        this.cursorCodec = cursorCodec;
        this.notifyBaseUrl = stripTrailingSlash(notifyBaseUrl);
    }

    @Transactional
    public RechargeView create(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            UUID idempotencyKey,
            String grossAmountYuan,
            String statusBase) {
        requireUuidV4(idempotencyKey);
        WebScope scope = access.webScope(
                platformPath, tenantCode, organizationCode,
                "recharge.create", true);
        long gross = parseCent(grossAmountYuan, "grossAmountYuan");
        if (gross < MIN_GROSS_CENT || gross > MAX_GROSS_CENT) {
            throw new TargetApiException(
                    422,
                    "FUNDS.RECHARGE_AMOUNT_OUT_OF_RANGE",
                    "充值金额必须在 1.00 元到 200000.00 元之间");
        }
        String rechargeNo = stableNo("RC", idempotencyKey);
        RechargeRow existing = findRecharge(scope, rechargeNo);
        if (existing != null) {
            if (existing.grossCent() != gross) {
                throw idempotencyConflict();
            }
            return view(existing, statusBase);
        }
        BindingRow binding = requiredBinding(
                scope.tenantId(), scope.organizationId(), true);
        long fee = Math.floorDiv(
                Math.addExact(Math.multiplyExact(gross, FEE_RATE_PPM),
                        999_999L),
                1_000_000L);
        long net = gross - fee;
        LocalDateTime now = databaseNow();
        LocalDateTime expires = now.plusMinutes(30);
        String outTradeNo = stableNo("NP", idempotencyKey);
        jdbc.update("""
                INSERT INTO fund_recharge_order (
                    recharge_order_no, tenant_id, organization_id,
                    created_by_staff_account_id, gross_amount_cent,
                    fee_rate_ppm, fee_rounding_mode, fee_amount_cent,
                    net_amount_cent, business_state, expires_at,
                    paid_at, posted_at, closed_at, lock_version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 6000, 'CEILING_TO_CENT', ?, ?,
                          'PENDING_PAYMENT', ?, NULL, NULL, NULL, 0, ?, ?)
                """, rechargeNo, scope.tenantId(), scope.organizationId(),
                scope.staffAccountId(), gross, fee, net, expires, now, now);
        long rechargeId = requiredLong(
                "SELECT id FROM fund_recharge_order WHERE recharge_order_no = ?",
                rechargeNo);
        String notifyUrl = notifyBaseUrl
                + "/api/v1/wechat-pay/notifications/native-payments";
        String requestSnapshot = nativeSnapshot(
                binding, outTradeNo, gross, expires, notifyUrl);
        jdbc.update("""
                INSERT INTO fund_wechat_payment (
                    payment_uid, tenant_id, organization_id,
                    recharge_order_id, merchant_profile_id,
                    miniapp_merchant_binding_id, miniapp_channel_id,
                    mchid_snapshot, appid_snapshot, out_trade_no,
                    request_amount_cent, currency, description, time_expire,
                    notify_url_snapshot, notify_url_sha256, request_sha256,
                    code_url,
                    transaction_id, channel_state, last_api_error_code,
                    channel_updated_at, lock_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CNY', ?, ?, ?,
                          ?, ?, NULL, NULL, NULL, NULL, NULL, 0, ?, ?)
                """, UUID.randomUUID().toString(),
                scope.tenantId(), scope.organizationId(), rechargeId,
                binding.merchantProfileId(), binding.bindingId(),
                 binding.miniappId(), binding.mchid(), binding.appid(),
                 outTradeNo, gross, "EcoBin机构充值", expires,
                 notifyUrl, sha256(notifyUrl), nativeRequestDigest(
                         binding.mchid(), binding.appid(), outTradeNo,
                         gross, "CNY", "EcoBin机构充值", expires,
                         sha256(notifyUrl)), now, now);
        registerTask(
                scope.tenantId(), scope.organizationId(),
                "CREATE_NATIVE_PAYMENT", "CREATE_NATIVE_PAYMENT:" + rechargeNo,
                rechargeNo, requestSnapshot, null);
        return view(requiredRecharge(scope, rechargeNo), statusBase);
    }

    @Transactional(readOnly = true)
    public RechargeView detail(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String rechargeNo,
            String statusBase) {
        WebScope scope = access.webScope(
                platformPath, tenantCode, organizationCode,
                "fund.read", false);
        return view(requiredRecharge(scope, rechargeNo), statusBase);
    }

    @Transactional(readOnly = true)
    public RechargePage list(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String status,
            String cursor,
            Integer limit,
            String statusBase) {
        WebScope scope = access.webScope(
                platformPath, tenantCode, organizationCode,
                "fund.read", false);
        int pageSize = normalizeLimit(limit);
        String normalized = normalizeStatus(status);
        String fingerprint = FundsListCursorCodec.fingerprint(
                "RECHARGE", scope.tenantId(), scope.organizationId(),
                normalized, pageSize);
        FundsListCursorCodec.Decoded decoded =
                cursor == null || cursor.isBlank()
                        ? null
                        : cursorCodec.decode(
                                cursor, "RECHARGE", fingerprint);
        long highWatermark = decoded == null
                ? jdbc.queryForObject("""
                        SELECT COALESCE(MAX(id), 0)
                        FROM fund_recharge_order
                        WHERE tenant_id = ? AND organization_id = ?
                        """, Long.class, scope.tenantId(),
                        scope.organizationId())
                : decoded.highWatermark();
        Instant asOf = decoded == null
                ? instant(databaseNow()) : decoded.asOf();
        LocalDateTime anchor = decoded == null
                ? null : decoded.lastOccurredAt();
        String anchorNo = decoded == null
                ? null : decoded.lastStableKey();
        List<RechargeRow> rows = jdbc.query("""
                SELECT r.*, p.code_url, p.channel_state,
                       p.last_api_error_code, p.payment_uid
                FROM fund_recharge_order r
                JOIN fund_wechat_payment p ON p.recharge_order_id = r.id
                WHERE r.tenant_id = ? AND r.organization_id = ?
                  AND (? IS NULL OR r.business_state = ?)
                  AND r.id <= ?
                  AND (? IS NULL OR r.created_at < ?
                       OR (r.created_at = ?
                           AND r.recharge_order_no < ?))
                ORDER BY r.created_at DESC, r.recharge_order_no DESC
                LIMIT ?
                """, (rs, ignored) -> row(rs), scope.tenantId(),
                scope.organizationId(), normalized, normalized,
                highWatermark, anchor, anchor, anchor, anchorNo,
                pageSize + 1);
        boolean hasMore = rows.size() > pageSize;
        List<RechargeRow> included = hasMore
                ? rows.subList(0, pageSize) : rows;
        String nextCursor = null;
        if (hasMore) {
            RechargeRow last = included.getLast();
            nextCursor = cursorCodec.encode(
                    "RECHARGE", fingerprint, asOf, highWatermark,
                    last.createdAt(), last.rechargeNo(), last.id());
        }
        return new RechargePage(
                included.stream()
                        .map(row -> view(row, statusBase)).toList(),
                asOf, nextCursor);
    }

    @Transactional(readOnly = true)
    public PayoutAccountView payoutAccount(
            boolean platformPath,
            String tenantCode,
            String organizationCode) {
        WebScope scope = access.webScope(
                platformPath, tenantCode, organizationCode,
                "fund.read", false);
        return jdbc.queryForObject("""
                SELECT a.available_payout_cent, a.frozen_withdrawal_cent,
                       a.lock_version,
                       COALESCE(SUM(CASE WHEN e.event_type = 'RECHARGE_POSTED'
                           THEN e.recharge_gross_cent ELSE 0 END), 0) gross,
                       COALESCE(SUM(CASE WHEN e.event_type = 'RECHARGE_POSTED'
                           THEN e.recharge_fee_cent ELSE 0 END), 0) fee,
                       COALESCE(SUM(CASE WHEN e.event_type = 'RECHARGE_POSTED'
                           THEN e.recharge_net_cent ELSE 0 END), 0) net,
                       COALESCE(-SUM(CASE WHEN e.event_type = 'WITHDRAWAL_SUCCEEDED'
                           THEN e.frozen_delta_cent ELSE 0 END), 0) withdrawn,
                       COALESCE(MAX(b.status), 'UNAVAILABLE') binding_status,
                       COALESCE(MAX(g.gate_state), 'UNAVAILABLE') gate_state
                FROM fund_organization_payout_account a
                LEFT JOIN fund_organization_payout_entry e
                  ON e.payout_account_id = a.id
                LEFT JOIN fund_miniapp_merchant_binding b
                  ON b.tenant_id = a.tenant_id
                 AND b.organization_id = a.organization_id
                LEFT JOIN fund_payout_gate g
                  ON g.merchant_profile_id = b.merchant_profile_id
                WHERE a.tenant_id = ? AND a.organization_id = ?
                GROUP BY a.id, a.available_payout_cent,
                         a.frozen_withdrawal_cent, a.lock_version
                """, (rs, ignored) -> {
                    long available = rs.getLong("available_payout_cent");
                    long frozen = rs.getLong("frozen_withdrawal_cent");
                    return new PayoutAccountView(
                            money(available), money(frozen),
                            money(Math.addExact(available, frozen)),
                            money(rs.getLong("gross")), money(rs.getLong("fee")),
                            money(rs.getLong("net")), money(rs.getLong("withdrawn")),
                            rs.getString("binding_status"), rs.getString("gate_state"),
                            rs.getLong("lock_version"),
                            Instant.now().truncatedTo(ChronoUnit.MILLIS));
                }, scope.tenantId(), scope.organizationId());
    }

    @Transactional(readOnly = true)
    public PayoutEntryPage payoutEntries(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String entryType,
            String sourceNo,
            String cursor,
            Integer limit) {
        WebScope scope = access.webScope(
                platformPath, tenantCode, organizationCode,
                "fund.read", false);
        int pageSize = normalizeLimit(limit);
        String normalizedEntryType = blankToNull(entryType);
        String normalizedSourceNo = blankToNull(sourceNo);
        String fingerprint = FundsListCursorCodec.fingerprint(
                "PAYOUT_ENTRY", scope.tenantId(), scope.organizationId(),
                normalizedEntryType, normalizedSourceNo, pageSize);
        FundsListCursorCodec.Decoded decoded =
                cursor == null || cursor.isBlank()
                        ? null
                        : cursorCodec.decode(
                                cursor, "PAYOUT_ENTRY", fingerprint);
        long highWatermark = decoded == null
                ? jdbc.queryForObject("""
                        SELECT COALESCE(MAX(id), 0)
                        FROM fund_organization_payout_entry
                        WHERE tenant_id = ? AND organization_id = ?
                        """, Long.class, scope.tenantId(),
                        scope.organizationId())
                : decoded.highWatermark();
        Instant asOf = decoded == null
                ? instant(databaseNow()) : decoded.asOf();
        LocalDateTime anchor = decoded == null
                ? null : decoded.lastOccurredAt();
        Long anchorId = decoded == null ? null : decoded.lastId();
        List<PayoutEntryRow> rows = jdbc.query("""
                SELECT e.id, e.entry_uid, e.event_type,
                       e.available_delta_cent,
                       e.available_after_cent, e.frozen_delta_cent,
                       e.frozen_after_cent, e.occurred_at,
                       COALESCE(r.recharge_order_no,
                                w.withdrawal_order_no) source_no
                FROM fund_organization_payout_entry e
                LEFT JOIN fund_recharge_order r ON r.id = e.recharge_order_id
                LEFT JOIN fund_withdrawal_order w ON w.id = e.withdrawal_order_id
                WHERE e.tenant_id = ? AND e.organization_id = ?
                  AND (? IS NULL OR e.event_type = ?)
                  AND (? IS NULL OR COALESCE(r.recharge_order_no,
                                             w.withdrawal_order_no) = ?)
                  AND e.id <= ?
                  AND (? IS NULL OR e.occurred_at < ?
                       OR (e.occurred_at = ? AND e.id < ?))
                ORDER BY e.occurred_at DESC, e.id DESC
                LIMIT ?
                """, (rs, ignored) -> {
                    LocalDateTime occurredAt = rs.getObject(
                            "occurred_at", LocalDateTime.class);
                    return new PayoutEntryRow(
                            rs.getLong("id"), rs.getString("entry_uid"),
                            occurredAt, new PayoutEntryView(
                            rs.getString("entry_uid"),
                            rs.getString("event_type"),
                            money(rs.getLong("available_delta_cent")),
                            money(rs.getLong("frozen_delta_cent")),
                            money(rs.getLong("available_after_cent")),
                            money(rs.getLong("frozen_after_cent")),
                            rs.getString("source_no"), instant(occurredAt)));
                },
                scope.tenantId(), scope.organizationId(),
                normalizedEntryType, normalizedEntryType,
                normalizedSourceNo, normalizedSourceNo,
                highWatermark, anchor, anchor, anchor, anchorId,
                pageSize + 1);
        boolean hasMore = rows.size() > pageSize;
        List<PayoutEntryRow> included = hasMore
                ? rows.subList(0, pageSize) : rows;
        String nextCursor = null;
        if (hasMore) {
            PayoutEntryRow last = included.getLast();
            nextCursor = cursorCodec.encode(
                    "PAYOUT_ENTRY", fingerprint, asOf, highWatermark,
                    last.occurredAt(), last.entryUid(), last.id());
        }
        return new PayoutEntryPage(
                included.stream().map(PayoutEntryRow::view).toList(),
                asOf, nextCursor);
    }

    public Result executeTask(Command command) {
        return switch (command.taskType()) {
            case "CREATE_NATIVE_PAYMENT" -> createNative(command);
            case "QUERY_NATIVE_PAYMENT" -> queryNative(command);
            case "CLOSE_NATIVE_PAYMENT" -> closeNative(command);
            case "POST_RECHARGE_NET_AMOUNT" -> postRecharge(command);
            default -> new Result(
                    Result.Outcome.BLOCKED,
                    "unsupported funds task type");
        };
    }

    public boolean applyTrustedNotification(
            long sourceInboxId,
            long sourceTaskAttemptId,
            long trustedTenantId,
            long trustedOrganizationId,
            JsonNode payload) {
        PaymentSnapshot current = lockPaymentByOutTradeNo(
                requiredText(payload, "out_trade_no"));
        if (current.tenantId() != trustedTenantId
                || current.organizationId() != trustedOrganizationId) {
            throw new IllegalArgumentException(
                    "trusted payment notification scope mismatch");
        }
        String mchid = requiredText(payload, "mchid");
        String appid = requiredText(payload, "appid");
        String state = requiredText(payload, "trade_state");
        String transactionId = requiredText(payload, "transaction_id");
        long total = requiredLong(payload.path("amount"), "total");
        long payerTotal = requiredLong(payload.path("amount"), "payer_total");
        String payerOpenid = requiredText(payload.path("payer"), "openid");
        if (!current.mchid().equals(mchid)
                || !current.appid().equals(appid)
                || current.amountCent() != total
                || !"SUCCESS".equals(state)) {
            throw new IllegalArgumentException(
                    "trusted payment notification does not match the original order");
        }
        Integer prior = jdbc.queryForObject("""
                SELECT COUNT(*) FROM fund_wechat_payment_observation
                WHERE source_inbox_id = ?
                """, Integer.class, sourceInboxId);
        if (prior != null && prior > 0) return false;
        LocalDateTime now = databaseNow();
        jdbc.update("""
                INSERT INTO fund_wechat_payment_observation (
                    observation_uid, tenant_id, organization_id, payment_id,
                    observation_type, evidence_source_kind, source_scope_kind,
                    source_inbox_id, source_task_attempt_id,
                    raw_channel_state, api_error_code, transaction_id,
                    total_amount_cent, payer_total_cent, payer_openid,
                    channel_occurred_at, content_sha256, observed_at, created_at
                ) VALUES (?, ?, ?, ?, 'CALLBACK', 'INBOX', 'ORGANIZATION',
                          ?, NULL, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
                """, UUID.randomUUID().toString(), trustedTenantId,
                trustedOrganizationId, current.paymentId(), sourceInboxId,
                state, transactionId, total, payerTotal, payerOpenid,
                databaseTime(parseInstant(requiredText(payload, "success_time"))),
                sha256(payload.toString()), now, now);
        String existingTransaction = jdbc.queryForObject("""
                SELECT transaction_id FROM fund_wechat_payment WHERE id = ?
                FOR UPDATE
                """, String.class, current.paymentId());
        if (existingTransaction != null
                && !existingTransaction.equals(transactionId)) {
            throw new IllegalArgumentException(
                    "payment transaction id conflicts with trusted evidence");
        }
        jdbc.update("""
                UPDATE fund_wechat_payment
                SET transaction_id = COALESCE(transaction_id, ?),
                    channel_state = 'SUCCESS', last_api_error_code = NULL,
                    channel_updated_at = ?, lock_version = lock_version + 1,
                    updated_at = ? WHERE id = ?
                """, transactionId, now, now, current.paymentId());
        int advanced = jdbc.update("""
                UPDATE fund_recharge_order
                SET business_state = 'PAID_PENDING_POST', paid_at = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE id = ? AND business_state = 'PENDING_PAYMENT'
                """, now, now, current.rechargeId());
        if (advanced == 1) {
            registerTask(
                    current.tenantId(), current.organizationId(),
                    "POST_RECHARGE_NET_AMOUNT",
                    "POST_RECHARGE_NET_AMOUNT:" + current.rechargeNo(),
                    current.rechargeNo(),
                    "{\"rechargeNo\":\"" + current.rechargeNo() + "\"}",
                    now);
        } else if (List.of("CLOSED", "EXPIRED")
                .contains(current.businessState())) {
            String evidence = "CALLBACK_TERMINAL_CONFLICT|"
                    + current.outTradeNo() + "|"
                    + current.businessState() + "|SUCCESS|"
                    + transactionId;
            operationalControl.observeReconciliationIssue(
                    new ReconciliationIssue(
                            current.tenantId(), current.organizationId(),
                            sourceTaskAttemptId,
                            "FUNDS.NATIVE_PAYMENT_TERMINAL_CONFLICT",
                            "CRITICAL", "WECHAT_PAYMENT",
                            current.outTradeNo(), sha256(evidence),
                             "localState=" + current.businessState()
                                     + "; trustedChannelState=SUCCESS",
                             now));
            return true;
        }
        return advanced == 1;
    }

    private Result createNative(Command command) {
        PaymentSnapshot payment = paymentSnapshot(command.targetStableKey());
        Result invalid = validateOriginalNativeRequest(command, payment);
        if (invalid != null) {
            return invalid;
        }
        attemptBoundary.markExternalCallMayHaveStarted(command.attemptUid());
        NativePaymentResult response = channel.create(
                new NativePaymentRequest(
                        payment.mchid(), payment.appid(), payment.outTradeNo(),
                        payment.amountCent(), payment.description(),
                        instant(payment.expiresAt()), payment.notifyUrl()));
        return transactions.execute(status -> mergePaymentResult(
                command, payment, "CREATE_RESPONSE", response,
                PaymentAttemptKind.CREATE));
    }

    private Result queryNative(Command command) {
        PaymentSnapshot payment = paymentSnapshot(command.targetStableKey());
        if (!"PENDING_PAYMENT".equals(payment.businessState())) {
            return new Result(Result.Outcome.DONE,
                    "recharge is already terminal or paid");
        }
        attemptBoundary.markExternalCallMayHaveStarted(command.attemptUid());
        NativePaymentResult response = channel.query(
                new NativePaymentChannelPort.NativePaymentQuery(
                        payment.mchid(), payment.outTradeNo()));
        return transactions.execute(status -> mergePaymentResult(
                command, payment, "QUERY", response,
                PaymentAttemptKind.QUERY));
    }

    private Result closeNative(Command command) {
        PaymentSnapshot payment = paymentSnapshot(command.targetStableKey());
        if (!"PENDING_PAYMENT".equals(payment.businessState())) {
            return new Result(Result.Outcome.DONE,
                    "recharge is already terminal or paid");
        }
        attemptBoundary.markExternalCallMayHaveStarted(command.attemptUid());
        NativePaymentResult response = channel.close(
                new NativePaymentChannelPort.NativePaymentQuery(
                        payment.mchid(), payment.outTradeNo()));
        return transactions.execute(status -> mergePaymentResult(
                command, payment, "CLOSE_RESPONSE", response,
                PaymentAttemptKind.CLOSE));
    }

    private Result mergePaymentResult(
            Command command,
            PaymentSnapshot snapshot,
            String observationType,
            NativePaymentResult result,
            PaymentAttemptKind attemptKind) {
        PaymentSnapshot current = lockPayment(snapshot.rechargeNo());
        LocalDateTime now = databaseNow();
        jdbc.update("""
                INSERT INTO fund_wechat_payment_observation (
                    observation_uid, tenant_id, organization_id, payment_id,
                    observation_type, evidence_source_kind, source_scope_kind,
                    source_inbox_id, source_task_attempt_id,
                    raw_channel_state, api_error_code, transaction_id,
                    observed_mchid, observed_appid, observed_out_trade_no,
                    total_amount_cent, payer_total_cent, payer_openid,
                    observed_currency, channel_occurred_at, content_sha256,
                    observed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, 'TASK_ATTEMPT', 'ORGANIZATION',
                          NULL, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?,
                          ?, ?)
                """, UUID.randomUUID().toString(), current.tenantId(),
                current.organizationId(), current.paymentId(), observationType,
                command.sourceTaskAttemptId(), result.channelState(),
                result.errorCode(), result.transactionId(),
                result.mchid(), result.appid(), result.outTradeNo(),
                result.totalAmountCent(), result.currency(),
                databaseTime(result.channelTime()),
                sha256(observationType + "|" + safe(result.channelState())
                        + "|" + safe(result.transactionId())
                        + "|" + safe(result.errorCode())
                        + "|" + safe(result.mchid())
                        + "|" + safe(result.appid())
                        + "|" + safe(result.outTradeNo())
                        + "|" + result.totalAmountCent()
                        + "|" + safe(result.currency())),
                now, now);
        if (isAuthoritativeNativeQuery(attemptKind, result)) {
            Validation evidence = WechatChannelEvidencePolicy
                    .validateNativeQuery(
                            current.mchid(), current.appid(),
                            current.outTradeNo(), current.amountCent(), result);
            if (!evidence.trusted()) {
                observePaymentIssue(
                        command, current,
                        "FUNDS.NATIVE_PAYMENT_EVIDENCE_MISMATCH",
                        "CRITICAL", evidence.safeSummary(), result, now);
                return new Result(Result.Outcome.BLOCKED,
                        "Wechat payment evidence mismatch: "
                                + evidence.safeSummary());
            }
        }
        if (result.outcome() == NativePaymentResult.Outcome.REFUNDED) {
            observePaymentIssue(
                    command, current,
                    "FUNDS.NATIVE_PAYMENT_REFUNDED",
                    "CRITICAL", "AUTHORITATIVE_REFUND_TERMINAL_STATE",
                    result, now);
            return new Result(Result.Outcome.BLOCKED,
                    "refunded payment requires reconciliation");
        }
        if (result.outcome() == NativePaymentResult.Outcome.UNKNOWN_STATE
                || result.outcome()
                == NativePaymentResult.Outcome.PERMANENT_FAILURE) {
            String unknown = result.outcome()
                    == NativePaymentResult.Outcome.UNKNOWN_STATE
                    ? "UNKNOWN_CHANNEL_STATE" : "PERMANENT_CHANNEL_ERROR";
            observePaymentIssue(
                    command, current,
                    result.outcome() == NativePaymentResult.Outcome.UNKNOWN_STATE
                            ? "FUNDS.NATIVE_PAYMENT_UNKNOWN_STATE"
                            : "FUNDS.NATIVE_PAYMENT_CHANNEL_CONFIGURATION",
                    "CRITICAL", unknown, result, now);
            return new Result(Result.Outcome.BLOCKED,
                    "payment channel result requires reconciliation");
        }
        if (result.outcome() == NativePaymentResult.Outcome.SUCCEEDED) {
            if (result.transactionId() == null || result.transactionId().isBlank()) {
                return new Result(Result.Outcome.BLOCKED,
                        "successful payment lacks transaction id");
            }
            if (current.transactionId() != null
                    && !current.transactionId().equals(result.transactionId())) {
                return new Result(Result.Outcome.BLOCKED,
                        "successful payment transaction id conflicts");
            }
            if (List.of("CLOSED", "EXPIRED").contains(
                    current.businessState())) {
                return new Result(Result.Outcome.BLOCKED,
                        "successful payment conflicts with a closed recharge");
            }
            jdbc.update("""
                    UPDATE fund_wechat_payment
                    SET transaction_id = COALESCE(transaction_id, ?),
                        channel_state = ?, last_api_error_code = NULL,
                        channel_updated_at = ?, lock_version = lock_version + 1,
                        updated_at = ?
                    WHERE id = ?
                    """, result.transactionId(), result.channelState(), now,
                    now, current.paymentId());
            int advanced = jdbc.update("""
                    UPDATE fund_recharge_order
                    SET business_state = 'PAID_PENDING_POST', paid_at = ?,
                        lock_version = lock_version + 1, updated_at = ?
                    WHERE id = ? AND business_state = 'PENDING_PAYMENT'
                    """, now, now, current.rechargeId());
            if (advanced == 1) {
                registerTask(
                        current.tenantId(), current.organizationId(),
                        "POST_RECHARGE_NET_AMOUNT",
                        "POST_RECHARGE_NET_AMOUNT:" + current.rechargeNo(),
                        current.rechargeNo(),
                        "{\"rechargeNo\":\"" + current.rechargeNo() + "\"}",
                        now);
            }
            return new Result(Result.Outcome.DONE, "payment success merged");
        }
        if (NativePaymentLifecyclePolicy.successAlreadyEstablished(
                current.businessState(), current.channelState(),
                current.transactionId())) {
            return new Result(Result.Outcome.DONE,
                    "late non-success observation preserved without projection rollback");
        }
        if (attemptKind == PaymentAttemptKind.CLOSE) {
            jdbc.update("""
                    UPDATE fund_wechat_payment
                    SET last_api_error_code = ?, channel_updated_at = ?,
                        lock_version = lock_version + 1, updated_at = ?
                    WHERE id = ?
                    """, result.errorCode(), now, now, current.paymentId());
            if (result.outcome() == NativePaymentResult.Outcome.CLOSED) {
                jdbc.update("""
                        UPDATE fund_wechat_payment
                        SET channel_state = 'CLOSE_ACCEPTED',
                            last_api_error_code = NULL,
                            channel_updated_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                        """, now, now, current.paymentId());
                registerTask(
                        current.tenantId(), current.organizationId(),
                        "QUERY_NATIVE_PAYMENT",
                        "QUERY_NATIVE_PAYMENT_AFTER_CLOSE:"
                                + current.rechargeNo(),
                        current.rechargeNo(),
                        "{\"rechargeNo\":\"" + current.rechargeNo()
                                + "\",\"afterClose\":true}",
                        now.plusSeconds(30));
                return new Result(Result.Outcome.DONE,
                        "close accepted; final channel query scheduled");
            }
            if (result.outcome()
                    == NativePaymentResult.Outcome.PERMANENT_FAILURE) {
                return new Result(Result.Outcome.BLOCKED,
                        "payment close was permanently rejected");
            }
            if (result.outcome()
                    == NativePaymentResult.Outcome.RETRYABLE_FAILURE) {
                return new Result(Result.Outcome.RETRY,
                        "temporary payment close channel error",
                        Duration.ofSeconds(30));
            }
            return new Result(Result.Outcome.WAITING,
                    "payment close remains uncertain",
                    Duration.ofSeconds(30));
        }
        if (attemptKind == PaymentAttemptKind.QUERY
                && "CLOSE_ACCEPTED".equals(current.channelState())
                && result.outcome() != NativePaymentResult.Outcome.CLOSED) {
            jdbc.update("""
                    UPDATE fund_wechat_payment
                    SET last_api_error_code = ?, channel_updated_at = ?,
                        lock_version = lock_version + 1, updated_at = ?
                    WHERE id = ?
                    """, result.errorCode(), now, now, current.paymentId());
            return new Result(Result.Outcome.WAITING,
                    "close was accepted; awaiting authoritative CLOSED query",
                    Duration.ofSeconds(30));
        }
        if (attemptKind == PaymentAttemptKind.CREATE
                && result.outcome()
                == NativePaymentResult.Outcome.ORDER_ALREADY_EXISTS) {
            jdbc.update("""
                    UPDATE fund_wechat_payment
                    SET last_api_error_code = ?, channel_updated_at = ?,
                        lock_version = lock_version + 1, updated_at = ?
                    WHERE id = ?
                    """, result.errorCode(), now, now, current.paymentId());
            registerTask(
                    current.tenantId(), current.organizationId(),
                    "QUERY_NATIVE_PAYMENT",
                    "QUERY_NATIVE_PAYMENT:" + current.rechargeNo(),
                    current.rechargeNo(),
                    "{\"rechargeNo\":\"" + current.rechargeNo() + "\"}",
                    now);
            return new Result(Result.Outcome.DONE,
                    "out_trade_no exists; authoritative query scheduled");
        }
        jdbc.update("""
                UPDATE fund_wechat_payment
                SET code_url = CASE WHEN ? IS NOT NULL THEN ? ELSE code_url END,
                    channel_state = ?, last_api_error_code = ?,
                    channel_updated_at = ?, lock_version = lock_version + 1,
                    updated_at = ?
                WHERE id = ?
                """, validCodeUrl(result.codeUrl()), validCodeUrl(result.codeUrl()),
                result.channelState(), result.errorCode(), now, now,
                current.paymentId());
        if (attemptKind == PaymentAttemptKind.CREATE
                && result.outcome() == NativePaymentResult.Outcome.ACCEPTED) {
            registerTask(
                    current.tenantId(), current.organizationId(),
                    "QUERY_NATIVE_PAYMENT",
                    "QUERY_NATIVE_PAYMENT:" + current.rechargeNo(),
                    current.rechargeNo(),
                    "{\"rechargeNo\":\"" + current.rechargeNo() + "\"}",
                    now.plusSeconds(2));
            return new Result(Result.Outcome.DONE, "native code url prepared");
        }
        if (attemptKind == PaymentAttemptKind.QUERY
                && NativePaymentLifecyclePolicy
                .shouldCloseAfterExpiredUnpaidQuery(
                        current.expiresAt(), now, result)) {
            registerTask(
                    current.tenantId(), current.organizationId(),
                    "CLOSE_NATIVE_PAYMENT",
                    "CLOSE_NATIVE_PAYMENT:" + current.rechargeNo(),
                    current.rechargeNo(),
                    "{\"rechargeNo\":\"" + current.rechargeNo()
                            + "\",\"expiredUnpaid\":true}",
                    now);
            return new Result(Result.Outcome.DONE,
                    "expired unpaid order queued for close");
        }
        return switch (result.outcome()) {
            case CLOSED -> {
                String businessState = NativePaymentLifecyclePolicy
                        .closedBusinessState(current.expiresAt(), now);
                jdbc.update("""
                        UPDATE fund_recharge_order
                        SET business_state = ?, closed_at = ?,
                            lock_version = lock_version + 1, updated_at = ?
                        WHERE id = ? AND business_state = 'PENDING_PAYMENT'
                        """, businessState, now, now, current.rechargeId());
                yield new Result(Result.Outcome.DONE, "payment closed");
            }
            case RETRYABLE_FAILURE -> new Result(
                    attemptKind == PaymentAttemptKind.QUERY
                            ? Result.Outcome.WAITING
                            : Result.Outcome.RETRY,
                    "temporary native payment channel error",
                    Duration.ofSeconds(30));
            case ACCEPTED, NOT_FOUND -> new Result(
                    Result.Outcome.WAITING, "payment remains non-terminal",
                    Duration.ofSeconds(30));
            case PERMANENT_FAILURE, UNKNOWN_STATE ->
                    throw new IllegalStateException("handled above");
            case SUCCEEDED, REFUNDED, ORDER_ALREADY_EXISTS ->
                    throw new IllegalStateException("handled above");
        };
    }

    private static boolean isAuthoritativeNativeQuery(
            PaymentAttemptKind attemptKind,
            NativePaymentResult result) {
        if (attemptKind != PaymentAttemptKind.QUERY) return false;
        return switch (result.outcome()) {
            case ACCEPTED, SUCCEEDED, REFUNDED, CLOSED, UNKNOWN_STATE -> true;
            case ORDER_ALREADY_EXISTS, NOT_FOUND, RETRYABLE_FAILURE,
                    PERMANENT_FAILURE -> false;
        };
    }

    private Result validateOriginalNativeRequest(
            Command command,
            PaymentSnapshot payment) {
        String notifyUrl = payment.notifyUrlSnapshot();
        if (notifyUrl == null) {
            String current = notifyBaseUrl
                    + "/api/v1/wechat-pay/notifications/native-payments";
            if (MessageDigest.isEqual(
                    sha256(current), payment.notifyUrlSha256())) {
                notifyUrl = current;
            }
        }
        boolean validNotify = notifyUrl != null
                && MessageDigest.isEqual(
                sha256(notifyUrl), payment.notifyUrlSha256());
        byte[] rebuilt = nativeRequestDigest(
                payment.mchid(), payment.appid(), payment.outTradeNo(),
                payment.amountCent(), payment.currency(),
                payment.description(), payment.expiresAt(),
                payment.notifyUrlSha256());
        boolean validRequest = MessageDigest.isEqual(
                rebuilt, payment.requestSha256());
        if (validNotify && validRequest) {
            payment.useNotifyUrl(notifyUrl);
            return null;
        }
        String reason = !validNotify
                ? "NOTIFY_URL_SNAPSHOT_UNAVAILABLE_OR_MISMATCH"
                : "ORIGINAL_REQUEST_DIGEST_MISMATCH";
        String finalNotifyUrl = notifyUrl;
        return transactions.execute(status -> {
            PaymentSnapshot current = lockPayment(payment.rechargeNo());
            observePaymentIssue(
                    command, current,
                    "FUNDS.NATIVE_PAYMENT_ORIGINAL_REQUEST_UNAVAILABLE",
                    "CRITICAL", reason,
                    new NativePaymentResult(
                            NativePaymentResult.Outcome.PERMANENT_FAILURE,
                            "LOCAL_REQUEST_VALIDATION", null, null,
                            "ORIGINAL_REQUEST_MISMATCH",
                            finalNotifyUrl == null
                                    ? "notify URL unavailable"
                                    : "request digest mismatch",
                            Instant.now()),
                    databaseNow());
            return new Result(Result.Outcome.BLOCKED,
                    "original native payment request cannot be reproduced");
        });
    }

    private void observePaymentIssue(
            Command command,
            PaymentSnapshot payment,
            String issueCode,
            String severity,
            String reason,
            NativePaymentResult result,
            LocalDateTime now) {
        String evidence = issueCode + "|" + payment.outTradeNo() + "|"
                + safe(result.channelState()) + "|"
                + safe(result.errorCode()) + "|" + reason;
        String summary = "reason=" + reason
                + "; channelState=" + safe(result.channelState())
                + "; errorCode=" + safe(result.errorCode());
        operationalControl.observeReconciliationIssue(
                new ReconciliationIssue(
                        payment.tenantId(), payment.organizationId(),
                        command.sourceTaskAttemptId(), issueCode, severity,
                        "WECHAT_PAYMENT", payment.outTradeNo(),
                        sha256(evidence), summary, now));
    }

    private Result postRecharge(Command command) {
        return transactions.execute(status -> {
            PaymentSnapshot row = lockPayment(command.targetStableKey());
            if ("POSTED".equals(row.businessState())) {
                return new Result(Result.Outcome.DONE, "already posted");
            }
            if (!"PAID_PENDING_POST".equals(row.businessState())) {
                return new Result(Result.Outcome.WAITING,
                        "trusted payment success is not present yet");
            }
            AccountRow account = jdbc.queryForObject("""
                    SELECT id, available_payout_cent, frozen_withdrawal_cent
                    FROM fund_organization_payout_account
                    WHERE tenant_id = ? AND organization_id = ?
                    FOR UPDATE
                    """, (rs, ignored) -> new AccountRow(
                            rs.getLong("id"),
                            rs.getLong("available_payout_cent"),
                            rs.getLong("frozen_withdrawal_cent")),
                    row.tenantId(), row.organizationId());
            long after = Math.addExact(account.availableCent(), row.netCent());
            LocalDateTime now = databaseNow();
            try {
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
                        ) VALUES (?, ?, ?, ?, 'RECHARGE_POSTED', ?, ?, ?,
                                  0, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?)
                        """, UUID.randomUUID().toString(), row.tenantId(),
                        row.organizationId(), account.id(), row.netCent(),
                        account.availableCent(), after, account.frozenCent(),
                        account.frozenCent(), row.rechargeId(), row.amountCent(),
                        row.feeCent(), row.netCent(), now, now);
            } catch (DuplicateKeyException duplicate) {
                // 唯一 recharge_order_id 证明该净额已记账，继续收敛投影状态。
            }
            jdbc.update("""
                    UPDATE fund_organization_payout_account
                    SET available_payout_cent = ?, lock_version = lock_version + 1,
                        updated_at = ?
                    WHERE id = ? AND available_payout_cent = ?
                    """, after, now, account.id(), account.availableCent());
            jdbc.update("""
                    UPDATE fund_recharge_order
                    SET business_state = 'POSTED', posted_at = ?,
                        lock_version = lock_version + 1, updated_at = ?
                    WHERE id = ? AND business_state = 'PAID_PENDING_POST'
                    """, now, now, row.rechargeId());
            return new Result(Result.Outcome.DONE, "recharge net amount posted");
        });
    }

    private PaymentSnapshot paymentSnapshot(String rechargeNo) {
        return transactions.execute(status -> payment(rechargeNo, false));
    }

    private PaymentSnapshot lockPayment(String rechargeNo) {
        return payment(rechargeNo, true);
    }

    private PaymentSnapshot lockPaymentByOutTradeNo(String outTradeNo) {
        return jdbc.queryForObject("""
                SELECT r.id recharge_id, r.recharge_order_no,
                       r.tenant_id, r.organization_id,
                       r.gross_amount_cent, r.fee_amount_cent,
                       r.net_amount_cent, r.business_state, r.expires_at,
                       p.id payment_id, p.mchid_snapshot, p.appid_snapshot,
                       p.out_trade_no, p.request_amount_cent, p.currency,
                       p.description, p.time_expire, p.channel_state,
                       p.transaction_id, p.notify_url_snapshot,
                       p.notify_url_sha256, p.request_sha256
                FROM fund_recharge_order r
                JOIN fund_wechat_payment p ON p.recharge_order_id = r.id
                WHERE p.out_trade_no = ?
                FOR UPDATE
                """, (rs, ignored) -> new PaymentSnapshot(
                        rs.getLong("recharge_id"),
                        rs.getString("recharge_order_no"),
                        rs.getLong("tenant_id"),
                        rs.getLong("organization_id"),
                        rs.getLong("payment_id"),
                        rs.getString("mchid_snapshot"),
                        rs.getString("appid_snapshot"),
                        rs.getString("out_trade_no"),
                        rs.getLong("request_amount_cent"),
                        rs.getLong("fee_amount_cent"),
                        rs.getLong("net_amount_cent"),
                        rs.getString("description"),
                        rs.getString("currency"),
                        rs.getObject("time_expire", LocalDateTime.class),
                        rs.getString("business_state"),
                        rs.getString("channel_state"),
                        rs.getString("transaction_id"),
                        rs.getString("notify_url_snapshot"),
                        rs.getBytes("notify_url_sha256"),
                        rs.getBytes("request_sha256")),
                outTradeNo);
    }

    private PaymentSnapshot payment(String rechargeNo, boolean lock) {
        return jdbc.queryForObject("""
                SELECT r.id recharge_id, r.recharge_order_no,
                       r.tenant_id, r.organization_id,
                       r.gross_amount_cent, r.fee_amount_cent,
                       r.net_amount_cent, r.business_state, r.expires_at,
                       p.id payment_id, p.mchid_snapshot, p.appid_snapshot,
                       p.out_trade_no, p.request_amount_cent, p.currency,
                       p.description, p.time_expire, p.channel_state,
                       p.transaction_id, p.notify_url_snapshot,
                       p.notify_url_sha256, p.request_sha256
                FROM fund_recharge_order r
                JOIN fund_wechat_payment p ON p.recharge_order_id = r.id
                WHERE r.recharge_order_no = ?
                """ + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> new PaymentSnapshot(
                        rs.getLong("recharge_id"),
                        rs.getString("recharge_order_no"),
                        rs.getLong("tenant_id"),
                        rs.getLong("organization_id"),
                        rs.getLong("payment_id"),
                        rs.getString("mchid_snapshot"),
                        rs.getString("appid_snapshot"),
                        rs.getString("out_trade_no"),
                        rs.getLong("request_amount_cent"),
                        rs.getLong("fee_amount_cent"),
                        rs.getLong("net_amount_cent"),
                        rs.getString("description"),
                        rs.getString("currency"),
                        rs.getObject("time_expire", LocalDateTime.class),
                        rs.getString("business_state"),
                        rs.getString("channel_state"),
                        rs.getString("transaction_id"),
                        rs.getString("notify_url_snapshot"),
                        rs.getBytes("notify_url_sha256"),
                        rs.getBytes("request_sha256")),
                rechargeNo);
    }

    private BindingRow requiredBinding(
            long tenantId, long organizationId, boolean lock) {
        List<BindingRow> rows = jdbc.query("""
                SELECT b.id binding_id, b.merchant_profile_id,
                       b.miniapp_channel_id, b.appid,
                       m.mchid, m.scene_id, m.report_type,
                       m.report_content, m.transfer_page_style
                FROM fund_miniapp_merchant_binding b
                JOIN fund_wechat_merchant_profile m
                  ON m.id = b.merchant_profile_id
                JOIN iam_miniapp_channel app
                  ON app.id = b.miniapp_channel_id
                 AND app.appid = b.appid
                WHERE b.tenant_id = ? AND b.organization_id = ?
                  AND b.status = 'VERIFIED'
                  AND m.status = 'ENABLED'
                  AND app.login_enabled = 1
                """ + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> new BindingRow(
                        rs.getLong("binding_id"),
                        rs.getLong("merchant_profile_id"),
                        rs.getLong("miniapp_channel_id"),
                        rs.getString("appid"), rs.getString("mchid"),
                        rs.getString("scene_id"), rs.getString("report_type"),
                        rs.getString("report_content"),
                        rs.getString("transfer_page_style")),
                tenantId, organizationId);
        if (rows.isEmpty()) {
            throw new TargetApiException(
                    422,
                    "FUNDS.MINIAPP_MERCHANT_BINDING_UNAVAILABLE",
                    "机构小程序尚未完成系统商户绑定核验");
        }
        return rows.getFirst();
    }

    private RechargeRow requiredRecharge(WebScope scope, String rechargeNo) {
        RechargeRow row = findRecharge(scope, rechargeNo);
        if (row == null) {
            throw new TargetApiException(
                    404, "RESOURCE.NOT_FOUND", "充值单不存在");
        }
        return row;
    }

    private RechargeRow findRecharge(WebScope scope, String rechargeNo) {
        List<RechargeRow> rows = jdbc.query("""
                SELECT r.*, p.code_url, p.channel_state,
                       p.last_api_error_code, p.payment_uid
                FROM fund_recharge_order r
                JOIN fund_wechat_payment p ON p.recharge_order_id = r.id
                WHERE r.tenant_id = ? AND r.organization_id = ?
                  AND r.recharge_order_no = ?
                """, (rs, ignored) -> row(rs), scope.tenantId(),
                scope.organizationId(), rechargeNo);
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private RechargeRow row(java.sql.ResultSet rs)
            throws java.sql.SQLException {
        return new RechargeRow(
                rs.getLong("id"), rs.getString("recharge_order_no"),
                rs.getString("payment_uid"),
                rs.getLong("gross_amount_cent"),
                rs.getLong("fee_amount_cent"),
                rs.getLong("net_amount_cent"),
                rs.getString("business_state"),
                rs.getLong("lock_version"),
                rs.getObject("expires_at", LocalDateTime.class),
                rs.getObject("created_at", LocalDateTime.class),
                rs.getObject("paid_at", LocalDateTime.class),
                rs.getObject("posted_at", LocalDateTime.class),
                rs.getString("code_url"),
                rs.getString("channel_state"),
                rs.getString("last_api_error_code"));
    }

    private RechargeView view(RechargeRow row, String statusBase) {
        boolean ready = row.codeUrl() != null
                && "PENDING_PAYMENT".equals(row.state())
                && row.expiresAt().isAfter(databaseNow());
        String preparation = ready ? "READY"
                : row.errorCode() != null ? "RETRYING"
                : row.channelState() == null ? "PENDING" : "PENDING";
        String statusUrl = stripTrailingSlash(statusBase)
                + "/recharge-orders/" + row.rechargeNo();
        return new RechargeView(
                row.paymentUid(), row.rechargeNo(), row.rechargeNo(),
                row.state(), row.version(), money(row.grossCent()),
                money(row.feeCent()), money(row.netCent()), preparation,
                ready ? row.codeUrl() : null, instant(row.expiresAt()),
                statusUrl, 1000, instant(row.createdAt()),
                instant(row.paidAt()), instant(row.postedAt()));
    }

    private void registerTask(
            long tenantId,
            long organizationId,
            String taskType,
            String taskKey,
            String targetStableKey,
            String snapshot,
            LocalDateTime runAt) {
        tasks.register(new ReliableFundsTaskRegistration(
                tenantId, organizationId, taskType, taskKey,
                "RECHARGE_ORDER", targetStableKey, 1, snapshot,
                sha256(snapshot), 20, runAt));
    }

    private String nativeSnapshot(
            BindingRow binding,
            String outTradeNo,
            long amountCent,
            LocalDateTime expires,
            String notifyUrl) {
        return "{\"appid\":\"" + binding.appid()
                + "\",\"mchid\":\"" + binding.mchid()
                + "\",\"outTradeNo\":\"" + outTradeNo
                + "\",\"amountCent\":" + amountCent
                + ",\"expiresAt\":\"" + instant(expires)
                + "\",\"notifyUrlSha256\":\""
                + HexFormat.of().formatHex(sha256(notifyUrl)) + "\"}";
    }

    static byte[] nativeRequestDigest(
            String mchid,
            String appid,
            String outTradeNo,
            long amountCent,
            String currency,
            String description,
            LocalDateTime expiresAt,
            byte[] notifyUrlSha256) {
        String canonical = "NATIVE_PAYMENT_REQUEST_V2|"
                + part(mchid) + "|" + part(appid) + "|"
                + part(outTradeNo) + "|" + amountCent + "|"
                + part(currency) + "|" + part(description) + "|"
                + expiresAt.toInstant(ZoneOffset.UTC).toEpochMilli() + "|"
                + HexFormat.of().formatHex(notifyUrlSha256);
        return sha256(canonical);
    }

    private static String part(String value) {
        return value.getBytes(StandardCharsets.UTF_8).length + ":" + value;
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

    public static long parseCent(String value, String field) {
        try {
            if (value == null || value.isBlank()) throw new NumberFormatException();
            BigDecimal decimal = new BigDecimal(value.trim())
                    .setScale(2, RoundingMode.UNNECESSARY);
            return decimal.movePointRight(2).longValueExact();
        } catch (ArithmeticException | NumberFormatException invalid) {
            throw new TargetApiException(
                    400,
                    "COMMON.VALIDATION_FAILED",
                    field + " 必须是精确到分的人民币元字符串");
        }
    }

    public static String money(long cent) {
        return BigDecimal.valueOf(cent, 2).toPlainString();
    }

    public static byte[] sha256(String value) {
        try {
            return MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
        } catch (Exception failure) {
            throw new IllegalStateException("SHA-256 unavailable", failure);
        }
    }

    public static String stableNo(String prefix, UUID key) {
        return prefix + key.toString().replace("-", "").substring(0, 30);
    }

    private static void requireUuidV4(UUID value) {
        if (value == null || value.version() != 4) {
            throw new TargetApiException(
                    400,
                    "COMMON.VALIDATION_FAILED",
                    "Idempotency-Key 必须是 UUIDv4");
        }
    }

    private static int normalizeLimit(Integer value) {
        int limit = value == null ? 20 : value;
        if (limit < 1 || limit > 100) {
            throw new TargetApiException(
                    400, "COMMON.VALIDATION_FAILED",
                    "limit 必须在 1 到 100 之间");
        }
        return limit;
    }

    private static String normalizeStatus(String value) {
        String result = blankToNull(value);
        if (result == null) return null;
        result = result.toUpperCase(Locale.ROOT);
        if (!List.of("PENDING_PAYMENT", "PAID_PENDING_POST", "POSTED",
                "CLOSED", "EXPIRED").contains(result)) {
            throw new TargetApiException(
                    400, "COMMON.VALIDATION_FAILED", "不支持的充值状态");
        }
        return result;
    }

    private static String validCodeUrl(String value) {
        return value != null && value.startsWith("weixin://")
                && !value.contains(" ") ? value : null;
    }

    private static String safe(String value) {
        return value == null ? "" : value;
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value.trim();
    }

    private static String stripTrailingSlash(String value) {
        if (value == null || value.isBlank()) return "";
        String result = value.trim();
        while (result.endsWith("/")) {
            result = result.substring(0, result.length() - 1);
        }
        return result;
    }

    private static Instant instant(LocalDateTime value) {
        return value == null ? null : value.toInstant(ZoneOffset.UTC);
    }

    private static String requiredText(JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        if (value == null || value.isNull() || value.asText().isBlank()) {
            throw new IllegalArgumentException(
                    "trusted payment notification lacks " + field);
        }
        return value.asText();
    }

    private static long requiredLong(JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        if (value == null || !value.canConvertToLong()) {
            throw new IllegalArgumentException(
                    "trusted payment notification lacks " + field);
        }
        return value.asLong();
    }

    private static Instant parseInstant(String value) {
        return java.time.OffsetDateTime.parse(value).toInstant();
    }

    private static LocalDateTime databaseTime(Instant value) {
        return value == null ? null : LocalDateTime.ofInstant(value, ZoneOffset.UTC);
    }

    private static TargetApiException idempotencyConflict() {
        return new TargetApiException(
                409,
                "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                "该 Idempotency-Key 已用于不同的充值请求");
    }

    private record BindingRow(
            long bindingId, long merchantProfileId, long miniappId,
            String appid, String mchid, String sceneId,
            String reportType, String reportContent, String pageStyle) {
    }

    private record RechargeRow(
            long id, String rechargeNo, String paymentUid, long grossCent,
            long feeCent, long netCent, String state, long version,
            LocalDateTime expiresAt, LocalDateTime createdAt,
            LocalDateTime paidAt, LocalDateTime postedAt,
            String codeUrl, String channelState, String errorCode) {
    }

    private record PayoutEntryRow(
            long id,
            String entryUid,
            LocalDateTime occurredAt,
            PayoutEntryView view) {
    }

    private static final class PaymentSnapshot {
        private final long rechargeId;
        private final String rechargeNo;
        private final long tenantId;
        private final long organizationId;
        private final long paymentId;
        private final String mchid;
        private final String appid;
        private final String outTradeNo;
        private final long amountCent;
        private final long feeCent;
        private final long netCent;
        private final String description;
        private final String currency;
        private final LocalDateTime expiresAt;
        private final String businessState;
        private final String channelState;
        private final String transactionId;
        private final String notifyUrlSnapshot;
        private final byte[] notifyUrlSha256;
        private final byte[] requestSha256;
        private String notifyUrl;

        private PaymentSnapshot(
            long rechargeId, String rechargeNo, long tenantId,
            long organizationId, long paymentId, String mchid,
            String appid, String outTradeNo, long amountCent,
            long feeCent, long netCent, String description,
            String currency, LocalDateTime expiresAt, String businessState,
            String channelState, String transactionId,
            String notifyUrlSnapshot, byte[] notifyUrlSha256,
            byte[] requestSha256) {
            this.rechargeId = rechargeId;
            this.rechargeNo = rechargeNo;
            this.tenantId = tenantId;
            this.organizationId = organizationId;
            this.paymentId = paymentId;
            this.mchid = mchid;
            this.appid = appid;
            this.outTradeNo = outTradeNo;
            this.amountCent = amountCent;
            this.feeCent = feeCent;
            this.netCent = netCent;
            this.description = description;
            this.currency = currency;
            this.expiresAt = expiresAt;
            this.businessState = businessState;
            this.channelState = channelState;
            this.transactionId = transactionId;
            this.notifyUrlSnapshot = notifyUrlSnapshot;
            this.notifyUrlSha256 = Arrays.copyOf(notifyUrlSha256,
                    notifyUrlSha256.length);
            this.requestSha256 = Arrays.copyOf(requestSha256,
                    requestSha256.length);
        }

        long rechargeId() { return rechargeId; }
        String rechargeNo() { return rechargeNo; }
        long tenantId() { return tenantId; }
        long organizationId() { return organizationId; }
        long paymentId() { return paymentId; }
        String mchid() { return mchid; }
        String appid() { return appid; }
        String outTradeNo() { return outTradeNo; }
        long amountCent() { return amountCent; }
        long feeCent() { return feeCent; }
        long netCent() { return netCent; }
        String description() { return description; }
        String currency() { return currency; }
        LocalDateTime expiresAt() { return expiresAt; }
        String businessState() { return businessState; }
        String channelState() { return channelState; }
        String transactionId() { return transactionId; }
        String notifyUrlSnapshot() { return notifyUrlSnapshot; }
        byte[] notifyUrlSha256() {
            return Arrays.copyOf(notifyUrlSha256, notifyUrlSha256.length);
        }
        byte[] requestSha256() {
            return Arrays.copyOf(requestSha256, requestSha256.length);
        }
        String notifyUrl() { return notifyUrl; }
        void useNotifyUrl(String value) { this.notifyUrl = value; }
    }

    private enum PaymentAttemptKind {
        CREATE,
        QUERY,
        CLOSE
    }

    private record AccountRow(long id, long availableCent, long frozenCent) {
    }
}
