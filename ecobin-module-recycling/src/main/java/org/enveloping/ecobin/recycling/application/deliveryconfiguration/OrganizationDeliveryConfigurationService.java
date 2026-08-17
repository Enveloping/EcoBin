package org.enveloping.ecobin.recycling.application.deliveryconfiguration;

import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.enveloping.ecobin.framework.web.TargetWebAuditRequestContext;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.DeliveryScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.query.DeliveryScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeliveryScope;
import org.enveloping.ecobin.recycling.web.v1.DeliveryConfigurationModels
        .DeliveryConfigurationReleaseRequest;
import org.enveloping.ecobin.recycling.web.v1.DeliveryConfigurationModels
        .DeliveryConfigurationVersion;
import org.enveloping.ecobin.recycling.web.v1.DeliveryConfigurationModels
        .DeliveryConfigurationVersionPage;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Pattern;

@Service
public class OrganizationDeliveryConfigurationService {

    private static final String CAPABILITY =
            "delivery.configuration.manage";
    private static final Set<String> REVIEW_MODES = Set.of(
            "ALL_MANUAL",
            "NORMAL_AUTO_IMMEDIATE",
            "NORMAL_AUTO_AFTER_24H",
            "NORMAL_AUTO_AFTER_48H");
    private static final String ACTION =
            "delivery.configuration.release";
    private static final String TARGET_TYPE =
            "ORGANIZATION_DELIVERY_CONFIGURATION";
    private static final int SCHEMA_VERSION = 3;
    private static final int DEFAULT_LIMIT = 20;
    private static final int MAX_LIMIT = 100;
    private static final long MAX_VERSION = 9_007_199_254_740_991L;
    private static final Pattern MONEY_PATTERN =
            Pattern.compile("^-?(0|[1-9][0-9]*)\\.[0-9]{2}$");
    private static final Pattern NON_NEGATIVE_MONEY_PATTERN =
            Pattern.compile("^(0|[1-9][0-9]*)\\.[0-9]{2}$");
    private static final Pattern WEIGHT_PATTERN =
            Pattern.compile("^(0|[1-9][0-9]*)\\.[0-9]{3}$");

    private final DeliveryScopeAuthorizationPort authorization;
    private final OrganizationDeliveryConfigurationRepository repository;
    private final AuditPort audit;
    private final ObjectMapper objectMapper;
    private final Clock clock;

    @Autowired
    public OrganizationDeliveryConfigurationService(
            DeliveryScopeAuthorizationPort authorization,
            OrganizationDeliveryConfigurationRepository repository,
            AuditPort audit,
            ObjectMapper objectMapper) {
        this(
                authorization,
                repository,
                audit,
                objectMapper,
                Clock.systemUTC());
    }

    OrganizationDeliveryConfigurationService(
            DeliveryScopeAuthorizationPort authorization,
            OrganizationDeliveryConfigurationRepository repository,
            AuditPort audit,
            ObjectMapper objectMapper,
            Clock clock) {
        this.authorization = authorization;
        this.repository = repository;
        this.audit = audit;
        this.objectMapper = objectMapper;
        this.clock = clock;
    }

    @Transactional(readOnly = true)
    public DeliveryConfigurationVersion current(
            boolean platformPath,
            String tenantCode,
            String organizationCode) {
        AuthorizedDeliveryScope authorized = authorize(
                platformPath,
                tenantCode,
                organizationCode);
        return authorized.persistenceRef().withScopeOnce(
                (tenantId,
                 organizationId,
                 platformAdminId,
                 staffAccountId) -> repository.findCurrent(
                                new DeliveryConfigurationScope(
                                        tenantId,
                                        organizationId))
                        .map(this::view)
                        .orElseThrow(
                                OrganizationDeliveryConfigurationService
                                        ::configurationMissing));
    }

    @Transactional(readOnly = true)
    public DeliveryConfigurationVersionPage versions(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            Long beforeVersionNo,
            Integer requestedLimit) {
        if (beforeVersionNo != null && beforeVersionNo < 1) {
            throw invalidRequest(
                    "beforeVersionNo 必须是正整数");
        }
        int limit = requestedLimit == null
                ? DEFAULT_LIMIT
                : requestedLimit;
        if (limit < 1 || limit > MAX_LIMIT) {
            throw invalidRequest("limit 必须在 1 到 100 之间");
        }
        AuthorizedDeliveryScope authorized = authorize(
                platformPath,
                tenantCode,
                organizationCode);
        return authorized.persistenceRef().withScopeOnce(
                (tenantId,
                 organizationId,
                 platformAdminId,
                 staffAccountId) -> {
                    List<DeliveryConfigurationRow> rows =
                            repository.findVersions(
                                    new DeliveryConfigurationScope(
                                            tenantId,
                                            organizationId),
                                    beforeVersionNo,
                                    limit + 1);
                    boolean hasMore = rows.size() > limit;
                    List<DeliveryConfigurationVersion> items = rows.stream()
                            .limit(limit)
                            .map(this::view)
                            .toList();
                    Long next = hasMore
                            ? items.getLast().versionNo()
                            : null;
                    return new DeliveryConfigurationVersionPage(
                            items,
                            next);
                });
    }

    @Transactional(readOnly = true)
    public DeliveryConfigurationVersion version(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            long versionNo) {
        if (versionNo < 1) {
            throw notFound();
        }
        AuthorizedDeliveryScope authorized = authorize(
                platformPath,
                tenantCode,
                organizationCode);
        return authorized.persistenceRef().withScopeOnce(
                (tenantId,
                 organizationId,
                 platformAdminId,
                 staffAccountId) -> repository.findVersion(
                                new DeliveryConfigurationScope(
                                        tenantId,
                                        organizationId),
                                versionNo)
                        .map(this::view)
                        .orElseThrow(
                                OrganizationDeliveryConfigurationService
                                        ::notFound));
    }

    @Transactional(
            isolation = Isolation.READ_COMMITTED,
            readOnly = false)
    public DeliveryConfigurationVersion release(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            UUID operationUid,
            DeliveryConfigurationReleaseRequest request) {
        validateOperationUid(operationUid);
        NormalizedRelease normalized = normalize(request);
        AuthorizedDeliveryScope authorized = authorize(
                platformPath,
                tenantCode,
                organizationCode);
        TargetWebAuditRequestContext.describe(
                ACTION,
                authorized.organizationCode());
        String fingerprint = fingerprint(
                authorized,
                normalized);
        return authorized.persistenceRef().withScopeOnce(
                (tenantId,
                 organizationId,
                 platformAdminId,
                 staffAccountId) -> releaseInScope(
                        authorized,
                        new DeliveryConfigurationScope(
                                tenantId,
                                organizationId),
                        platformAdminId,
                        staffAccountId,
                        operationUid,
                        normalized,
                        fingerprint));
    }

    private DeliveryConfigurationVersion releaseInScope(
            AuthorizedDeliveryScope authorized,
            DeliveryConfigurationScope scope,
            Long platformAdminId,
            Long staffAccountId,
            UUID operationUid,
            NormalizedRelease request,
            String fingerprint) {
        Optional<SuccessfulAudit> previous =
                audit.findSuccessful(operationUid);
        if (previous.isPresent()) {
            return replay(
                    previous.orElseThrow(),
                    authorized,
                    scope,
                    platformAdminId,
                    staffAccountId,
                    fingerprint);
        }

        DeliveryConfigurationRow current = repository.lockCurrent(scope)
                .orElseThrow(
                        OrganizationDeliveryConfigurationService
                                ::configurationMissing);
        Optional<SuccessfulAudit> concurrentPrevious =
                audit.findSuccessful(operationUid);
        if (concurrentPrevious.isPresent()) {
            return replay(
                    concurrentPrevious.orElseThrow(),
                    authorized,
                    scope,
                    platformAdminId,
                    staffAccountId,
                    fingerprint);
        }
        if (current.versionNo() != request.expectedLatestVersion()) {
            throw new TargetApiException(
                    409,
                    "DELIVERY.CONFIGURATION_VERSION_CONFLICT",
                    "机构投递规则已经发生变化，请刷新后重新提交",
                    false,
                    Map.of("currentVersion", current.versionNo()));
        }

        byte[] contentSha256 = contentSha256(request);
        if (Arrays.equals(current.contentSha256(), contentSha256)) {
            throw new TargetApiException(
                    422,
                    "DELIVERY.CONFIGURATION_UNCHANGED",
                    "新投递规则与当前版本完全相同");
        }
        if (current.versionNo() >= MAX_VERSION) {
            throw new TargetApiException(
                    409,
                    "DELIVERY.CONFIGURATION_VERSION_EXHAUSTED",
                    "机构投递规则版本号已经达到系统上限");
        }

        long versionNo = current.versionNo() + 1;
        Instant publishedAt = clock.instant();
        LocalDateTime databasePublishedAt =
                LocalDateTime.ofInstant(
                        publishedAt,
                        ZoneOffset.UTC);
        String publicationSource = authorized.platformActor()
                ? "SYSTEM"
                : "STAFF";
        long configurationId = repository.insertVersion(
                scope,
                new NewDeliveryConfigurationVersion(
                        versionNo,
                        contentSha256,
                        request.reviewMode(),
                        request.automaticReviewMaxAmountCent(),
                        request.openBalanceFloorCent(),
                        request.maxReviewAbsoluteWeightGram(),
                        publicationSource,
                        staffAccountId,
                        databasePublishedAt));
        repository.switchCurrent(
                scope,
                current,
                configurationId,
                versionNo,
                databasePublishedAt);

        DeliveryConfigurationVersion response =
                new DeliveryConfigurationVersion(
                        versionNo,
                        HexFormat.of().formatHex(contentSha256),
                        request.reviewMode(),
                        nullableMoney(
                                request.automaticReviewMaxAmountCent()),
                        money(request.openBalanceFloorCent()),
                        weight(request.maxReviewAbsoluteWeightGram()),
                        publicationSource,
                        authorized.platformActor()
                                ? null
                                : authorized.principalUid(),
                        authorized.platformActor()
                                ? "系统或平台"
                                : authorized.actorDisplayName(),
                        publishedAt,
                        true);
        appendAudit(
                authorized,
                scope,
                platformAdminId,
                staffAccountId,
                operationUid,
                request,
                current,
                response,
                fingerprint);
        return response;
    }

    private AuthorizedDeliveryScope authorize(
            boolean platformPath,
            String tenantCode,
            String organizationCode) {
        AuthorizedDeliveryScope authorized = authorization.authorize(
                new DeliveryScopeAuthorizationQuery(
                        platformPath,
                        platformPath ? tenantCode : null,
                        organizationCode));
        if (!authorized.deliveryConfigurationManage()) {
            throw new TargetApiException(
                    403,
                    "AUTH.CAPABILITY_REQUIRED",
                    "当前账号缺少机构投递规则管理能力",
                    false,
                    Map.of("requiredCapability", CAPABILITY));
        }
        return authorized;
    }

    private NormalizedRelease normalize(
            DeliveryConfigurationReleaseRequest request) {
        if (request == null
                || request.expectedLatestVersion() == null) {
            throw invalidRequest("投递规则发布请求不能为空");
        }
        if (request.expectedLatestVersion() < 1
                || request.expectedLatestVersion() > MAX_VERSION) {
            throw invalidRequest(
                    "expectedLatestVersion 必须是有效正整数");
        }
        String reviewMode = required(request.reviewMode());
        if (!REVIEW_MODES.contains(reviewMode)) {
            throw new TargetApiException(
                    422,
                    "DELIVERY.REVIEW_MODE_INVALID",
                    "投递审核模式不受支持",
                    false,
                    Map.of(
                            "supportedReviewModes",
                            REVIEW_MODES.stream().sorted().toList()));
        }
        long floorCent = parseFloor(request.openBalanceFloorYuan());
        Long automaticReviewMaxAmountCent =
                parseAutomaticReviewMaxAmount(
                        reviewMode,
                        request.automaticReviewMaxAmountYuan());
        long maximumWeightGram = parseMaximumWeight(
                request.maxReviewAbsoluteWeightKg());
        String reason = trimToNull(request.reason());
        if (reason != null && reason.length() > 500) {
            throw invalidRequest("reason 最多允许 500 个字符");
        }
        return new NormalizedRelease(
                request.expectedLatestVersion(),
                reviewMode,
                automaticReviewMaxAmountCent,
                floorCent,
                maximumWeightGram,
                reason);
    }

    private static long parseFloor(String value) {
        String normalized = required(value);
        if (!MONEY_PATTERN.matcher(normalized).matches()) {
            throw invalidRequest(
                    "openBalanceFloorYuan 必须是精确到分的金额字符串");
        }
        try {
            long cents = new BigDecimal(normalized)
                    .movePointRight(2)
                    .longValueExact();
            if (cents >= 0) {
                throw new TargetApiException(
                        422,
                        "DELIVERY.OPEN_BALANCE_FLOOR_INVALID",
                        "负余额停投下限必须小于 0 元");
            }
            return cents;
        } catch (ArithmeticException exception) {
            throw invalidRequest(
                    "openBalanceFloorYuan 超出可保存范围");
        }
    }

    private static Long parseAutomaticReviewMaxAmount(
            String reviewMode,
            String value) {
        String normalized = trimToNull(value);
        if ("ALL_MANUAL".equals(reviewMode)) {
            if (normalized != null) {
                throw new TargetApiException(
                        422,
                        "DELIVERY.AUTO_REVIEW_AMOUNT_LIMIT_NOT_APPLICABLE",
                        "全部人工审核模式不能设置自动审核金额上限");
            }
            return null;
        }
        if (normalized == null) {
            throw new TargetApiException(
                    422,
                    "DELIVERY.AUTO_REVIEW_AMOUNT_LIMIT_REQUIRED",
                    "自动审核模式必须设置单笔结算金额上限");
        }
        if (!NON_NEGATIVE_MONEY_PATTERN.matcher(normalized).matches()) {
            throw invalidRequest(
                    "automaticReviewMaxAmountYuan 必须是精确到分的非负金额字符串");
        }
        try {
            return new BigDecimal(normalized)
                    .movePointRight(2)
                    .longValueExact();
        } catch (ArithmeticException exception) {
            throw invalidRequest(
                    "automaticReviewMaxAmountYuan 超出可保存范围");
        }
    }

    private static long parseMaximumWeight(String value) {
        String normalized = required(value);
        if (!WEIGHT_PATTERN.matcher(normalized).matches()) {
            throw invalidRequest(
                    "maxReviewAbsoluteWeightKg 必须是精确到克的非负重量字符串");
        }
        try {
            long grams = new BigDecimal(normalized)
                    .movePointRight(3)
                    .longValueExact();
            if (grams < 1 || grams > 1_000_000L) {
                throw new TargetApiException(
                        422,
                        "DELIVERY.MAX_REVIEW_WEIGHT_INVALID",
                        "人工认定重量上限必须在 0.001kg 到 1000.000kg 之间");
            }
            return grams;
        } catch (ArithmeticException exception) {
            throw invalidRequest(
                    "maxReviewAbsoluteWeightKg 超出可保存范围");
        }
    }

    private byte[] contentSha256(NormalizedRelease request) {
        String canonical = """
                {"automaticReviewMaxAmountCent":%s,"maxReviewAbsoluteWeightGram":%d,"openBalanceFloorCent":%d,"reviewMode":"%s","schemaVersion":%d}"""
                .formatted(
                        request.automaticReviewMaxAmountCent() == null
                                ? "null"
                                : request.automaticReviewMaxAmountCent(),
                        request.maxReviewAbsoluteWeightGram(),
                        request.openBalanceFloorCent(),
                        request.reviewMode(),
                        SCHEMA_VERSION);
        try {
            return MessageDigest.getInstance("SHA-256")
                    .digest(canonical.getBytes(StandardCharsets.UTF_8));
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
    }

    private String fingerprint(
            AuthorizedDeliveryScope authorized,
            NormalizedRelease request) {
        Map<String, Object> canonical = new LinkedHashMap<>();
        canonical.put("principalUid", authorized.principalUid());
        canonical.put("action", ACTION);
        canonical.put("tenantCode", authorized.tenantCode());
        canonical.put(
                "organizationCode",
                authorized.organizationCode());
        canonical.put(
                "expectedLatestVersion",
                request.expectedLatestVersion());
        canonical.put("reviewMode", request.reviewMode());
        canonical.put(
                "automaticReviewMaxAmountCent",
                request.automaticReviewMaxAmountCent());
        canonical.put(
                "openBalanceFloorCent",
                request.openBalanceFloorCent());
        canonical.put(
                "maxReviewAbsoluteWeightGram",
                request.maxReviewAbsoluteWeightGram());
        canonical.put("reason", request.reason());
        try {
            byte[] json = objectMapper.writeValueAsBytes(canonical);
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(json));
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "delivery configuration fingerprint failed",
                    exception);
        }
    }

    private void appendAudit(
            AuthorizedDeliveryScope authorized,
            DeliveryConfigurationScope scope,
            Long platformAdminId,
            Long staffAccountId,
            UUID operationUid,
            NormalizedRelease request,
            DeliveryConfigurationRow before,
            DeliveryConfigurationVersion response,
            String fingerprint) {
        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("fingerprint", fingerprint);
        Map<String, Object> beforeSummary = new LinkedHashMap<>();
        beforeSummary.put("versionNo", before.versionNo());
        beforeSummary.put("reviewMode", before.reviewMode());
        beforeSummary.put(
                "automaticReviewMaxAmountYuan",
                nullableMoney(before.automaticReviewMaxAmountCent()));
        beforeSummary.put(
                "openBalanceFloorYuan",
                money(before.openBalanceFloorCent()));
        beforeSummary.put(
                "maxReviewAbsoluteWeightKg",
                weight(before.maxReviewAbsoluteWeightGram()));
        summary.put("before", beforeSummary);
        summary.put("after", response);
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
                    platformAdminId,
                    staffAccountId,
                    null,
                    null,
                    authorized.actorDisplayName(),
                    ACTION,
                    TARGET_TYPE,
                    authorized.organizationCode(),
                    "WEB",
                    "SUCCEEDED",
                    authorized.sessionUid(),
                    request.reason(),
                    writeJson(summary),
                    response.publishedAt()));
        } catch (DuplicateKeyException exception) {
            throw idempotencyConflict();
        }
    }

    private DeliveryConfigurationVersion replay(
            SuccessfulAudit previous,
            AuthorizedDeliveryScope authorized,
            DeliveryConfigurationScope scope,
            Long platformAdminId,
            Long staffAccountId,
            String fingerprint) {
        JsonNode summary = readJson(
                previous.safeChangeSummaryJson());
        boolean sameActor = authorized.platformActor()
                ? previous.actorKind()
                == AuditActorKind.PLATFORM_ADMIN
                && Objects.equals(
                        previous.platformAdminId(),
                        platformAdminId)
                : previous.actorKind()
                == AuditActorKind.STAFF_ACCOUNT
                && Objects.equals(
                        previous.staffAccountId(),
                        staffAccountId);
        if (!sameActor
                || previous.scopeKind()
                != AuditScopeKind.ORGANIZATION
                || !Objects.equals(
                        previous.tenantId(),
                        scope.tenantId())
                || !Objects.equals(
                        previous.organizationId(),
                        scope.organizationId())
                || !ACTION.equals(previous.actionCode())
                || !TARGET_TYPE.equals(previous.targetType())
                || !authorized.organizationCode().equals(
                        previous.targetStableKey())
                || !fingerprint.equals(
                        summary.path("fingerprint").asText())) {
            throw idempotencyConflict();
        }
        try {
            DeliveryConfigurationVersion response =
                    objectMapper.treeToValue(
                            summary.path("response"),
                            DeliveryConfigurationVersion.class);
            if (response == null || !response.current()) {
                throw idempotencyConflict();
            }
            return response;
        } catch (TargetApiException exception) {
            throw exception;
        } catch (Exception exception) {
            throw idempotencyConflict();
        }
    }

    private DeliveryConfigurationVersion view(
            DeliveryConfigurationRow row) {
        return new DeliveryConfigurationVersion(
                row.versionNo(),
                HexFormat.of().formatHex(row.contentSha256()),
                row.reviewMode(),
                nullableMoney(row.automaticReviewMaxAmountCent()),
                money(row.openBalanceFloorCent()),
                weight(row.maxReviewAbsoluteWeightGram()),
                row.publicationSource(),
                row.publishedByStaffAccountUid(),
                row.publishedBy(),
                row.publishedAt().toInstant(ZoneOffset.UTC),
                row.current());
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "delivery configuration audit serialization failed",
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

    private static void validateOperationUid(UUID operationUid) {
        if (operationUid == null || operationUid.version() != 4) {
            throw invalidRequest(
                    "Idempotency-Key 必须是 UUIDv4");
        }
    }

    private static String required(String value) {
        if (value == null || value.isBlank()) {
            throw invalidRequest("投递规则字段不能为空");
        }
        return value.trim();
    }

    private static String trimToNull(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        return value.trim();
    }

    private static String money(long cents) {
        return BigDecimal.valueOf(cents, 2)
                .setScale(2, RoundingMode.UNNECESSARY)
                .toPlainString();
    }

    private static String nullableMoney(Long cents) {
        return cents == null ? null : money(cents);
    }

    private static String weight(long grams) {
        return BigDecimal.valueOf(grams, 3)
                .setScale(3, RoundingMode.UNNECESSARY)
                .toPlainString();
    }

    private static TargetApiException invalidRequest(String message) {
        return new TargetApiException(
                400,
                "COMMON.INVALID_REQUEST",
                message);
    }

    private static TargetApiException configurationMissing() {
        return new TargetApiException(
                409,
                "DELIVERY.CONFIGURATION_NOT_INITIALIZED",
                "机构还没有完整的当前投递规则");
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404,
                "RESOURCE.NOT_FOUND",
                "目标投递规则版本不存在");
    }

    private static TargetApiException idempotencyConflict() {
        return new TargetApiException(
                409,
                "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                "相同操作标识已经绑定到不同的投递规则发布请求");
    }

    private record NormalizedRelease(
            long expectedLatestVersion,
            String reviewMode,
            Long automaticReviewMaxAmountCent,
            long openBalanceFloorCent,
            long maxReviewAbsoluteWeightGram,
            String reason) {
    }
}
