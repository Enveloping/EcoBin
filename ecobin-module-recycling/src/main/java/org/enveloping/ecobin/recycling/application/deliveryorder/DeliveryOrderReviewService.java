package org.enveloping.ecobin.recycling.application.deliveryorder;

import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.enveloping.ecobin.framework.web.TargetWebAuditRequestContext;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.funds.api.command.ApplyDeliveryRevisionDeltaCommand;
import org.enveloping.ecobin.funds.api.port.ApplyDeliveryRevisionDeltaPort;
import org.enveloping.ecobin.funds.api.value.DeliveryRevisionKind;
import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletEntryOwnerRef;
import org.enveloping.ecobin.identity.api.port.DeliveryScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.port.DeliveryWalletEntryOwnerResolverPort;
import org.enveloping.ecobin.identity.api.query.DeliveryScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeliveryScope;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.DeliveryReviewResult;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.DeliveryReviewPreview;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.PreviewDeliveryReviewRequest;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.ReviewDeliveryOrderRequest;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;

/**
 * 投递订单首次审核和后续纠错的统一用例。
 *
 * <p>设备原始重量永不覆盖；每次认定都追加 revision（审核版本），并在同一事务内把
 * 新旧认定金额的差额交给 funds。首次审核把待审核金额转成正式钱包资金，后续纠错只补
 * 或扣差额，避免重复把整单金额入账。</p>
 */
@Service
public class DeliveryOrderReviewService {

    private static final String REVIEW_ACTION = "delivery.review";
    private static final String CORRECT_ACTION = "delivery.correct";
    private static final String TARGET_TYPE = "DELIVERY_ORDER";

    private final DeliveryScopeAuthorizationPort authorization;
    private final DeliveryWalletEntryOwnerResolverPort walletOwnerResolver;
    private final JdbcDeliveryOrderRepository repository;
    private final ApplyDeliveryRevisionDeltaPort funds;
    private final AuditPort audit;
    private final ObjectMapper objectMapper;
    private final Clock clock;

    @Autowired
    public DeliveryOrderReviewService(
            DeliveryScopeAuthorizationPort authorization,
            DeliveryWalletEntryOwnerResolverPort walletOwnerResolver,
            JdbcDeliveryOrderRepository repository,
            ApplyDeliveryRevisionDeltaPort funds,
            AuditPort audit,
            ObjectMapper objectMapper) {
        this(
                authorization,
                walletOwnerResolver,
                repository,
                funds,
                audit,
                objectMapper,
                Clock.systemUTC());
    }

    DeliveryOrderReviewService(
            DeliveryScopeAuthorizationPort authorization,
            DeliveryWalletEntryOwnerResolverPort walletOwnerResolver,
            JdbcDeliveryOrderRepository repository,
            ApplyDeliveryRevisionDeltaPort funds,
            AuditPort audit,
            ObjectMapper objectMapper,
            Clock clock) {
        this.authorization = authorization;
        this.walletOwnerResolver = walletOwnerResolver;
        this.repository = repository;
        this.funds = funds;
        this.audit = audit;
        this.objectMapper = objectMapper;
        this.clock = clock;
    }

    @Transactional(
            isolation = Isolation.READ_COMMITTED,
            readOnly = true)
    public DeliveryReviewPreview preview(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String deliveryOrderNo,
            PreviewDeliveryReviewRequest request) {
        AuthorizedDeliveryScope authorized = authorization.authorize(
                new DeliveryScopeAuthorizationQuery(
                        platformPath,
                        tenantCode,
                        organizationCode));
        String normalizedOrderNo = requiredOrderNo(deliveryOrderNo);
        NormalizedReviewRequest normalized = normalizeRequest(request);
        return authorized.persistenceRef().withScopeOnce(
                (tenantId,
                 organizationId,
                 platformAdminId,
                 staffAccountId) -> previewInScope(
                        authorized,
                        new DeliveryOrderScope(
                                tenantId,
                                organizationId,
                                null),
                        normalizedOrderNo,
                        normalized));
    }

    @Transactional(
            isolation = Isolation.READ_COMMITTED,
            readOnly = false)
    public DeliveryReviewResult review(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String deliveryOrderNo,
            UUID operationUid,
            ReviewDeliveryOrderRequest request) {
        return execute(
                Operation.INITIAL_REVIEW,
                platformPath,
                tenantCode,
                organizationCode,
                deliveryOrderNo,
                operationUid,
                request);
    }

    @Transactional(
            isolation = Isolation.READ_COMMITTED,
            readOnly = false)
    public DeliveryReviewResult correct(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String deliveryOrderNo,
            UUID operationUid,
            ReviewDeliveryOrderRequest request) {
        return execute(
                Operation.CORRECTION,
                platformPath,
                tenantCode,
                organizationCode,
                deliveryOrderNo,
                operationUid,
                request);
    }

    private DeliveryReviewPreview previewInScope(
            AuthorizedDeliveryScope authorized,
            DeliveryOrderScope scope,
            String deliveryOrderNo,
            NormalizedReviewRequest request) {
        // 预览只按当前订单版本计算结果，不写 revision、钱包或审计；真正提交仍会加锁重算。
        LockedDeliveryOrderRow order = repository.findOrder(
                        scope,
                        deliveryOrderNo)
                .orElseThrow(DeliveryOrderReviewService::notFound);
        Operation operation = operationForPreview(order);
        requireCapability(operation, authorized);
        requireExpectedRevision(order, request.expectedRevisionNo());
        requireCurrentState(
                operation,
                order,
                request.expectedRevisionNo());

        DeliveryReviewPolicy.ReviewValues values =
                DeliveryReviewPolicy.calculate(
                        order,
                        request.decision(),
                        request.finalWeightKg());
        long beforeAmountCent = order.finalAmountCent() == null
                ? 0L
                : order.finalAmountCent();
        long amountDeltaCent = amountDelta(
                values.finalAmountCent(),
                beforeAmountCent);
        return new DeliveryReviewPreview(
                deliveryOrderNo,
                operation.revisionKind,
                request.expectedRevisionNo(),
                values.decision(),
                decimal(values.finalWeightKg()),
                money(values.finalAmountCent()),
                money(amountDeltaCent),
                amountDeltaCent == 0 ? "NO_CHANGE" : "APPLIED",
                clock.instant().truncatedTo(ChronoUnit.MILLIS));
    }

    private DeliveryReviewResult execute(
            Operation operation,
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String deliveryOrderNo,
            UUID operationUid,
            ReviewDeliveryOrderRequest request) {
        AuthorizedDeliveryScope authorized = authorization.authorize(
                new DeliveryScopeAuthorizationQuery(
                        platformPath,
                        tenantCode,
                        organizationCode));
        requireCapability(operation, authorized);
        validateOperationUid(operationUid);
        String normalizedOrderNo = requiredOrderNo(deliveryOrderNo);
        NormalizedReviewRequest normalized =
                normalizeRequest(request);
        TargetWebAuditRequestContext.describe(
                operation.actionCode,
                normalizedOrderNo);
        Fingerprint fingerprint = fingerprint(
                operation,
                authorized,
                normalizedOrderNo,
                normalized);
        return authorized.persistenceRef().withScopeOnce(
                (tenantId,
                 organizationId,
                 platformAdminId,
                 staffAccountId) -> executeInScope(
                        operation,
                        authorized,
                        new DeliveryReviewScope(
                                tenantId,
                                organizationId,
                                platformAdminId,
                                staffAccountId),
                        normalizedOrderNo,
                        operationUid,
                        normalized,
                        fingerprint));
    }

    private DeliveryReviewResult executeInScope(
            Operation operation,
            AuthorizedDeliveryScope authorized,
            DeliveryReviewScope scope,
            String deliveryOrderNo,
            UUID operationUid,
            NormalizedReviewRequest request,
            Fingerprint fingerprint) {
        Optional<SuccessfulAudit> prior =
                audit.findSuccessful(operationUid);
        if (prior.isPresent()) {
            return replay(
                    prior.orElseThrow(),
                    operation,
                    authorized,
                    scope,
                    deliveryOrderNo,
                    fingerprint.hex());
        }

        DeliveryOrderScope orderScope = new DeliveryOrderScope(
                scope.tenantId(),
                scope.organizationId(),
                null);
        // 固定锁序先锁机构当前停投阈值，再锁订单，最后由 funds 锁钱包与提现。
        // 审核和纠错都遵循同一顺序，避免并发事务形成反向等待。
        long currentStopThresholdCent =
                repository.lockCurrentOpenBalanceFloor(orderScope);
        /*
         * Another request with the same key may have committed while this
         * transaction was waiting for the organization configuration lock.
         * READ_COMMITTED must re-read the audit fact here so the concurrent
         * duplicate receives the original success instead of a stale revision
         * conflict.
         */
        Optional<SuccessfulAudit> concurrentPrior =
                audit.findSuccessful(operationUid);
        if (concurrentPrior.isPresent()) {
            return replay(
                    concurrentPrior.orElseThrow(),
                    operation,
                    authorized,
                    scope,
                    deliveryOrderNo,
                    fingerprint.hex());
        }
        LockedDeliveryOrderRow order = repository.lockOrder(
                        orderScope,
                        deliveryOrderNo)
                .orElseThrow(
                        DeliveryOrderReviewService::notFound);
        requireCurrentState(operation, order, request.expectedRevisionNo());

        DeliveryReviewPolicy.ReviewValues values =
                DeliveryReviewPolicy.calculate(
                        order,
                        request.decision(),
                        request.finalWeightKg());
        long beforeAmountCent = order.finalAmountCent() == null
                ? 0L
                : order.finalAmountCent();
        long amountDeltaCent = amountDelta(
                values.finalAmountCent(),
                beforeAmountCent);
        DeliveryWalletEntryOwnerRef walletOwnerRef =
                amountDeltaCent == 0
                        ? null
                        : walletOwnerResolver.resolve(
                                TransactionBoundDeliveryWalletEntryOwnerRequestRef
                                        .issue(
                                                scope.tenantId(),
                                                scope.organizationId(),
                                                order.organizationUserId()));

        Instant reviewedAt = clock.instant()
                .truncatedTo(ChronoUnit.MILLIS);
        LocalDateTime reviewedAtDatabase =
                LocalDateTime.ofInstant(reviewedAt, ZoneOffset.UTC);
        UUID revisionUid = UUID.randomUUID();
        long revisionNo = Math.addExact(
                order.currentRevisionNo(),
                1L);
        DeliveryRevisionInsert revisionInsert =
                new DeliveryRevisionInsert(
                        revisionUid,
                        revisionNo,
                        order.currentRevisionId(),
                        order.currentRevisionId() == null
                                ? null
                                : order.currentRevisionNo(),
                        operation.revisionKind,
                        values.decision(),
                        order.finalWeightKg(),
                        order.finalAmountCent(),
                        values.finalWeightKg(),
                        values.finalAmountCent(),
                        amountDeltaCent,
                        authorized.platformActor()
                                ? "PLATFORM_ADMIN"
                                : "STAFF",
                        scope.platformAdminId(),
                        scope.staffAccountId(),
                        request.reason(),
                        fingerprint.digest(),
                        reviewedAtDatabase);
        // revision 只追加保存本次认定的前后值；订单主表仅指向“当前版本”，
        // 所以历史认定和设备原始证据都可以追溯。
        InsertedDeliveryRevision revision =
                repository.insertRevision(
                        orderScope,
                        order,
                        revisionInsert);
        repository.updateCurrentRevision(
                orderScope,
                order,
                revision,
                values.finalWeightKg(),
                values.finalAmountCent(),
                reviewedAtDatabase);

        String walletEffect = "NO_CHANGE";
        if (amountDeltaCent != 0) {
            // funds 使用 MANDATORY 加入当前事务。钱包写入失败时 revision 和订单更新也回滚，
            // 不会出现“订单已审核但余额未变化”或相反的半完成状态。
            funds.applyDeliveryRevisionDelta(
                    new ApplyDeliveryRevisionDeltaCommand(
                            deliveryOrderNo,
                            revisionUid,
                            Objects.requireNonNull(walletOwnerRef),
                            TransactionBoundDeliveryRevisionWalletEntryRef
                                    .issue(
                                            scope.tenantId(),
                                            scope.organizationId(),
                                            revision.id()),
                            operation.fundsRevisionKind,
                            amountDeltaCent,
                            currentStopThresholdCent,
                            reviewedAt));
            walletEffect = "APPLIED";
        }

        DeliveryReviewResult response =
                new DeliveryReviewResult(
                        deliveryOrderNo,
                        revisionUid,
                        revisionNo,
                        "APPROVED",
                        values.decision(),
                        decimal(values.finalWeightKg()),
                        money(values.finalAmountCent()),
                        money(amountDeltaCent),
                        walletEffect,
                        reviewedAt);
        appendAudit(
                operation,
                authorized,
                scope,
                order,
                operationUid,
                request,
                fingerprint.hex(),
                response);
        return response;
    }

    private void appendAudit(
            Operation operation,
            AuthorizedDeliveryScope authorized,
            DeliveryReviewScope scope,
            LockedDeliveryOrderRow order,
            UUID operationUid,
            NormalizedReviewRequest request,
            String fingerprint,
            DeliveryReviewResult response) {
        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("fingerprint", fingerprint);
        summary.put("before", Map.of(
                "reviewStatus", order.reviewStatus(),
                "revisionNo", order.currentRevisionNo(),
                "finalWeightKg",
                order.finalWeightKg() == null
                        ? ""
                        : decimal(order.finalWeightKg()),
                "finalAmountYuan",
                order.finalAmountCent() == null
                        ? ""
                        : money(order.finalAmountCent())));
        summary.put("after", Map.of(
                "reviewStatus", response.reviewStatus(),
                "revisionNo", response.revisionNo(),
                "finalWeightKg", response.finalWeightKg(),
                "finalAmountYuan", response.finalAmountYuan()));
        summary.put("response", response);
        summary.put("reasonPresent", request.reason() != null);
        try {
            audit.append(new AuditEntry(
                    UUID.randomUUID(),
                    UUID.randomUUID(),
                    operationUid,
                    AuditScopeKind.ORGANIZATION,
                    scope.tenantId(),
                    scope.organizationId(),
                    authorized.platformActor()
                            ? AuditActorKind.PLATFORM_ADMIN
                            : AuditActorKind.STAFF_ACCOUNT,
                    scope.platformAdminId(),
                    scope.staffAccountId(),
                    null,
                    null,
                    authorized.actorDisplayName(),
                    operation.actionCode,
                    TARGET_TYPE,
                    order.deliveryOrderNo(),
                    "WEB",
                    "SUCCEEDED",
                    authorized.sessionUid(),
                    request.reason(),
                    writeJson(summary),
                    response.reviewedAt()));
        } catch (DuplicateKeyException exception) {
            throw idempotencyConflict();
        }
    }

    private DeliveryReviewResult replay(
            SuccessfulAudit previous,
            Operation operation,
            AuthorizedDeliveryScope authorized,
            DeliveryReviewScope scope,
            String deliveryOrderNo,
            String fingerprint) {
        JsonNode summary = readJson(
                previous.safeChangeSummaryJson());
        boolean sameActor = authorized.platformActor()
                ? previous.actorKind()
                == AuditActorKind.PLATFORM_ADMIN
                && Objects.equals(
                previous.platformAdminId(),
                scope.platformAdminId())
                : previous.actorKind()
                == AuditActorKind.STAFF_ACCOUNT
                && Objects.equals(
                previous.staffAccountId(),
                scope.staffAccountId());
        if (!sameActor
                || previous.scopeKind()
                != AuditScopeKind.ORGANIZATION
                || !Objects.equals(
                previous.tenantId(),
                scope.tenantId())
                || !Objects.equals(
                previous.organizationId(),
                scope.organizationId())
                || !operation.actionCode.equals(
                previous.actionCode())
                || !TARGET_TYPE.equals(previous.targetType())
                || !deliveryOrderNo.equals(
                previous.targetStableKey())
                || !fingerprint.equals(
                summary.path("fingerprint").asText())) {
            throw idempotencyConflict();
        }
        try {
            DeliveryReviewResult response =
                    objectMapper.treeToValue(
                            summary.path("response"),
                            DeliveryReviewResult.class);
            if (response == null
                    || !deliveryOrderNo.equals(
                    response.deliveryOrderNo())) {
                throw idempotencyConflict();
            }
            return response;
        } catch (TargetApiException exception) {
            throw exception;
        } catch (Exception exception) {
            throw idempotencyConflict();
        }
    }

    private Fingerprint fingerprint(
            Operation operation,
            AuthorizedDeliveryScope authorized,
            String deliveryOrderNo,
            NormalizedReviewRequest request) {
        Map<String, Object> canonical = new LinkedHashMap<>();
        canonical.put("principalUid", authorized.principalUid());
        canonical.put("action", operation.actionCode);
        canonical.put("tenantCode", authorized.tenantCode());
        canonical.put(
                "organizationCode",
                authorized.organizationCode());
        canonical.put("deliveryOrderNo", deliveryOrderNo);
        canonical.put(
                "expectedRevisionNo",
                request.expectedRevisionNo());
        canonical.put("decision", request.decision());
        canonical.put("finalWeightKg", request.finalWeightKg());
        canonical.put("reason", request.reason());
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(objectMapper.writeValueAsBytes(canonical));
            return new Fingerprint(
                    digest,
                    HexFormat.of().formatHex(digest));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable",
                    exception);
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "delivery review fingerprint cannot be encoded",
                    exception);
        }
    }

    private static void requireCapability(
            Operation operation,
            AuthorizedDeliveryScope authorized) {
        boolean allowed = operation == Operation.INITIAL_REVIEW
                ? authorized.reviewExecute()
                : authorized.deliveryCorrect();
        if (!allowed) {
            throw new TargetApiException(
                    403,
                    "AUTH.CAPABILITY_REQUIRED",
                    operation == Operation.INITIAL_REVIEW
                            ? "当前账号没有投递审核权限"
                            : "当前账号没有投递纠错权限");
        }
    }

    private static void requireCurrentState(
            Operation operation,
            LockedDeliveryOrderRow order,
            long expectedRevisionNo) {
        if (operation == Operation.INITIAL_REVIEW
                && (!"PENDING".equals(order.reviewStatus())
                || order.currentRevisionNo() != 0)) {
            throw new TargetApiException(
                    409,
                    "DELIVERY.ORDER_ALREADY_APPROVED",
                    "只有待审核且尚无修订的订单可以首次审核");
        }
        if (operation == Operation.CORRECTION
                && !"APPROVED".equals(order.reviewStatus())) {
            throw new TargetApiException(
                    409,
                    "DELIVERY.ORDER_NOT_APPROVED",
                    "只有已经通过审核的订单可以追加纠错");
        }
        if (order.currentRevisionNo() != expectedRevisionNo) {
            throw new TargetApiException(
                    409,
                    "DELIVERY.REVISION_VERSION_CONFLICT",
                    "投递订单已被其他审核操作更新，请刷新后重试");
        }
    }

    private static Operation operationForPreview(
            LockedDeliveryOrderRow order) {
        return switch (order.reviewStatus()) {
            case "PENDING" -> Operation.INITIAL_REVIEW;
            case "APPROVED" -> Operation.CORRECTION;
            default -> throw new TargetApiException(
                    409,
                    "DELIVERY.REVIEW_STATE_CONFLICT",
                    "投递订单当前状态不允许审核预览");
        };
    }

    private static void requireExpectedRevision(
            LockedDeliveryOrderRow order,
            long expectedRevisionNo) {
        if (order.currentRevisionNo() != expectedRevisionNo) {
            throw new TargetApiException(
                    409,
                    "DELIVERY.REVISION_VERSION_CONFLICT",
                    "投递订单已被其他审核操作更新，请刷新后重试");
        }
    }

    private static NormalizedReviewRequest normalizeRequest(
            ReviewDeliveryOrderRequest request) {
        if (request == null || request.expectedRevisionNo() == null) {
            throw validation(
                    "expectedRevisionNo",
                    "expectedRevisionNo 不能为空");
        }
        if (request.expectedRevisionNo() < 0) {
            throw validation(
                    "expectedRevisionNo",
                    "expectedRevisionNo 不能为负数");
        }
        String decision = request.decision() == null
                ? null
                : request.decision().trim();
        if (decision == null
                || decision.isBlank()
                || decision.length() > 24) {
            throw validation(
                    "decision",
                    "decision 不能为空且长度不能超过 24");
        }
        String finalWeight = request.finalWeightKg();
        if (finalWeight != null && finalWeight.length() > 64) {
            throw validation(
                    "finalWeightKg",
                    "finalWeightKg 长度不能超过 64");
        }
        String reason = blankToNull(request.reason());
        if (reason != null && reason.length() > 500) {
            throw validation(
                    "reason",
                    "reason 长度不能超过 500");
        }
        return new NormalizedReviewRequest(
                request.expectedRevisionNo(),
                decision,
                finalWeight,
                reason);
    }

    private static NormalizedReviewRequest normalizeRequest(
            PreviewDeliveryReviewRequest request) {
        if (request == null) {
            throw validation(
                    "expectedRevisionNo",
                    "审核预览请求不能为空");
        }
        return normalizeRequest(new ReviewDeliveryOrderRequest(
                request.expectedRevisionNo(),
                request.decision(),
                request.finalWeightKg(),
                null));
    }

    private static long amountDelta(
            long finalAmountCent,
            long beforeAmountCent) {
        try {
            return Math.subtractExact(
                    finalAmountCent,
                    beforeAmountCent);
        } catch (ArithmeticException exception) {
            throw new TargetApiException(
                    422,
                    "DELIVERY.FINAL_AMOUNT_OUT_OF_RANGE",
                    "本次审核金额差额超出系统可精确保存的范围");
        }
    }

    private static String requiredOrderNo(String value) {
        if (value == null || value.isBlank()) {
            throw validation(
                    "deliveryOrderNo",
                    "deliveryOrderNo 不能为空");
        }
        return value.trim();
    }

    private static void validateOperationUid(UUID value) {
        if (value == null || value.version() != 4 || value.variant() != 2) {
            throw new TargetApiException(
                    400,
                    "COMMON.INVALID_IDEMPOTENCY_KEY",
                    "Idempotency-Key 必须是 UUIDv4");
        }
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "delivery review audit summary cannot be encoded",
                    exception);
        }
    }

    private JsonNode readJson(String value) {
        try {
            return objectMapper.readTree(value);
        } catch (Exception exception) {
            throw idempotencyConflict();
        }
    }

    private static String decimal(java.math.BigDecimal value) {
        return value.setScale(2).toPlainString();
    }

    private static String money(long amountCent) {
        return java.math.BigDecimal.valueOf(amountCent, 2)
                .toPlainString();
    }

    private static String blankToNull(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        return value.trim();
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404,
                "RESOURCE.NOT_FOUND",
                "投递订单不存在");
    }

    private static TargetApiException validation(
            String field,
            String message) {
        return new TargetApiException(
                400,
                "COMMON.VALIDATION_FAILED",
                "审核请求字段不符合接口契约",
                false,
                Map.of(field, message));
    }

    private static TargetApiException idempotencyConflict() {
        return new TargetApiException(
                409,
                "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                "该 Idempotency-Key 已用于另一项投递订单操作");
    }

    private enum Operation {
        INITIAL_REVIEW(
                REVIEW_ACTION,
                "INITIAL_REVIEW",
                DeliveryRevisionKind.INITIAL_REVIEW),
        CORRECTION(
                CORRECT_ACTION,
                "CORRECTION",
                DeliveryRevisionKind.CORRECTION);

        private final String actionCode;
        private final String revisionKind;
        private final DeliveryRevisionKind fundsRevisionKind;

        Operation(
                String actionCode,
                String revisionKind,
                DeliveryRevisionKind fundsRevisionKind) {
            this.actionCode = actionCode;
            this.revisionKind = revisionKind;
            this.fundsRevisionKind = fundsRevisionKind;
        }
    }

    private record DeliveryReviewScope(
            long tenantId,
            long organizationId,
            Long platformAdminId,
            Long staffAccountId) {
    }

    private record NormalizedReviewRequest(
            long expectedRevisionNo,
            String decision,
            String finalWeightKg,
            String reason) {
    }

    private record Fingerprint(byte[] digest, String hex) {

        private Fingerprint {
            digest = digest.clone();
        }

        @Override
        public byte[] digest() {
            return digest.clone();
        }
    }
}
