package org.enveloping.ecobin.funds.application.authorization;

import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.funds.api.port.FundsOperationalControlPort;
import org.enveloping.ecobin.funds.api.port.FundsOperationalControlPort
        .AuthorizationQueryTaskWakeResult;
import org.enveloping.ecobin.funds.api.port.FundsOperationalControlPort
        .ReconciliationIssue;
import org.enveloping.ecobin.funds.api.port
        .MerchantTransferAuthorizationChannelPort;
import org.enveloping.ecobin.funds.api.port
        .MerchantTransferAuthorizationChannelPort.AuthorizationQuery;
import org.enveloping.ecobin.funds.api.port
        .MerchantTransferAuthorizationChannelPort.AuthorizationRequest;
import org.enveloping.ecobin.funds.api.port
        .MerchantTransferAuthorizationChannelPort.AuthorizationResult;
import org.enveloping.ecobin.funds.api.port.ReliableFundsAttemptBoundaryPort;
import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskExecutorPort;
import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskRegistrationPort;
import org.enveloping.ecobin.funds.api.port.ReliableFundsTaskRegistrationPort
        .ReliableFundsTaskRegistration;
import org.enveloping.ecobin.funds.application.access.FundsAccessService;
import org.enveloping.ecobin.funds.application.access.FundsAccessService
        .MiniappScope;
import org.enveloping.ecobin.funds.application.recharge
        .RechargeApplicationService;
import org.enveloping.ecobin.funds.web.v1.FundsModels
        .MerchantTransferAuthorizationAcceptedView;
import org.enveloping.ecobin.funds.web.v1.FundsModels
        .MerchantTransferAuthorizationView;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.HexFormat;
import java.util.List;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;

/** 微信免确认收款授权聚合及其可靠渠道任务。 */
@Service
public class MerchantTransferAuthorizationApplicationService {

    public static final String CREATE_TASK =
            "CREATE_MERCHANT_TRANSFER_AUTHORIZATION";
    public static final String QUERY_TASK =
            "QUERY_MERCHANT_TRANSFER_AUTHORIZATION";
    private static final String TARGET_TYPE =
            "WECHAT_TRANSFER_AUTHORIZATION";
    private static final String STATUS_URL =
            "/api/v1/miniapp/me/merchant-transfer-authorization";
    private static final String CREATE_ACTION =
            "merchant-transfer-authorization.create";
    private static final String QUERY_ACTION =
            "merchant-transfer-authorization.query";
    private static final Set<String> DEFINITIVE_CREATE_REJECTION_CODES =
            Set.of("PARAM_ERROR", "NO_AUTH", "SIGN_ERROR",
                    "MCHID_MISMATCH");

    private final JdbcTemplate jdbc;
    private final FundsAccessService access;
    private final ReliableFundsTaskRegistrationPort tasks;
    private final ReliableFundsAttemptBoundaryPort attemptBoundary;
    private final MerchantTransferAuthorizationChannelPort channel;
    private final FundsOperationalControlPort operationalControl;
    private final TransactionTemplate transactions;
    private final AuditPort audit;
    private final String notifyBaseUrl;

    public MerchantTransferAuthorizationApplicationService(
            JdbcTemplate jdbc,
            FundsAccessService access,
            ReliableFundsTaskRegistrationPort tasks,
            ReliableFundsAttemptBoundaryPort attemptBoundary,
            MerchantTransferAuthorizationChannelPort channel,
            FundsOperationalControlPort operationalControl,
            TransactionTemplate transactions,
            AuditPort audit,
            @Value("${ecobin.funds.wechat-pay.notify-base-url:https://fake.invalid}")
            String notifyBaseUrl) {
        this.jdbc = jdbc;
        this.access = access;
        this.tasks = tasks;
        this.attemptBoundary = attemptBoundary;
        this.channel = channel;
        this.operationalControl = operationalControl;
        this.transactions = transactions;
        this.audit = audit;
        this.notifyBaseUrl = stripTrailingSlash(notifyBaseUrl);
    }

    @Transactional(readOnly = true)
    public MerchantTransferAuthorizationView current() {
        MiniappScope scope = access.miniappScope(false);
        requiredUser(scope, false);
        AuthorizationBindingRow binding = findBinding(scope, false);
        AuthorizationRow row = binding == null
                ? null
                : findLatestScope(
                        binding.merchantId(), scope.miniappChannelId(),
                        scope.wechatSubjectId(), binding.sceneId(), false);
        return view(row);
    }

    @Transactional
    public MerchantTransferAuthorizationAcceptedView create(
            UUID operationUid) {
        requireUuidV4(operationUid);
        MiniappScope scope = access.miniappScope(true);
        AuthorizationRow replay = replay(
                operationUid, scope, CREATE_ACTION);
        if (replay != null) return accepted(replay);

        UserRow observedUser = requiredUser(scope, false);
        if (!access.lockWithdrawalTransferIdentity(
                scope.tenantId(), scope.organizationId(),
                scope.miniappChannelId(), scope.appid(),
                scope.organizationUserId(), observedUser.openid())) {
            throw unavailable("当前机构用户或小程序状态不允许开通自动收款");
        }
        UserRow user = requiredUser(scope, true);
        if (!"ACTIVE".equals(user.status()) || user.phoneE164() == null) {
            throw unavailable("当前机构用户状态不允许开通自动收款");
        }
        AuthorizationBindingRow binding = requiredBinding(scope, true);

        replay = replay(operationUid, scope, CREATE_ACTION);
        if (replay != null) return accepted(replay);
        AuthorizationRow current = findCurrentScope(
                binding.merchantId(), scope.miniappChannelId(),
                scope.wechatSubjectId(),
                binding.sceneId(), true);
        if (current != null) {
            appendMiniappAudit(
                    scope, operationUid, CREATE_ACTION,
                    current.outAuthorizationNo(),
                    "{\"status\":\"" + publicStatus(current) + "\"}",
                    databaseNow());
            return accepted(current);
        }

        String outAuthorizationNo = RechargeApplicationService.stableNo(
                "AU", operationUid);
        String userDisplayName = safeUserDisplayName(
                scope.organizationUserUid());
        String notifyUrl = notifyBaseUrl
                + "/api/v1/wechat-pay/notifications/"
                + "merchant-transfer-authorizations";
        AuthorizationRequest request = new AuthorizationRequest(
                binding.mchid(), outAuthorizationNo, binding.appid(),
                user.openid(), binding.sceneId(), userDisplayName,
                null, notifyUrl);
        byte[] requestHash = requestHash(request);
        LocalDateTime now = databaseNow();
        jdbc.update("""
                INSERT INTO fund_wechat_transfer_authorization (
                    authorization_uid, tenant_id, organization_id,
                    organization_user_id, wechat_subject_id,
                    merchant_profile_id,
                    miniapp_merchant_binding_id, miniapp_channel_id,
                    out_authorization_no, authorization_id,
                    mchid_snapshot, appid_snapshot, openid_snapshot,
                    scene_id_snapshot, user_display_name_snapshot,
                    user_recv_perception_snapshot,
                    authorization_notify_url_snapshot,
                    notify_url_sha256, request_sha256,
                    local_state, channel_state, package_info,
                    package_expires_at, last_api_error_code,
                    close_reason, state_conflict,
                    submitted_at, channel_created_at,
                    confirmation_deadline_at, authorized_at, closed_at,
                    channel_updated_at, lock_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL,
                          ?, ?, ?, 'CREATED', NULL, NULL, NULL, NULL, NULL, 0,
                          NULL, NULL, NULL, NULL, NULL, NULL, 0, ?, ?)
                """, operationUid.toString(), scope.tenantId(),
                scope.organizationId(), scope.organizationUserId(),
                scope.wechatSubjectId(),
                binding.merchantId(), binding.bindingId(),
                binding.miniappId(), outAuthorizationNo, binding.mchid(),
                binding.appid(), user.openid(), binding.sceneId(),
                userDisplayName, notifyUrl,
                RechargeApplicationService.sha256(notifyUrl),
                requestHash, now, now);
        AuthorizationRow created = requiredByOutNo(
                outAuthorizationNo, false);
        registerTask(created, CREATE_TASK, now);
        registerTask(created, QUERY_TASK, now.plusSeconds(30));
        appendMiniappAudit(
                scope, operationUid, CREATE_ACTION, outAuthorizationNo,
                "{\"status\":\"PREPARING\"}", now);
        return accepted(created);
    }

    @Transactional
    public MerchantTransferAuthorizationAcceptedView requestQuery(
            UUID operationUid) {
        requireUuidV4(operationUid);
        MiniappScope scope = access.miniappScope(false);
        AuthorizationRow replay = replay(
                operationUid, scope, QUERY_ACTION);
        if (replay != null) return accepted(replay);
        requiredUser(scope, false);
        AuthorizationBindingRow binding = requiredBinding(scope, false);
        AuthorizationRow found = findLatestScope(
                binding.merchantId(), scope.miniappChannelId(),
                scope.wechatSubjectId(), binding.sceneId(), false);
        if (found == null) throw notFound();
        AuthorizationRow row = requiredById(found.id(), true);
        replay = replay(operationUid, scope, QUERY_ACTION);
        if (replay != null) return accepted(replay);
        if (!isTerminal(row.localState())
                && !"FAILED".equals(publicStatus(row))) {
            AuthorizationQueryTaskWakeResult result =
                    operationalControl
                            .wakeMerchantTransferAuthorizationQuery(
                                    row.tenantId(), row.organizationId(),
                                    row.outAuthorizationNo(), databaseNow());
            if (result == AuthorizationQueryTaskWakeResult.NOT_WAKEABLE) {
                throw stateConflict("授权查单任务当前无法安全唤醒");
            }
        }
        appendMiniappAudit(
                scope, operationUid, QUERY_ACTION,
                row.outAuthorizationNo(),
                "{\"status\":\"" + publicStatus(row) + "\"}",
                databaseNow());
        return accepted(row);
    }

    public ReliableFundsTaskExecutorPort.Result executeTask(
            ReliableFundsTaskExecutorPort.Command command) {
        return switch (command.taskType()) {
            case CREATE_TASK -> executeCreate(command);
            case QUERY_TASK -> executeQuery(command);
            default -> new ReliableFundsTaskExecutorPort.Result(
                    ReliableFundsTaskExecutorPort.Result.Outcome.BLOCKED,
                    "unsupported authorization task type");
        };
    }

    private ReliableFundsTaskExecutorPort.Result executeCreate(
            ReliableFundsTaskExecutorPort.Command command) {
        AuthorizationRow row = requiredByOutNo(
                command.targetStableKey(), false);
        if ("CREATE_REJECTED".equals(row.localState())) {
            return blocked(
                    "permanently rejected authorization request cannot be "
                            + "replayed; a new user request is required");
        }
        if (!"CREATED".equals(row.localState())) {
            return done("authorization creation already converged");
        }
        AuthorizationRequest request = originalRequest(row);
        if (request == null) {
            return transactions.execute(status -> {
                AuthorizationRow locked = requiredById(row.id(), true);
                observeIssue(
                        command.sourceTaskAttemptId(), locked,
                        "FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_REQUEST_MISMATCH",
                        "CRITICAL", "ORIGINAL_REQUEST_DIGEST_MISMATCH",
                        null, databaseNow());
                return blocked(
                        "original authorization request cannot be reproduced");
            });
        }
        attemptBoundary.markExternalCallMayHaveStarted(command.attemptUid());
        AuthorizationResult result = channel.create(request);
        return transactions.execute(status -> mergeTaskResult(
                command, row.id(), "CREATE_RESPONSE", result));
    }

    private ReliableFundsTaskExecutorPort.Result executeQuery(
            ReliableFundsTaskExecutorPort.Command command) {
        AuthorizationRow row = requiredByOutNo(
                command.targetStableKey(), false);
        if (isTerminal(row.localState())) {
            return done("authorization is terminal");
        }
        attemptBoundary.markExternalCallMayHaveStarted(command.attemptUid());
        AuthorizationResult result = channel.query(new AuthorizationQuery(
                row.mchid(), row.outAuthorizationNo(), row.appid(),
                row.openid(), row.sceneId(), row.userDisplayName(),
                row.userRecvPerception(), instant(row.channelCreatedAt()),
                false));
        return transactions.execute(status -> mergeTaskResult(
                command, row.id(), "QUERY", result));
    }

    private ReliableFundsTaskExecutorPort.Result mergeTaskResult(
            ReliableFundsTaskExecutorPort.Command command,
            long authorizationId,
            String observationType,
            AuthorizationResult result) {
        LocalDateTime now = databaseNow();
        AuthorizationRow row = requiredById(authorizationId, true);
        appendTaskObservation(command, row, observationType, result, now);
        if (result.outcome()
                == AuthorizationResult.Outcome.RETRYABLE_FAILURE) {
            jdbc.update("""
                    UPDATE fund_wechat_transfer_authorization
                    SET last_api_error_code = ?,
                        lock_version = lock_version + 1, updated_at = ?
                    WHERE id = ?
                    """, trimTo(result.errorCode(), 64), now, row.id());
            return channelResult(
                    ReliableFundsTaskExecutorPort.Result.Outcome.RETRY,
                    row, "temporary authorization channel error", result,
                    retryDelay(row.id()));
        }
        if (result.outcome()
                == AuthorizationResult.Outcome.PERMANENT_FAILURE) {
            boolean definitiveCreateRejection =
                    "CREATE_RESPONSE".equals(observationType)
                            && isDefinitiveCreateRejection(
                            result.errorCode());
            if (definitiveCreateRejection) {
                jdbc.update("""
                        UPDATE fund_wechat_transfer_authorization
                        SET local_state = 'CREATE_REJECTED',
                            last_api_error_code = ?,
                            submitted_at = COALESCE(submitted_at, ?),
                            channel_updated_at = COALESCE(
                                channel_updated_at, ?),
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ? AND local_state = 'CREATED'
                        """, trimTo(result.errorCode(), 64), now, now,
                        now, row.id());
                AuthorizationQueryTaskWakeResult convergence =
                        operationalControl
                                .convergeTerminalMerchantTransferAuthorizationQuery(
                                        row.tenantId(),
                                        row.organizationId(),
                                        row.outAuthorizationNo(),
                                        now);
                if (convergence
                        == AuthorizationQueryTaskWakeResult.NOT_WAKEABLE) {
                    observeIssue(
                            command.sourceTaskAttemptId(), row,
                            "FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_QUERY_RECOVERY_MISSING",
                            "CRITICAL",
                            "QUERY_TASK_NOT_CONVERGENT_AFTER_CREATE_REJECTION",
                            result, now);
                }
            } else {
                jdbc.update("""
                        UPDATE fund_wechat_transfer_authorization
                        SET last_api_error_code = ?,
                            lock_version = lock_version + 1, updated_at = ?
                        WHERE id = ?
                        """, trimTo(result.errorCode(), 64), now, row.id());
            }
            observeIssue(
                    command.sourceTaskAttemptId(), row,
                    "FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_CHANNEL_CONFIGURATION",
                    "CRITICAL", "PERMANENT_CHANNEL_ERROR", result, now);
            return channelResult(
                    ReliableFundsTaskExecutorPort.Result.Outcome.BLOCKED,
                    row,
                    definitiveCreateRejection
                            ? "authorization creation was definitively "
                            + "rejected; a new application is required after "
                            + "the cause is fixed"
                            : "permanent authorization channel error requires "
                            + "operator recovery against the original order",
                    result, null);
        }
        if (result.outcome() == AuthorizationResult.Outcome.UNKNOWN_STATE) {
            if ("CREATE_RESPONSE".equals(observationType)
                    && "INVALID_REQUEST".equals(result.errorCode())) {
                jdbc.update("""
                        UPDATE fund_wechat_transfer_authorization
                        SET last_api_error_code = 'INVALID_REQUEST',
                            submitted_at = COALESCE(submitted_at, ?),
                            channel_updated_at = COALESCE(
                                channel_updated_at, ?),
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ? AND local_state = 'CREATED'
                        """, now, now, now, row.id());
                wakeQueryAfterUncertainCreate(
                        command, row, result, now,
                        "QUERY_TASK_NOT_WAKEABLE_AFTER_INVALID_REQUEST");
                return channelResult(
                        ReliableFundsTaskExecutorPort.Result.Outcome.BLOCKED,
                        row,
                        "WeChat requires querying the original authorization "
                                + "number; create POST will not be retried",
                        result, null);
            }
            markUnknown(row, result.channelState(), now);
            if ("CREATE_RESPONSE".equals(observationType)) {
                wakeQueryAfterUncertainCreate(
                        command, row, result, now,
                        "QUERY_TASK_NOT_WAKEABLE_AFTER_UNKNOWN_CREATE");
            }
            observeIssue(
                    command.sourceTaskAttemptId(), row,
                    "FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_UNKNOWN_STATE",
                    "CRITICAL", "UNKNOWN_CHANNEL_STATE", result, now);
            return blocked("unknown authorization state requires reconciliation");
        }
        if (result.outcome() == AuthorizationResult.Outcome.NOT_FOUND) {
            return mergeNotFound(command, row, now);
        }
        EvidenceValidation validation = "CREATE_RESPONSE".equals(
                observationType)
                ? validateCreateEvidence(row, result)
                : validateQueryEvidence(row, result);
        if (!validation.trusted()) {
            markUnknown(row, result.channelState(), now);
            observeIssue(
                    command.sourceTaskAttemptId(), row,
                    "FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_EVIDENCE_MISMATCH",
                    "CRITICAL", validation.summary(), result, now);
            return blocked("authorization evidence mismatch: "
                    + validation.summary());
        }
        if ("CREATE_RESPONSE".equals(observationType)) {
            return mergeCreateResponse(command, row, result, now);
        }
        return mergeQueryResponse(command, row, result, now);
    }

    private ReliableFundsTaskExecutorPort.Result mergeCreateResponse(
            ReliableFundsTaskExecutorPort.Command command,
            AuthorizationRow row,
            AuthorizationResult result,
            LocalDateTime now) {
        if (!"CREATED".equals(row.localState())) {
            return done("late create response preserved");
        }
        LocalDateTime channelCreated = databaseTime(
                result.channelCreatedAt());
        LocalDateTime confirmationDeadline = channelCreated.plusHours(24);
        LocalDateTime packageExpiresAt = earlier(
                now.plusMinutes(10), confirmationDeadline);
        jdbc.update("""
                UPDATE fund_wechat_transfer_authorization
                SET local_state = 'WAIT_USER_CONFIRM',
                    channel_state = 'WAIT_USER_CONFIRM',
                    package_info = ?,
                    package_expires_at = ?,
                    last_api_error_code = NULL,
                    submitted_at = COALESCE(submitted_at, ?),
                    channel_created_at = ?,
                    confirmation_deadline_at = DATE_ADD(?, INTERVAL 24 HOUR),
                    channel_updated_at = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE id = ? AND local_state = 'CREATED'
                """, result.packageInfo(), packageExpiresAt,
                now, channelCreated,
                channelCreated, now, now, row.id());
        AuthorizationQueryTaskWakeResult wake = operationalControl
                .wakeMerchantTransferAuthorizationQuery(
                        row.tenantId(), row.organizationId(),
                        row.outAuthorizationNo(), now);
        if (wake == AuthorizationQueryTaskWakeResult.NOT_WAKEABLE) {
            observeIssue(
                    command.sourceTaskAttemptId(), row,
                    "FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_QUERY_RECOVERY_MISSING",
                    "CRITICAL", "QUERY_TASK_NOT_WAKEABLE_AFTER_CREATE",
                    result, now);
        }
        return done("authorization request accepted by WeChat");
    }

    private void wakeQueryAfterUncertainCreate(
            ReliableFundsTaskExecutorPort.Command command,
            AuthorizationRow row,
            AuthorizationResult result,
            LocalDateTime now,
            String missingReason) {
        AuthorizationQueryTaskWakeResult wake = operationalControl
                .wakeMerchantTransferAuthorizationQuery(
                        row.tenantId(), row.organizationId(),
                        row.outAuthorizationNo(), now);
        if (wake == AuthorizationQueryTaskWakeResult.NOT_WAKEABLE) {
            observeIssue(
                    command.sourceTaskAttemptId(), row,
                    "FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_QUERY_RECOVERY_MISSING",
                    "CRITICAL", missingReason, result, now);
        }
    }

    private ReliableFundsTaskExecutorPort.Result mergeQueryResponse(
            ReliableFundsTaskExecutorPort.Command command,
            AuthorizationRow row,
            AuthorizationResult result,
            LocalDateTime now) {
        operationalControl
                .completeMerchantTransferAuthorizationCreateFromQueryProof(
                        row.tenantId(), row.organizationId(),
                        row.outAuthorizationNo(), now);
        return switch (result.outcome()) {
            case WAIT_USER_CONFIRM -> mergeWaiting(
                    command, row, result, now);
            case ACTIVE -> mergeActive(command, row, result, now);
            case CLOSED -> mergeClosed(row, result, now);
            default -> throw new IllegalStateException(
                    "unexpected successful authorization outcome");
        };
    }

    private ReliableFundsTaskExecutorPort.Result mergeWaiting(
            ReliableFundsTaskExecutorPort.Command command,
            AuthorizationRow row,
            AuthorizationResult result,
            LocalDateTime now) {
        if (isTerminal(row.localState())) {
            return done("late waiting observation preserved");
        }
        if ("ACTIVE".equals(row.localState())) {
            markUnknown(row, result.channelState(), now);
            observeIssue(
                    command.sourceTaskAttemptId(), row,
                    "FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_STATE_REGRESSION",
                    "CRITICAL", "ACTIVE_REPORTED_WAIT_USER_CONFIRM",
                    result, now);
            return blocked(
                    "active authorization unexpectedly returned to waiting state");
        }
        LocalDateTime channelCreated = channelCreatedAt(row, result);
        LocalDateTime confirmationDeadline = channelCreated.plusHours(24);
        String packageInfo = blankToNull(result.packageInfo());
        LocalDateTime packageExpiresAt = null;
        if (packageInfo != null) {
            packageExpiresAt = earlier(
                    now.plusMinutes(10), confirmationDeadline);
        } else if (row.packageInfo() != null
                && row.packageExpiresAt() != null
                && now.isBefore(row.packageExpiresAt())) {
            packageInfo = row.packageInfo();
            packageExpiresAt = row.packageExpiresAt();
        }
        if (packageInfo == null || packageExpiresAt == null
                || !now.isBefore(packageExpiresAt)) {
            jdbc.update("""
                    UPDATE fund_wechat_transfer_authorization
                    SET local_state = 'UNKNOWN',
                        channel_state = 'WAIT_USER_CONFIRM',
                        package_info = NULL, package_expires_at = NULL,
                        last_api_error_code = NULL, close_reason = NULL,
                        state_conflict = 1,
                        submitted_at = COALESCE(submitted_at, ?),
                        channel_created_at = ?,
                        confirmation_deadline_at = DATE_ADD(?, INTERVAL 24 HOUR),
                        authorized_at = NULL, closed_at = NULL,
                        channel_updated_at = ?,
                        lock_version = lock_version + 1, updated_at = ?
                    WHERE id = ?
                    """, now, channelCreated, channelCreated,
                    now, now, row.id());
            resolveEvidenceMismatch(row, now);
            observeIssue(
                    command.sourceTaskAttemptId(), row,
                    "FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_QUERY_RECOVERY_MISSING",
                    "CRITICAL", "WAITING_DISPLAY_PACKAGE_UNRECOVERABLE",
                    result, now);
            AuthorizationRow updated = requiredById(row.id(), false);
            return waiting(
                    "authorization is waiting at WeChat, but the original "
                            + "display package is unavailable; terminal "
                            + "state polling continues",
                    queryDelay(updated, now));
        }
        jdbc.update("""
                UPDATE fund_wechat_transfer_authorization
                SET local_state = 'WAIT_USER_CONFIRM',
                    channel_state = 'WAIT_USER_CONFIRM',
                    package_info = ?, package_expires_at = ?,
                    authorization_id = NULL,
                    last_api_error_code = NULL, close_reason = NULL,
                    state_conflict = 0,
                    channel_created_at = ?,
                    confirmation_deadline_at = DATE_ADD(?, INTERVAL 24 HOUR),
                    authorized_at = NULL, closed_at = NULL,
                    channel_updated_at = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE id = ?
                """, packageInfo, packageExpiresAt,
                channelCreated, channelCreated,
                now, now, row.id());
        resolveEvidenceMismatch(row, now);
        AuthorizationRow updated = requiredById(row.id(), false);
        return waiting(
                "authorization remains waiting for user confirmation",
                queryDelay(updated, now));
    }

    private ReliableFundsTaskExecutorPort.Result mergeActive(
            ReliableFundsTaskExecutorPort.Command command,
            AuthorizationRow row,
            AuthorizationResult result,
            LocalDateTime now) {
        if (isTerminal(row.localState())) {
            handleTerminalConflict(
                    command.sourceTaskAttemptId(), row, result, now);
            return blocked(
                    "terminal authorization conflicts with active evidence");
        }
        if (authorizationIdOwnedByAnother(
                row.id(), result.authorizationId())) {
            markUnknown(row, result.channelState(), now);
            observeIssue(
                    command.sourceTaskAttemptId(), row,
                    "FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_EVIDENCE_MISMATCH",
                    "CRITICAL", "AUTHORIZATION_ID_ALREADY_OWNED",
                    result, now);
            return blocked("authorization ID belongs to another record");
        }
        LocalDateTime channelCreated = channelCreatedAt(row, result);
        jdbc.update("""
                UPDATE fund_wechat_transfer_authorization
                SET local_state = 'ACTIVE', channel_state = 'TAKING_EFFECT',
                    authorization_id = ?, package_info = NULL,
                    package_expires_at = NULL,
                    last_api_error_code = NULL, close_reason = NULL,
                    state_conflict = 0,
                    channel_created_at = ?,
                    confirmation_deadline_at = DATE_ADD(?, INTERVAL 24 HOUR),
                    authorized_at = COALESCE(authorized_at, ?),
                    closed_at = NULL, channel_updated_at = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE id = ?
                """, result.authorizationId(), channelCreated,
                channelCreated, databaseTime(result.authorizedAt()),
                now, now, row.id());
        resolveEvidenceMismatch(row, now);
        return waiting(
                "authorization is active; periodic closure query retained",
                Duration.ofDays(1));
    }

    private ReliableFundsTaskExecutorPort.Result mergeClosed(
            AuthorizationRow row,
            AuthorizationResult result,
            LocalDateTime now) {
        if ("CLOSED".equals(row.localState())) {
            return done("authorization is already closed");
        }
        if ("EXPIRED".equals(row.localState())) {
            return done("late closed observation preserved after expiry");
        }
        LocalDateTime channelCreated = channelCreatedAt(row, result);
        LocalDateTime closedAt = databaseTime(result.closedAt());
        jdbc.update("""
                UPDATE fund_wechat_transfer_authorization
                SET local_state = 'CLOSED', channel_state = 'CLOSED',
                    authorization_id = COALESCE(authorization_id, ?),
                    package_info = NULL, package_expires_at = NULL,
                    last_api_error_code = NULL,
                    close_reason = ?, state_conflict = 0,
                    channel_created_at = ?,
                    confirmation_deadline_at = DATE_ADD(?, INTERVAL 24 HOUR),
                    authorized_at = COALESCE(authorized_at, ?),
                    closed_at = ?, channel_updated_at = ?,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE id = ?
                """, result.authorizationId(), result.closeReason(),
                channelCreated, channelCreated,
                databaseTime(result.authorizedAt()), closedAt,
                now, now, row.id());
        resolveEvidenceMismatch(row, now);
        return done("authorization closed");
    }

    private void resolveEvidenceMismatch(
            AuthorizationRow row,
            LocalDateTime now) {
        operationalControl.resolveMerchantTransferAuthorizationEvidenceMismatch(
                row.tenantId(), row.organizationId(),
                row.outAuthorizationNo(), now);
    }

    private ReliableFundsTaskExecutorPort.Result mergeNotFound(
            ReliableFundsTaskExecutorPort.Command command,
            AuthorizationRow row,
            LocalDateTime now) {
        if ("CREATED".equals(row.localState())) {
            if ("INVALID_REQUEST".equals(row.lastErrorCode())) {
                jdbc.update("""
                        UPDATE fund_wechat_transfer_authorization
                        SET local_state = 'CREATE_REJECTED',
                            channel_state = NULL, authorization_id = NULL,
                            package_info = NULL,
                            package_expires_at = NULL,
                            submitted_at = COALESCE(submitted_at, ?),
                            channel_updated_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ? AND local_state = 'CREATED'
                        """, now, now, now, row.id());
                operationalControl
                        .completeMerchantTransferAuthorizationCreateFromQueryProof(
                                row.tenantId(), row.organizationId(),
                                row.outAuthorizationNo(), now);
                resolveEvidenceMismatch(row, now);
                return done(
                        "original authorization number was not accepted by WeChat");
            }
            return waiting(
                    "original authorization number is not visible yet",
                    Duration.ofSeconds(30));
        }
        if ("WAIT_USER_CONFIRM".equals(row.localState())
                && row.confirmationDeadlineAt() != null
                && !now.isBefore(
                row.confirmationDeadlineAt().plusDays(30))) {
            jdbc.update("""
                    UPDATE fund_wechat_transfer_authorization
                    SET local_state = 'EXPIRED', package_info = NULL,
                        package_expires_at = NULL,
                        last_api_error_code = 'NOT_FOUND',
                        close_reason =
                            'USER_OVERDUE_UNCONFIRMED_AFTER_RETENTION',
                        closed_at = ?, channel_updated_at = ?,
                        lock_version = lock_version + 1, updated_at = ?
                    WHERE id = ?
                    """, now, now, now, row.id());
            return done("unconfirmed authorization expired after retention");
        }
        if (isTerminal(row.localState())) {
            return done("late not-found observation preserved");
        }
        markUnknown(row, "NOT_FOUND", now);
        observeIssue(
                command.sourceTaskAttemptId(), row,
                "FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_EVIDENCE_MISMATCH",
                "CRITICAL", "NOT_FOUND_WITHIN_PROTECTED_LIFECYCLE",
                null, now);
        return blocked("authorization disappeared during protected lifecycle");
    }

    public boolean applyTrustedNotification(
            long sourceInboxId,
            long sourceTaskAttemptId,
            long trustedTenantId,
            long trustedOrganizationId,
            JsonNode payload) {
        String outAuthorizationNo = requiredText(
                payload, "out_authorization_no");
        AuthorizationRow known = requiredByOutNo(
                outAuthorizationNo, false);
        if (known.tenantId() != trustedTenantId
                || known.organizationId() != trustedOrganizationId) {
            throw new IllegalArgumentException(
                    "authorization notification scope mismatch");
        }
        Integer prior = jdbc.queryForObject("""
                SELECT COUNT(*)
                FROM fund_wechat_transfer_authorization_observation
                WHERE source_inbox_id = ?
                """, Integer.class, sourceInboxId);
        if (prior != null && prior > 0) return false;
        LocalDateTime now = databaseNow();
        AuthorizationRow row = requiredById(known.id(), true);
        appendInboxObservation(sourceInboxId, row, payload, now);
        String state = requiredText(payload, "state");
        EvidenceValidation validation = validateCallbackEvidence(row, payload);
        if (!validation.trusted()) {
            markUnknown(row, state, now);
            observeIssue(
                    sourceTaskAttemptId, row,
                    "FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_EVIDENCE_MISMATCH",
                    "CRITICAL", validation.summary(), null, now);
            return true;
        }
        if ("TAKING_EFFECT".equals(state)
                && isTerminal(row.localState())) {
            handleTerminalConflict(
                    sourceTaskAttemptId, row,
                    callbackActiveResult(row, payload, now), now);
        }
        if ("CLOSED".equals(state)
                && row.channelCreatedAt() != null
                && !"EXPIRED".equals(row.localState())) {
            String closeReason = requiredText(
                    payload.path("close_info"), "close_reason");
            LocalDateTime authorizedAt = databaseTime(parseInstant(
                    requiredText(payload, "authorize_time")));
            LocalDateTime closedAt = databaseTime(parseInstant(
                    requiredText(payload.path("close_info"), "close_time")));
            jdbc.update("""
                    UPDATE fund_wechat_transfer_authorization
                    SET local_state = 'CLOSED', channel_state = 'CLOSED',
                        authorization_id = COALESCE(authorization_id, ?),
                        package_info = NULL, package_expires_at = NULL,
                        close_reason = ?,
                        authorized_at = COALESCE(authorized_at, ?),
                        closed_at = ?,
                        last_api_error_code = NULL, channel_updated_at = ?,
                        lock_version = lock_version + 1, updated_at = ?
                    WHERE id = ?
                    """, requiredText(payload, "authorization_id"),
                    closeReason, authorizedAt, closedAt,
                    now, now, row.id());
        }
        AuthorizationRow queryTarget = findCurrentScope(
                row.merchantId(), row.miniappId(), row.subjectId(),
                row.sceneId(), false);
        if (queryTarget == null) queryTarget = row;
        AuthorizationQueryTaskWakeResult wake = operationalControl
                .wakeMerchantTransferAuthorizationQuery(
                        queryTarget.tenantId(), queryTarget.organizationId(),
                        queryTarget.outAuthorizationNo(), now);
        if (wake == AuthorizationQueryTaskWakeResult.NOT_WAKEABLE) {
            observeIssue(
                    sourceTaskAttemptId, row,
                    "FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_QUERY_RECOVERY_MISSING",
                    "CRITICAL", "QUERY_TASK_NOT_WAKEABLE", null, now);
        }
        return true;
    }

    private static AuthorizationResult callbackActiveResult(
            AuthorizationRow row,
            JsonNode payload,
            LocalDateTime observedAt) {
        return new AuthorizationResult(
                AuthorizationResult.Outcome.ACTIVE,
                "TAKING_EFFECT",
                requiredText(payload, "out_authorization_no"),
                requiredText(payload, "authorization_id"),
                requiredText(payload, "appid"),
                requiredText(payload, "openid"),
                row.sceneId(),
                requiredText(payload, "user_display_name"),
                row.userRecvPerception(),
                null, null,
                instant(row.channelCreatedAt()),
                parseInstant(requiredText(payload, "authorize_time")),
                null, null, "TRUSTED_CALLBACK", instant(observedAt));
    }

    /** 提现创建在绑定锁之后调用，取得一份不可变 ACTIVE 授权快照。 */
    public ActiveAuthorizationSnapshot lockCurrentActive(
            long tenantId,
            long organizationId,
            long organizationUserId,
            long wechatSubjectId,
            long merchantProfileId,
            long bindingId,
            long miniappId,
            String mchid,
            String appid,
            String openid,
            String sceneId) {
        AuthorizationRow row = findCurrentScope(
                merchantProfileId, miniappId, wechatSubjectId,
                sceneId, true);
        if (row == null) {
            throw authorizationRequired();
        }
        if ("UNKNOWN".equals(row.localState())) {
            throw authorizationUnresolved();
        }
        if (!"ACTIVE".equals(row.localState())
                || row.miniappId() != miniappId
                || row.subjectId() != wechatSubjectId
                || row.merchantId() != merchantProfileId
                || !row.mchid().equals(mchid)
                || !row.appid().equals(appid)
                || !row.openid().equals(openid)
                || row.authorizationId() == null) {
            throw authorizationRequired();
        }
        return snapshot(row);
    }

    /** 提交 worker 在渠道边界前按提现冻结快照精确复核。 */
    public boolean lockReferencedActive(
            long authorizationRowId,
            long tenantId,
            long organizationId,
            long organizationUserId,
            long merchantProfileId,
            long bindingId,
            long miniappId,
            String mchid,
            String appid,
            String openid,
            String outAuthorizationNo,
            String authorizationId) {
        AuthorizationRow row = requiredById(authorizationRowId, true);
        return "ACTIVE".equals(row.localState())
                && row.merchantId() == merchantProfileId
                && row.miniappId() == miniappId
                && row.mchid().equals(mchid)
                && row.appid().equals(appid)
                && row.openid().equals(openid)
                && row.outAuthorizationNo().equals(outAuthorizationNo)
                && Objects.equals(row.authorizationId(), authorizationId);
    }

    private void appendTaskObservation(
            ReliableFundsTaskExecutorPort.Command command,
            AuthorizationRow row,
            String type,
            AuthorizationResult result,
            LocalDateTime now) {
        String content = type + "|" + safe(result.channelState()) + "|"
                + safe(result.outAuthorizationNo()) + "|"
                + safe(result.authorizationId()) + "|"
                + safe(result.appid()) + "|" + safe(result.openid()) + "|"
                + safe(result.sceneId()) + "|" + safe(result.errorCode());
        jdbc.update("""
                INSERT INTO fund_wechat_transfer_authorization_observation (
                    observation_uid, tenant_id, organization_id,
                    transfer_authorization_id, observation_type,
                    evidence_source_kind, source_scope_kind,
                    source_inbox_id, source_task_attempt_id,
                    raw_channel_state, api_error_code, close_reason,
                    out_authorization_no, observed_out_authorization_no,
                    observed_authorization_id, observed_appid,
                    observed_openid, observed_scene_id,
                    observed_user_display_name,
                    observed_user_recv_perception, package_info,
                    observed_channel_created_at, observed_authorized_at,
                    observed_closed_at, content_sha256,
                    observed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, 'TASK_ATTEMPT', 'ORGANIZATION',
                          NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?)
                """, UUID.randomUUID().toString(), row.tenantId(),
                row.organizationId(), row.id(), type,
                command.sourceTaskAttemptId(), result.channelState(),
                result.errorCode(), result.closeReason(),
                row.outAuthorizationNo(), result.outAuthorizationNo(),
                result.authorizationId(), result.appid(), result.openid(),
                result.sceneId(), result.userDisplayName(),
                result.userRecvPerception(), result.packageInfo(),
                databaseTime(result.channelCreatedAt()),
                databaseTime(result.authorizedAt()),
                databaseTime(result.closedAt()),
                RechargeApplicationService.sha256(content), now, now);
    }

    private void appendInboxObservation(
            long sourceInboxId,
            AuthorizationRow row,
            JsonNode payload,
            LocalDateTime now) {
        JsonNode closeInfo = payload.path("close_info");
        jdbc.update("""
                INSERT INTO fund_wechat_transfer_authorization_observation (
                    observation_uid, tenant_id, organization_id,
                    transfer_authorization_id, observation_type,
                    evidence_source_kind, source_scope_kind,
                    source_inbox_id, source_task_attempt_id,
                    raw_channel_state, api_error_code, close_reason,
                    out_authorization_no, observed_out_authorization_no,
                    observed_authorization_id, observed_appid,
                    observed_openid, observed_scene_id,
                    observed_user_display_name,
                    observed_user_recv_perception, package_info,
                    observed_channel_created_at, observed_authorized_at,
                    observed_closed_at, content_sha256,
                    observed_at, created_at
                ) VALUES (?, ?, ?, ?, 'CALLBACK', 'INBOX', 'ORGANIZATION',
                          ?, NULL, ?, NULL, ?, ?, ?, ?, ?, ?, NULL, ?, NULL,
                          NULL, NULL, ?, ?, ?, ?, ?)
                """, UUID.randomUUID().toString(), row.tenantId(),
                row.organizationId(), row.id(), sourceInboxId,
                requiredText(payload, "state"),
                text(closeInfo, "close_reason"), row.outAuthorizationNo(),
                requiredText(payload, "out_authorization_no"),
                requiredText(payload, "authorization_id"),
                requiredText(payload, "appid"),
                requiredText(payload, "openid"),
                requiredText(payload, "user_display_name"),
                databaseTime(parseEvidenceInstant(
                        text(payload, "authorize_time"))),
                databaseTime(parseEvidenceInstant(
                        text(closeInfo, "close_time"))),
                RechargeApplicationService.sha256(payload.toString()),
                now, now);
    }

    private EvidenceValidation validateCreateEvidence(
            AuthorizationRow row,
            AuthorizationResult result) {
        ValidationBuilder validation = new ValidationBuilder();
        validation.equal(result.outAuthorizationNo(),
                row.outAuthorizationNo(), "OUT_AUTHORIZATION_NO");
        validation.equal(result.channelState(),
                "WAIT_USER_CONFIRM", "STATE");
        validation.present(result.packageInfo(), "PACKAGE_INFO");
        validation.present(result.channelCreatedAt(), "CREATE_TIME");
        return validation.result();
    }

    private EvidenceValidation validateQueryEvidence(
            AuthorizationRow row,
            AuthorizationResult result) {
        ValidationBuilder validation = new ValidationBuilder();
        validation.equal(result.outAuthorizationNo(),
                row.outAuthorizationNo(), "OUT_AUTHORIZATION_NO");
        validation.equal(result.appid(), row.appid(), "APPID");
        validation.equal(result.openid(), row.openid(), "OPENID");
        validation.equal(result.channelState(),
                expectedChannelState(result.outcome()), "STATE");
        validation.equalIfPresent(
                result.sceneId(), row.sceneId(), "SCENE_ID");
        validation.equal(result.userDisplayName(),
                row.userDisplayName(), "USER_DISPLAY_NAME");
        validation.equalIfPresent(result.userRecvPerception(),
                row.userRecvPerception(), "USER_RECV_PERCEPTION");
        // WeChat timestamps are optional diagnostic evidence. They are kept in
        // observations, but an estimated local fallback must not turn a later
        // authoritative timestamp into an identity/evidence mismatch.
        if (result.outcome() == AuthorizationResult.Outcome.ACTIVE) {
            validation.present(result.authorizationId(), "AUTHORIZATION_ID");
            validation.present(result.authorizedAt(), "AUTHORIZE_TIME");
        }
        if (result.outcome() == AuthorizationResult.Outcome.CLOSED) {
            validation.present(result.closeReason(), "CLOSE_REASON");
            validation.present(result.closedAt(), "CLOSE_TIME");
        }
        return validation.result();
    }

    private EvidenceValidation validateCallbackEvidence(
            AuthorizationRow row,
            JsonNode payload) {
        ValidationBuilder validation = new ValidationBuilder();
        validation.equal(text(payload, "out_authorization_no"),
                row.outAuthorizationNo(), "OUT_AUTHORIZATION_NO");
        validation.equal(text(payload, "appid"), row.appid(), "APPID");
        validation.equal(text(payload, "openid"), row.openid(), "OPENID");
        validation.equal(text(payload, "user_display_name"),
                row.userDisplayName(), "USER_DISPLAY_NAME");
        validation.present(text(payload, "authorization_id"),
                "AUTHORIZATION_ID");
        String state = text(payload, "state");
        if (!List.of("TAKING_EFFECT", "CLOSED").contains(state)) {
            validation.violation("STATE_UNKNOWN");
        }
        String authorizeTime = text(payload, "authorize_time");
        validation.present(authorizeTime, "AUTHORIZE_TIME");
        Instant parsedAuthorizeTime = parseEvidenceInstant(authorizeTime);
        if (authorizeTime != null && parsedAuthorizeTime == null) {
            validation.violation("AUTHORIZE_TIME_INVALID");
        }
        // A parseable provider timestamp is evidence, not an identity field;
        // repeated observations need not be byte-for-byte time-equal.
        if ("CLOSED".equals(state)) {
            validation.present(text(payload.path("close_info"),
                    "close_reason"), "CLOSE_REASON");
            validation.present(text(payload.path("close_info"),
                    "close_time"), "CLOSE_TIME");
            String closeTime = text(
                    payload.path("close_info"), "close_time");
            if (closeTime != null
                    && parseEvidenceInstant(closeTime) == null) {
                validation.violation("CLOSE_TIME_INVALID");
            }
        }
        if (row.authorizationId() != null) {
            validation.equal(text(payload, "authorization_id"),
                    row.authorizationId(), "AUTHORIZATION_ID");
        }
        return validation.result();
    }

    private void handleTerminalConflict(
            long sourceTaskAttemptId,
            AuthorizationRow terminal,
            AuthorizationResult result,
            LocalDateTime now) {
        AuthorizationRow newer = findCurrentScopeExcluding(
                terminal, true);
        if (newer == null) {
            jdbc.update("""
                    UPDATE fund_wechat_transfer_authorization
                    SET local_state = 'UNKNOWN', channel_state = ?,
                        package_info = NULL, package_expires_at = NULL,
                        state_conflict = 1,
                        lock_version = lock_version + 1, updated_at = ?
                    WHERE id = ?
                    """, result.channelState(), now, terminal.id());
        } else {
            jdbc.update("""
                    UPDATE fund_wechat_transfer_authorization
                    SET state_conflict = 1,
                        lock_version = lock_version + 1, updated_at = ?
                    WHERE id = ?
                    """, now, terminal.id());
            jdbc.update("""
                    UPDATE fund_wechat_transfer_authorization
                    SET local_state = 'UNKNOWN', state_conflict = 1,
                        package_info = NULL, package_expires_at = NULL,
                        lock_version = lock_version + 1, updated_at = ?
                    WHERE id = ?
                    """, now, newer.id());
        }
        observeIssue(
                sourceTaskAttemptId, terminal,
                "FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_TERMINAL_CONFLICT",
                "CRITICAL", "TERMINAL_AUTHORIZATION_REPORTED_ACTIVE",
                result, now);
    }

    private void observeIssue(
            long sourceTaskAttemptId,
            AuthorizationRow row,
            String issueCode,
            String severity,
            String reason,
            AuthorizationResult result,
            LocalDateTime now) {
        String state = result == null ? null : result.channelState();
        String error = result == null ? null : result.errorCode();
        String diagnostic = result == null
                ? "-" : redactChannelDiagnostic(row, result.diagnostic());
        String evidence = issueCode + "|" + row.outAuthorizationNo()
                + "|" + safe(state) + "|" + safe(error) + "|" + reason;
        operationalControl.observeReconciliationIssue(
                new ReconciliationIssue(
                        row.tenantId(), row.organizationId(),
                        sourceTaskAttemptId, issueCode, severity,
                        TARGET_TYPE, row.outAuthorizationNo(),
                        RechargeApplicationService.sha256(evidence),
                        "reason=" + reason + "; channelState="
                                + safe(state) + "; errorCode=" + safe(error)
                                + "; message=" + safe(diagnostic),
                        now));
    }

    private void markUnknown(
            AuthorizationRow row,
            String channelState,
            LocalDateTime now) {
        jdbc.update("""
                UPDATE fund_wechat_transfer_authorization
                SET local_state = 'UNKNOWN', channel_state = ?,
                    package_info = NULL, package_expires_at = NULL,
                    state_conflict = 1,
                    lock_version = lock_version + 1, updated_at = ?
                WHERE id = ?
                """, trimTo(channelState, 64), now, row.id());
    }

    private AuthorizationRequest originalRequest(AuthorizationRow row) {
        AuthorizationRequest request = new AuthorizationRequest(
                row.mchid(), row.outAuthorizationNo(), row.appid(),
                row.openid(), row.sceneId(), row.userDisplayName(),
                row.userRecvPerception(), row.notifyUrl());
        if (!MessageDigest.isEqual(
                row.notifyUrlHash(),
                RechargeApplicationService.sha256(request.notifyUrl()))
                || !MessageDigest.isEqual(
                row.requestHash(), requestHash(request))) {
            return null;
        }
        return request;
    }

    private static byte[] requestHash(AuthorizationRequest request) {
        String canonical = "MERCHANT_TRANSFER_AUTHORIZATION_REQUEST_V1|"
                + field(request.mchid()) + field(request.outAuthorizationNo())
                + field(request.appid()) + field(request.openid())
                + field(request.sceneId()) + field(request.userDisplayName())
                + nullableField(request.userRecvPerception())
                + HexFormat.of().formatHex(
                RechargeApplicationService.sha256(request.notifyUrl()));
        return RechargeApplicationService.sha256(canonical);
    }

    private static String field(String value) {
        return value.getBytes(StandardCharsets.UTF_8).length
                + ":" + value + "|";
    }

    private static String nullableField(String value) {
        return value == null ? "-1:|" : field(value);
    }

    private void registerTask(
            AuthorizationRow row,
            String taskType,
            LocalDateTime runAt) {
        String snapshot = "{\"outAuthorizationNo\":\""
                + row.outAuthorizationNo() + "\"}";
        tasks.register(new ReliableFundsTaskRegistration(
                row.tenantId(), row.organizationId(), taskType,
                taskType + ":" + row.outAuthorizationNo(), TARGET_TYPE,
                row.outAuthorizationNo(), 1, snapshot,
                RechargeApplicationService.sha256(snapshot), 20, runAt));
    }

    private AuthorizationRow replay(
            UUID operationUid,
            MiniappScope scope,
            String action) {
        SuccessfulAudit successful = audit.findSuccessful(operationUid)
                .orElse(null);
        if (successful == null) return null;
        boolean same = successful.actorKind()
                == AuditActorKind.ORGANIZATION_USER
                && Objects.equals(successful.organizationUserId(),
                scope.organizationUserId())
                && successful.scopeKind() == AuditScopeKind.ORGANIZATION
                && Objects.equals(successful.tenantId(), scope.tenantId())
                && Objects.equals(
                successful.organizationId(), scope.organizationId())
                && action.equals(successful.actionCode())
                && TARGET_TYPE.equals(successful.targetType());
        if (!same) throw idempotencyConflict();
        AuthorizationRow row = findByOutNo(
                successful.targetStableKey(), false);
        if (row == null
                || row.miniappId() != scope.miniappChannelId()
                || row.subjectId() != scope.wechatSubjectId()) {
            throw idempotencyConflict();
        }
        return row;
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
                    scope.actorDisplayName(), action, TARGET_TYPE, target,
                    "MINIAPP_USER", "SUCCEEDED", scope.sessionUid(),
                    null, summary, instant(now)));
        } catch (DuplicateKeyException duplicate) {
            AuthorizationRow replay = replay(operationUid, scope, action);
            if (replay == null || !replay.outAuthorizationNo().equals(target)) {
                throw idempotencyConflict();
            }
        }
    }

    private MerchantTransferAuthorizationView view(AuthorizationRow row) {
        if (row == null) {
            return new MerchantTransferAuthorizationView(
                    "NOT_OPENED", null, null, null, null,
                    false, null, null, null, null, null);
        }
        LocalDateTime now = databaseNow();
        boolean confirmable = "WAIT_USER_CONFIRM".equals(row.localState())
                && row.confirmationDeadlineAt() != null
                && now.isBefore(row.confirmationDeadlineAt())
                && row.packageInfo() != null
                && row.packageExpiresAt() != null
                && now.isBefore(row.packageExpiresAt());
        return new MerchantTransferAuthorizationView(
                publicStatus(row), row.outAuthorizationNo(),
                confirmable ? row.appid() : null,
                confirmable ? row.mchid() : null,
                confirmable ? row.packageInfo() : null,
                confirmable, instant(row.confirmationDeadlineAt()),
                instant(row.authorizedAt()), instant(row.closedAt()),
                row.closeReason(), lastSuccessfulQueryAt(row.id()));
    }

    private MerchantTransferAuthorizationAcceptedView accepted(
            AuthorizationRow row) {
        return new MerchantTransferAuthorizationAcceptedView(
                row.outAuthorizationNo(), publicStatus(row), STATUS_URL,
                "CREATED".equals(row.localState()) ? 1000 : 2000);
    }

    private Instant lastSuccessfulQueryAt(long authorizationId) {
        LocalDateTime value = jdbc.queryForObject("""
                SELECT MAX(observation.observed_at)
                FROM fund_wechat_transfer_authorization_observation observation
                JOIN ops_task_attempt attempt
                  ON attempt.id = observation.source_task_attempt_id
                 AND attempt.scope_kind = observation.source_scope_kind
                 AND attempt.tenant_id = observation.tenant_id
                 AND attempt.organization_id = observation.organization_id
                WHERE observation.transfer_authorization_id = ?
                  AND observation.observation_type = 'QUERY'
                  AND observation.api_error_code IS NULL
                  AND observation.raw_channel_state IN (
                    'WAIT_USER_CONFIRM', 'TAKING_EFFECT', 'CLOSED')
                  AND attempt.technical_result IN (
                    'NO_ACTION_REQUIRED', 'TECHNICAL_SUCCESS')
                """, LocalDateTime.class, authorizationId);
        return instant(value);
    }

    private AuthorizationBindingRow requiredBinding(
            MiniappScope scope,
            boolean lock) {
        AuthorizationBindingRow binding = findBinding(scope, lock);
        if (binding == null) {
            throw unavailable("机构小程序尚未完成系统商户绑定核验");
        }
        return binding;
    }

    private AuthorizationBindingRow findBinding(
            MiniappScope scope,
            boolean lock) {
        List<AuthorizationBindingRow> rows = jdbc.query("""
                SELECT b.id AS binding_id, b.miniapp_channel_id,
                       b.merchant_profile_id, b.appid, m.mchid, m.scene_id
                FROM fund_miniapp_merchant_binding b
                JOIN fund_wechat_merchant_profile m
                  ON m.id = b.merchant_profile_id
                JOIN iam_miniapp_channel app
                  ON app.id = b.miniapp_channel_id
                 AND app.appid = b.appid
                WHERE b.tenant_id = ? AND b.organization_id = ?
                  AND b.miniapp_channel_id = ? AND b.appid = ?
                  AND b.status = 'VERIFIED' AND m.status = 'ENABLED'
                  AND app.login_enabled = 1
                """ + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> new AuthorizationBindingRow(
                        rs.getLong("binding_id"),
                        rs.getLong("miniapp_channel_id"),
                        rs.getLong("merchant_profile_id"),
                        rs.getString("appid"), rs.getString("mchid"),
                        rs.getString("scene_id")),
                scope.tenantId(), scope.organizationId(),
                scope.miniappChannelId(), scope.appid());
        if (rows.size() > 1) {
            throw unavailable("机构小程序尚未完成系统商户绑定核验");
        }
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private UserRow requiredUser(MiniappScope scope, boolean lock) {
        return jdbc.queryForObject("""
                SELECT u.id, s.openid, u.phone_e164, u.status
                FROM iam_organization_user u
                JOIN iam_wechat_subject s
                  ON s.miniapp_channel_id = u.miniapp_channel_id
                 AND s.id = u.wechat_subject_id
                WHERE u.tenant_id = ? AND u.organization_id = ?
                  AND u.id = ? AND u.miniapp_channel_id = ?
                  AND u.wechat_subject_id = ?
                """ + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> new UserRow(
                        rs.getLong("id"), rs.getString("openid"),
                        rs.getString("phone_e164"), rs.getString("status")),
                scope.tenantId(), scope.organizationId(),
                scope.organizationUserId(), scope.miniappChannelId(),
                scope.wechatSubjectId());
    }

    private AuthorizationRow findLatestScope(
            long merchantId,
            long miniappChannelId,
            long wechatSubjectId,
            String sceneId,
            boolean lock) {
        List<AuthorizationRow> rows = jdbc.query(authorizationSql("""
                a.merchant_profile_id = ? AND a.miniapp_channel_id = ?
                AND a.wechat_subject_id = ? AND a.scene_id_snapshot = ?
                ORDER BY (a.current_authorization_slot IS NOT NULL) DESC,
                         a.created_at DESC, a.id DESC
                LIMIT 1
                """, lock), this::mapAuthorization,
                merchantId, miniappChannelId, wechatSubjectId, sceneId);
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private AuthorizationRow findCurrentScope(
            long merchantId,
            long miniappChannelId,
            long wechatSubjectId,
            String sceneId,
            boolean lock) {
        List<AuthorizationRow> rows = jdbc.query(authorizationSql("""
                a.merchant_profile_id = ? AND a.miniapp_channel_id = ?
                AND a.wechat_subject_id = ? AND a.scene_id_snapshot = ?
                AND a.current_authorization_slot = 1
                """, lock), this::mapAuthorization,
                merchantId, miniappChannelId, wechatSubjectId, sceneId);
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private AuthorizationRow findCurrentScopeExcluding(
            AuthorizationRow row,
            boolean lock) {
        List<AuthorizationRow> rows = jdbc.query(authorizationSql("""
                a.merchant_profile_id = ? AND a.miniapp_channel_id = ?
                AND a.wechat_subject_id = ? AND a.scene_id_snapshot = ?
                AND a.current_authorization_slot = 1 AND a.id <> ?
                """, lock), this::mapAuthorization,
                row.merchantId(), row.miniappId(), row.subjectId(),
                row.sceneId(), row.id());
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private AuthorizationRow requiredByOutNo(
            String outAuthorizationNo,
            boolean lock) {
        AuthorizationRow row = findByOutNo(outAuthorizationNo, lock);
        if (row == null) throw notFound();
        return row;
    }

    private AuthorizationRow findByOutNo(
            String outAuthorizationNo,
            boolean lock) {
        List<AuthorizationRow> rows = jdbc.query(authorizationSql(
                        "a.out_authorization_no = ?", lock),
                this::mapAuthorization, outAuthorizationNo);
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private AuthorizationRow requiredById(long id, boolean lock) {
        List<AuthorizationRow> rows = jdbc.query(authorizationSql(
                        "a.id = ?", lock),
                this::mapAuthorization, id);
        if (rows.size() != 1) throw notFound();
        return rows.getFirst();
    }

    private String authorizationSql(String predicate, boolean lock) {
        return """
                SELECT a.*
                FROM fund_wechat_transfer_authorization a
                WHERE %s
                %s
                """.formatted(predicate, lock ? "FOR UPDATE" : "");
    }

    private AuthorizationRow mapAuthorization(
            java.sql.ResultSet rs,
            int ignored) throws java.sql.SQLException {
        return new AuthorizationRow(
                rs.getLong("id"),
                UUID.fromString(rs.getString("authorization_uid")),
                rs.getLong("tenant_id"), rs.getLong("organization_id"),
                rs.getLong("organization_user_id"),
                rs.getLong("wechat_subject_id"),
                rs.getLong("merchant_profile_id"),
                rs.getLong("miniapp_merchant_binding_id"),
                rs.getLong("miniapp_channel_id"),
                rs.getString("out_authorization_no"),
                rs.getString("authorization_id"),
                rs.getString("mchid_snapshot"),
                rs.getString("appid_snapshot"),
                rs.getString("openid_snapshot"),
                rs.getString("scene_id_snapshot"),
                rs.getString("user_display_name_snapshot"),
                rs.getString("user_recv_perception_snapshot"),
                rs.getString("authorization_notify_url_snapshot"),
                rs.getBytes("notify_url_sha256"),
                rs.getBytes("request_sha256"),
                rs.getString("local_state"),
                rs.getString("channel_state"),
                rs.getString("package_info"),
                rs.getObject("package_expires_at", LocalDateTime.class),
                rs.getString("last_api_error_code"),
                rs.getString("close_reason"),
                rs.getBoolean("state_conflict"),
                rs.getObject("submitted_at", LocalDateTime.class),
                rs.getObject("channel_created_at", LocalDateTime.class),
                rs.getObject("confirmation_deadline_at", LocalDateTime.class),
                rs.getObject("authorized_at", LocalDateTime.class),
                rs.getObject("closed_at", LocalDateTime.class),
                rs.getObject("channel_updated_at", LocalDateTime.class),
                rs.getLong("lock_version"),
                rs.getObject("created_at", LocalDateTime.class),
                rs.getObject("updated_at", LocalDateTime.class));
    }

    private boolean authorizationIdOwnedByAnother(
            long rowId,
            String authorizationId) {
        if (authorizationId == null) return false;
        Integer count = jdbc.queryForObject("""
                SELECT COUNT(*)
                FROM fund_wechat_transfer_authorization
                WHERE authorization_id = ? AND id <> ?
                """, Integer.class, authorizationId, rowId);
        return count != null && count > 0;
    }

    private Duration retryDelay(long rowId) {
        Integer count = jdbc.queryForObject("""
                SELECT COUNT(*)
                FROM fund_wechat_transfer_authorization_observation o
                WHERE o.transfer_authorization_id = ?
                  AND o.api_error_code IS NOT NULL
                  AND o.id > COALESCE((
                    SELECT MAX(ok.id)
                    FROM fund_wechat_transfer_authorization_observation ok
                    JOIN ops_task_attempt attempt
                      ON attempt.id = ok.source_task_attempt_id
                     AND attempt.scope_kind = ok.source_scope_kind
                     AND attempt.tenant_id = ok.tenant_id
                     AND attempt.organization_id = ok.organization_id
                    WHERE ok.transfer_authorization_id = ?
                      AND ok.api_error_code IS NULL
                      AND ok.raw_channel_state IS NOT NULL
                      AND attempt.technical_result IN (
                        'NO_ACTION_REQUIRED', 'TECHNICAL_SUCCESS')
                  ), 0)
                """, Integer.class, rowId, rowId);
        int failures = count == null ? 1 : Math.max(1, count);
        if (failures == 1) return Duration.ofSeconds(30);
        if (failures == 2) return Duration.ofMinutes(2);
        if (failures == 3) return Duration.ofMinutes(10);
        return Duration.ofHours(1);
    }

    private static Duration queryDelay(
            AuthorizationRow row,
            LocalDateTime now) {
        if ("ACTIVE".equals(row.localState())) return Duration.ofDays(1);
        Duration age = Duration.between(row.createdAt(), now);
        if (age.compareTo(Duration.ofMinutes(10)) < 0) {
            return Duration.ofMinutes(1);
        }
        if (age.compareTo(Duration.ofHours(24)) < 0) {
            return Duration.ofMinutes(15);
        }
        return Duration.ofHours(6);
    }

    private static ActiveAuthorizationSnapshot snapshot(
            AuthorizationRow row) {
        return new ActiveAuthorizationSnapshot(
                row.id(), row.outAuthorizationNo(), row.authorizationId(),
                row.merchantId(), row.bindingId(), row.miniappId(),
                row.mchid(), row.appid(), row.openid(), row.sceneId());
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
    }

    private static String publicStatus(AuthorizationRow row) {
        return switch (row.localState()) {
            case "CREATED" -> "PREPARING";
            case "CREATE_REJECTED" -> "FAILED";
            default -> row.localState();
        };
    }

    private static boolean isDefinitiveCreateRejection(String code) {
        return code != null
                && DEFINITIVE_CREATE_REJECTION_CODES.contains(code);
    }

    private static boolean isTerminal(String state) {
        return "CREATE_REJECTED".equals(state)
                || "CLOSED".equals(state)
                || "EXPIRED".equals(state);
    }

    private static String safeUserDisplayName(UUID organizationUserUid) {
        String compactUid = Objects.requireNonNull(
                        organizationUserUid, "organizationUserUid")
                .toString().replace("-", "");
        return "JSBUser" + compactUid.substring(compactUid.length() - 16);
    }

    private static ReliableFundsTaskExecutorPort.Result done(
            String diagnostic) {
        return new ReliableFundsTaskExecutorPort.Result(
                ReliableFundsTaskExecutorPort.Result.Outcome.DONE,
                diagnostic);
    }

    private static ReliableFundsTaskExecutorPort.Result waiting(
            String diagnostic,
            Duration delay) {
        return new ReliableFundsTaskExecutorPort.Result(
                ReliableFundsTaskExecutorPort.Result.Outcome.WAITING,
                diagnostic, delay);
    }

    private static ReliableFundsTaskExecutorPort.Result blocked(
            String diagnostic) {
        return new ReliableFundsTaskExecutorPort.Result(
                ReliableFundsTaskExecutorPort.Result.Outcome.BLOCKED,
                diagnostic);
    }

    private static ReliableFundsTaskExecutorPort.Result channelResult(
            ReliableFundsTaskExecutorPort.Result.Outcome outcome,
            AuthorizationRow row,
            String summary,
            AuthorizationResult channelResult,
            Duration retryAfter) {
        String diagnostic = summary
                + "; httpStatus=" + safeNumber(channelResult.httpStatus())
                + "; errorCode=" + safe(channelResult.errorCode())
                + "; message=" + redactChannelDiagnostic(
                row, channelResult.diagnostic());
        return new ReliableFundsTaskExecutorPort.Result(
                outcome, diagnostic, retryAfter,
                channelResult.httpStatus(), channelResult.errorCode());
    }

    private static String redactChannelDiagnostic(
            AuthorizationRow row,
            String diagnostic) {
        if (diagnostic == null || diagnostic.isBlank()) return "-";
        String redacted = diagnostic.replaceAll("[\\r\\n\\t]+", " ")
                .trim();
        for (String sensitive : List.of(
                safeSensitive(row.mchid()),
                safeSensitive(row.appid()),
                safeSensitive(row.openid()),
                safeSensitive(row.outAuthorizationNo()),
                safeSensitive(row.authorizationId()),
                safeSensitive(row.packageInfo()),
                safeSensitive(row.notifyUrl()),
                safeSensitive(row.userDisplayName()),
                safeSensitive(row.userRecvPerception()))) {
            if (!sensitive.isEmpty()) {
                redacted = redacted.replace(sensitive, "[redacted]");
            }
        }
        return trimTo(redacted, 600);
    }

    private static String safeSensitive(String value) {
        return value == null ? "" : value;
    }

    private static String safeNumber(Integer value) {
        return value == null ? "-" : value.toString();
    }

    private static void requireUuidV4(UUID value) {
        if (value == null || value.version() != 4) {
            throw validation("Idempotency-Key 必须是 UUIDv4");
        }
    }

    private static TargetApiException authorizationRequired() {
        return new TargetApiException(
                422,
                "WITHDRAWAL.AUTO_COLLECTION_AUTHORIZATION_REQUIRED",
                "请先完成微信自动收款授权");
    }

    private static TargetApiException authorizationUnresolved() {
        return new TargetApiException(
                409,
                "WITHDRAWAL.AUTO_COLLECTION_AUTHORIZATION_UNRESOLVED",
                "微信自动收款授权状态正在核对，暂时不能提现");
    }

    private static TargetApiException unavailable(String message) {
        return new TargetApiException(
                422,
                "MERCHANT_TRANSFER_AUTHORIZATION.UNAVAILABLE",
                message);
    }

    private static TargetApiException stateConflict(String message) {
        return new TargetApiException(
                409, "COMMON.STATE_CONFLICT", message);
    }

    private static TargetApiException idempotencyConflict() {
        return new TargetApiException(
                409,
                "COMMON.IDEMPOTENCY_CONFLICT",
                "该 Idempotency-Key 已用于不同的自动收款授权操作");
    }

    private static TargetApiException validation(String message) {
        return new TargetApiException(
                400, "COMMON.VALIDATION_FAILED", message);
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404, "RESOURCE.NOT_FOUND", "自动收款授权不存在");
    }

    private static String requiredText(JsonNode node, String field) {
        String value = text(node, field);
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(
                    "authorization notification lacks " + field);
        }
        return value;
    }

    private static String text(JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        return value == null || value.isNull() ? null : value.asText();
    }

    private static String safe(String value) {
        return value == null || value.isBlank() ? "-" : value;
    }

    private static String trimTo(String value, int length) {
        if (value == null) return null;
        return value.length() <= length ? value : value.substring(0, length);
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }

    private static LocalDateTime earlier(
            LocalDateTime first,
            LocalDateTime second) {
        return first.isBefore(second) ? first : second;
    }

    private static String stripTrailingSlash(String value) {
        String result = value == null ? "" : value.trim();
        while (result.endsWith("/")) {
            result = result.substring(0, result.length() - 1);
        }
        return result;
    }

    private static Instant parseInstant(String value) {
        return java.time.OffsetDateTime.parse(value).toInstant();
    }

    private static Instant parseEvidenceInstant(String value) {
        if (value == null || value.isBlank()) return null;
        try {
            return parseInstant(value);
        } catch (RuntimeException invalid) {
            return null;
        }
    }

    private static LocalDateTime databaseTime(Instant value) {
        return value == null
                ? null : LocalDateTime.ofInstant(
                        value.truncatedTo(
                                java.time.temporal.ChronoUnit.MILLIS),
                        ZoneOffset.UTC);
    }

    private static LocalDateTime channelCreatedAt(
            AuthorizationRow row,
            AuthorizationResult result) {
        LocalDateTime observed = databaseTime(result.channelCreatedAt());
        if (observed != null) return observed;
        if (row.channelCreatedAt() != null) return row.channelCreatedAt();
        return row.createdAt();
    }

    private static String expectedChannelState(
            AuthorizationResult.Outcome outcome) {
        return switch (outcome) {
            case WAIT_USER_CONFIRM -> "WAIT_USER_CONFIRM";
            case ACTIVE -> "TAKING_EFFECT";
            case CLOSED -> "CLOSED";
            default -> null;
        };
    }

    private static Instant instant(LocalDateTime value) {
        return value == null ? null : value.toInstant(ZoneOffset.UTC);
    }

    public record ActiveAuthorizationSnapshot(
            long id,
            String outAuthorizationNo,
            String authorizationId,
            long merchantProfileId,
            long bindingId,
            long miniappId,
            String mchid,
            String appid,
            String openid,
            String sceneId) {
    }

    private record UserRow(
            long id,
            String openid,
            String phoneE164,
            String status) {
    }

    private record AuthorizationBindingRow(
            long bindingId,
            long miniappId,
            long merchantId,
            String appid,
            String mchid,
            String sceneId) {
    }

    private record AuthorizationRow(
            long id,
            UUID uid,
            long tenantId,
            long organizationId,
            long userId,
            long subjectId,
            long merchantId,
            long bindingId,
            long miniappId,
            String outAuthorizationNo,
            String authorizationId,
            String mchid,
            String appid,
            String openid,
            String sceneId,
            String userDisplayName,
            String userRecvPerception,
            String notifyUrl,
            byte[] notifyUrlHash,
            byte[] requestHash,
            String localState,
            String channelState,
            String packageInfo,
            LocalDateTime packageExpiresAt,
            String lastErrorCode,
            String closeReason,
            boolean stateConflict,
            LocalDateTime submittedAt,
            LocalDateTime channelCreatedAt,
            LocalDateTime confirmationDeadlineAt,
            LocalDateTime authorizedAt,
            LocalDateTime closedAt,
            LocalDateTime channelUpdatedAt,
            long version,
            LocalDateTime createdAt,
            LocalDateTime updatedAt) {
    }

    private record EvidenceValidation(List<String> violations) {

        private EvidenceValidation {
            violations = List.copyOf(violations);
        }

        boolean trusted() {
            return violations.isEmpty();
        }

        String summary() {
            return trusted() ? "MATCHED" : String.join(",", violations);
        }
    }

    private static final class ValidationBuilder {

        private final java.util.ArrayList<String> violations =
                new java.util.ArrayList<>();

        void equal(String actual, String expected, String field) {
            if (actual == null || actual.isBlank()) {
                violations.add(field + "_MISSING");
            } else if (!actual.equals(expected)) {
                violations.add(field + "_MISMATCH");
            }
        }

        void equalIfPresent(String actual, String expected, String field) {
            if (actual != null
                    && (actual.isBlank() || !actual.equals(expected))) {
                violations.add(field + "_MISMATCH");
            }
        }

        void present(Object value, String field) {
            if (value == null || value instanceof String text
                    && text.isBlank()) {
                violations.add(field + "_MISSING");
            }
        }

        void violation(String violation) {
            violations.add(violation);
        }

        EvidenceValidation result() {
            return new EvidenceValidation(violations);
        }
    }
}
