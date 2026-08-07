package org.enveloping.ecobin.recycling.application.deliveryorder;

import org.enveloping.ecobin.device.api.persistence.DeliveryOrderDeviceFactsRef;
import org.enveloping.ecobin.device.api.port.DeliveryOrderDeviceFactsQueryPort;
import org.enveloping.ecobin.device.api.port.DeliveryOrderDeviceFilterQueryPort;
import org.enveloping.ecobin.device.api.query.DeliveryOrderDeviceFilterQuery;
import org.enveloping.ecobin.device.api.result.DeliveryOrderDeviceFacts;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.persistence.DeliveryOrderIdentityBatchRef.ReviewerKind;
import org.enveloping.ecobin.identity.api.port.DeliveryOrderIdentityQueryPort;
import org.enveloping.ecobin.identity.api.port.DeliveryScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.port.MiniappDeliveryIdentityQueryPort;
import org.enveloping.ecobin.identity.api.query.DeliveryOrganizationUserFilterQuery;
import org.enveloping.ecobin.identity.api.query.DeliveryScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeliveryScope;
import org.enveloping.ecobin.identity.api.result.CurrentMiniappDeliveryIdentity;
import org.enveloping.ecobin.identity.api.result.DeliveryOrderIdentityFacts;
import org.enveloping.ecobin.identity.api.value.DeliveryIdentityFactToken;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.CursorPage;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.DeliveryOwnership;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.DeliveryPhoto;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.DeliveryRawFacts;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.DeliveryReviewProjection;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.DeliveryRevision;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.DeliveryRevisionOperator;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.DeliverySource;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.MiniappDeliveryAnomaly;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.MiniappDeliveryOrderDetail;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.MiniappDeliveryOrderItem;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.WebDeliveryAnomaly;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.WebDeliveryOrderDetail;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.WebDeliveryOrderItem;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;

@Service
public class DeliveryOrderQueryService {

    private static final int DEFAULT_LIMIT = 20;
    private static final int MAX_LIMIT = 100;
    private static final List<String> PHOTO_POSITIONS = List.of(
            "BEFORE_INNER",
            "BEFORE_OUTER",
            "AFTER_INNER",
            "AFTER_OUTER");

    private final MiniappDeliveryIdentityQueryPort miniappIdentity;
    private final DeliveryScopeAuthorizationPort authorization;
    private final DeliveryOrderIdentityQueryPort identityFacts;
    private final DeliveryOrderDeviceFactsQueryPort deviceFacts;
    private final DeliveryOrderDeviceFilterQueryPort deviceFilters;
    private final JdbcDeliveryOrderRepository repository;
    private final DeliveryOrderCursorCodec cursorCodec;
    private final ObjectMapper objectMapper;
    private final Clock clock;

    @Autowired
    public DeliveryOrderQueryService(
            MiniappDeliveryIdentityQueryPort miniappIdentity,
            DeliveryScopeAuthorizationPort authorization,
            DeliveryOrderIdentityQueryPort identityFacts,
            DeliveryOrderDeviceFactsQueryPort deviceFacts,
            DeliveryOrderDeviceFilterQueryPort deviceFilters,
            JdbcDeliveryOrderRepository repository,
            DeliveryOrderCursorCodec cursorCodec,
            ObjectMapper objectMapper) {
        this(
                miniappIdentity,
                authorization,
                identityFacts,
                deviceFacts,
                deviceFilters,
                repository,
                cursorCodec,
                objectMapper,
                Clock.systemUTC());
    }

    DeliveryOrderQueryService(
            MiniappDeliveryIdentityQueryPort miniappIdentity,
            DeliveryScopeAuthorizationPort authorization,
            DeliveryOrderIdentityQueryPort identityFacts,
            DeliveryOrderDeviceFactsQueryPort deviceFacts,
            DeliveryOrderDeviceFilterQueryPort deviceFilters,
            JdbcDeliveryOrderRepository repository,
            DeliveryOrderCursorCodec cursorCodec,
            ObjectMapper objectMapper,
            Clock clock) {
        this.miniappIdentity = miniappIdentity;
        this.authorization = authorization;
        this.identityFacts = identityFacts;
        this.deviceFacts = deviceFacts;
        this.deviceFilters = deviceFilters;
        this.repository = repository;
        this.cursorCodec = cursorCodec;
        this.objectMapper = objectMapper;
        this.clock = clock;
    }

    @Transactional(readOnly = true)
    public CursorPage<MiniappDeliveryOrderItem> miniappOrders(
            String cursor,
            Integer requestedLimit,
            String requestedReviewStatus) {
        int limit = normalizeLimit(requestedLimit);
        String reviewStatus =
                normalizeReviewStatus(requestedReviewStatus);
        CurrentMiniappDeliveryIdentity current =
                miniappIdentity.current();
        String fingerprint = filterFingerprint(Map.of(
                "channel", "MINIAPP",
                "tenantCode", current.tenantCode(),
                "organizationCode", current.organizationCode(),
                "organizationUserUid",
                current.organizationUserUid().value().toString(),
                "reviewStatus",
                nullMarker(reviewStatus),
                "limit", limit));
        PageAnchor anchor = decodeCursor(cursor, fingerprint);
        return current.deliveryQueryUserRef()
                .withDeliveryQueryUserOnce(
                        (tenantId,
                         organizationId,
                         organizationUserId,
                         ignoredPublicUid) -> {
                            DeliveryOrderScope scope =
                                    new DeliveryOrderScope(
                                            tenantId,
                                            organizationId,
                                            organizationUserId);
                            PageRows rows = pageRows(
                                    scope,
                                    anchor,
                                    fingerprint,
                                    limit,
                                    reviewStatus,
                                    null,
                                    null,
                                    null,
                                    null,
                                    null,
                                    null,
                                    null);
                            Map<Long, DeliveryOrderDeviceFacts> devices =
                                    resolveDeviceFacts(
                                            scope,
                                            rows.items());
                            List<MiniappDeliveryOrderItem> items =
                                    rows.items().stream()
                                            .map(row -> miniappItem(
                                                    row,
                                                    requiredDevice(
                                                            devices,
                                                            row.id())))
                                            .toList();
                            return new CursorPage<>(
                                    items,
                                    observedAt(),
                                    rows.nextCursor());
                        });
    }

    @Transactional(readOnly = true)
    public MiniappDeliveryOrderDetail miniappOrder(
            String deliveryOrderNo) {
        CurrentMiniappDeliveryIdentity current =
                miniappIdentity.current();
        String normalizedOrderNo = requiredOrderNo(deliveryOrderNo);
        return current.deliveryQueryUserRef()
                .withDeliveryQueryUserOnce(
                        (tenantId,
                         organizationId,
                         organizationUserId,
                         ignoredPublicUid) -> {
                            DeliveryOrderScope scope =
                                    new DeliveryOrderScope(
                                            tenantId,
                                            organizationId,
                                            organizationUserId);
                            DeliveryOrderRootRow root = repository
                                    .findDetail(scope, normalizedOrderNo)
                                    .orElseThrow(
                                            DeliveryOrderQueryService
                                                    ::notFound);
                            DeliveryOrderDeviceFacts device =
                                    resolveDeviceFact(scope, root);
                            List<DeliveryAnomalyRow> anomalies =
                                    repository.findAnomalies(root.id());
                            List<DeliveryPhotoRow> photos =
                                    repository.findPhotos(root.id());
                            requireFourPhotos(photos);
                            return new MiniappDeliveryOrderDetail(
                                    root.deliveryOrderNo(),
                                    source(root, device),
                                    raw(root),
                                    review(root),
                                    anomalies.stream()
                                            .map(DeliveryOrderQueryService
                                                    ::miniappAnomaly)
                                            .toList(),
                                    photos.stream()
                                            .map(photo -> photo(
                                                    photo,
                                                    false))
                                            .toList());
                        });
    }

    @Transactional(readOnly = true)
    public CursorPage<WebDeliveryOrderItem> webOrders(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String cursor,
            Integer requestedLimit,
            String requestedReviewStatus,
            Instant occurredFrom,
            Instant occurredTo,
            UUID organizationUserUid,
            String deviceCode,
            Integer portNo,
            String anomalyCode,
            String requestedPhotoCompleteness) {
        AuthorizedDeliveryScope authorized = authorization.authorize(
                new DeliveryScopeAuthorizationQuery(
                        platformPath,
                        tenantCode,
                        organizationCode));
        if (!authorized.deliveryRead()
                && !authorized.reviewExecute()) {
            throw new TargetApiException(
                    403,
                    "AUTH.CAPABILITY_REQUIRED",
                    "当前账号没有投递订单列表查询权限");
        }
        int limit = normalizeLimit(requestedLimit);
        String reviewStatus = authorized.deliveryRead()
                ? normalizeReviewStatus(requestedReviewStatus)
                : "PENDING";
        String photoCompleteness =
                normalizePhotoCompleteness(
                        requestedPhotoCompleteness);
        String normalizedAnomalyCode =
                blankToNull(anomalyCode);
        if (normalizedAnomalyCode != null
                && normalizedAnomalyCode.length() > 64) {
            throw validation(
                    "anomalyCode",
                    "anomalyCode 长度不能超过 64");
        }
        if (occurredFrom != null
                && occurredTo != null
                && !occurredFrom.isBefore(occurredTo)) {
            throw validation(
                    "occurredTo",
                    "occurredTo 必须晚于 occurredFrom");
        }
        String normalizedDeployment = blankToNull(deviceCode);
        if (normalizedDeployment != null
                && normalizedDeployment.length() > 64) {
            throw validation(
                    "deviceCode",
                    "deviceCode 长度不能超过 64");
        }
        if (portNo != null && (portNo < 1 || portNo > 6)) {
            throw validation(
                    "portNo",
                    "portNo 必须在 1 到 6 之间");
        }
        Map<String, Object> fingerprintFields =
                new LinkedHashMap<>();
        fingerprintFields.put("channel", "WEB");
        fingerprintFields.put(
                "principalUid",
                authorized.principalUid());
        fingerprintFields.put(
                "tenantCode",
                authorized.tenantCode());
        fingerprintFields.put(
                "organizationCode",
                authorized.organizationCode());
        fingerprintFields.put(
                "reviewStatus",
                nullMarker(reviewStatus));
        fingerprintFields.put(
                "occurredFrom",
                nullMarker(occurredFrom));
        fingerprintFields.put(
                "occurredTo",
                nullMarker(occurredTo));
        fingerprintFields.put(
                "organizationUserUid",
                nullMarker(organizationUserUid));
        fingerprintFields.put(
                "deviceCode",
                nullMarker(normalizedDeployment));
        fingerprintFields.put(
                "portNo",
                nullMarker(portNo));
        fingerprintFields.put(
                "anomalyCode",
                nullMarker(normalizedAnomalyCode));
        fingerprintFields.put(
                "photoCompleteness",
                nullMarker(photoCompleteness));
        fingerprintFields.put("limit", limit);
        String fingerprint =
                filterFingerprint(fingerprintFields);
        PageAnchor anchor = decodeCursor(cursor, fingerprint);

        Optional<ResolvedUserFilter> userFilter =
                resolveUserFilter(
                        authorized,
                        organizationUserUid);
        if (userFilter == null) {
            return emptyPage();
        }
        Optional<ResolvedDeviceFilter> deviceFilter =
                resolveDeviceFilter(
                        authorized,
                        normalizedDeployment,
                        portNo);
        if (deviceFilter == null) {
            return emptyPage();
        }
        ResolvedScope resolvedScope;
        if (deviceFilter.isPresent()) {
            ResolvedDeviceFilter device =
                    deviceFilter.orElseThrow();
            resolvedScope = new ResolvedScope(
                    device.tenantId(),
                    device.organizationId(),
                    device.assetId(),
                    device.portIds());
        } else {
            resolvedScope = authorized.persistenceRef()
                    .withScopeOnce(
                            (resolvedTenantId,
                             resolvedOrganizationId,
                             ignoredPlatformId,
                             ignoredStaffId) ->
                                    new ResolvedScope(
                                            resolvedTenantId,
                                            resolvedOrganizationId,
                                            null,
                                            List.of()));
        }
        Long userId = null;
        if (userFilter.isPresent()) {
            ResolvedUserFilter user =
                    userFilter.orElseThrow();
            requireSameScope(
                    resolvedScope.tenantId(),
                    resolvedScope.organizationId(),
                    user.tenantId(),
                    user.organizationId());
            userId = user.organizationUserId();
        }
        DeliveryOrderScope scope = new DeliveryOrderScope(
                resolvedScope.tenantId(),
                resolvedScope.organizationId(),
                null);
        PageRows rows = pageRows(
                scope,
                anchor,
                fingerprint,
                limit,
                reviewStatus,
                toDatabaseTime(occurredFrom),
                toDatabaseTime(occurredTo),
                userId,
                resolvedScope.assetId(),
                resolvedScope.portIds(),
                normalizedAnomalyCode,
                photoCompleteness);
        Map<Long, DeliveryOrderDeviceFacts> devices =
                resolveDeviceFacts(scope, rows.items());
        Map<Long, OrganizationUserUid> users =
                resolveOrganizationUsers(scope, rows.items());
        List<WebDeliveryOrderItem> items =
                rows.items().stream()
                        .map(row -> webItem(
                                row,
                                requiredUser(users, row.id()),
                                requiredDevice(devices, row.id())))
                        .toList();
        return new CursorPage<>(
                items,
                observedAt(),
                rows.nextCursor());
    }

    @Transactional(readOnly = true)
    public WebDeliveryOrderDetail webOrder(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            String deliveryOrderNo) {
        AuthorizedDeliveryScope authorized = authorization.authorize(
                new DeliveryScopeAuthorizationQuery(
                        platformPath,
                        tenantCode,
                        organizationCode));
        String normalizedOrderNo = requiredOrderNo(deliveryOrderNo);
        return authorized.persistenceRef().withScopeOnce(
                (tenantId,
                 organizationId,
                 ignoredPlatformId,
                 ignoredStaffId) -> {
                    DeliveryOrderScope scope =
                            new DeliveryOrderScope(
                                    tenantId,
                                    organizationId,
                                    null);
                    DeliveryOrderRootRow root = repository
                            .findDetail(scope, normalizedOrderNo)
                            .orElseThrow(
                                    DeliveryOrderQueryService::notFound);
                    DetailMode mode = detailMode(
                            authorized,
                            root.reviewStatus());
                    DeliveryOrderDeviceFacts device =
                            resolveDeviceFact(scope, root);
                    List<DeliveryAnomalyRow> anomalies =
                            repository.findAnomalies(root.id());
                    List<DeliveryPhotoRow> photos =
                            repository.findPhotos(root.id());
                    requireFourPhotos(photos);
                    List<DeliveryRevisionRow> revisions =
                            mode == DetailMode.FULL
                                    ? repository.findRevisions(root.id())
                                    : List.of();
                    ResolvedDetailIdentities identities =
                            resolveDetailIdentities(
                                    scope,
                                    root,
                                    revisions);
                    return new WebDeliveryOrderDetail(
                            root.deliveryOrderNo(),
                            source(root, device),
                            new DeliveryOwnership(
                                    identities.ownerUid().value()),
                            raw(root),
                            review(root),
                            anomalies.stream()
                                    .map(anomaly -> webAnomaly(
                                            anomaly,
                                            mode
                                                    != DetailMode
                                                    .CORRECTION_SAFE))
                                    .toList(),
                            photos.stream()
                                    .map(photo -> photo(photo, true))
                                    .toList(),
                            revisions.stream()
                                    .map(revision -> revision(
                                            revision,
                                            identities.reviewers()
                                                    .get(revision
                                                            .revisionNo())))
                                    .toList());
                });
    }

    private PageRows pageRows(
            DeliveryOrderScope scope,
            PageAnchor anchor,
            String fingerprint,
            int limit,
            String reviewStatus,
            LocalDateTime occurredFrom,
            LocalDateTime occurredTo,
            Long organizationUserId,
            Long assetId,
            List<Long> portIds,
            String anomalyCode,
            String photoCompleteness) {
        long highWatermark = anchor == null
                ? repository.currentHighWatermark(scope)
                : anchor.highWatermark();
        List<DeliveryOrderSummaryRow> fetched =
                repository.findPage(new DeliveryOrderPageQuery(
                        scope,
                        highWatermark,
                        anchor == null
                                ? null
                                : anchor.lastSortTime(),
                        anchor == null
                                ? null
                                : anchor.lastOrderNo(),
                        reviewStatus,
                        occurredFrom,
                        occurredTo,
                        organizationUserId,
                        assetId,
                        portIds,
                        anomalyCode,
                        photoCompleteness,
                        limit + 1));
        boolean hasMore = fetched.size() > limit;
        List<DeliveryOrderSummaryRow> items = hasMore
                ? List.copyOf(fetched.subList(0, limit))
                : List.copyOf(fetched);
        String nextCursor = null;
        if (hasMore && !items.isEmpty()) {
            DeliveryOrderSummaryRow last = items.getLast();
            nextCursor = cursorCodec.encode(
                    highWatermark,
                    last.sortOccurredAt(),
                    last.deliveryOrderNo(),
                    fingerprint);
        }
        return new PageRows(items, nextCursor);
    }

    private Optional<ResolvedUserFilter> resolveUserFilter(
            AuthorizedDeliveryScope authorized,
            UUID organizationUserUid) {
        if (organizationUserUid == null) {
            return Optional.empty();
        }
        var reference = identityFacts.resolveOrganizationUserFilter(
                new DeliveryOrganizationUserFilterQuery(
                        authorized.tenantCode(),
                        authorized.organizationCode(),
                        new OrganizationUserUid(
                                organizationUserUid)));
        if (reference.isEmpty()) {
            return null;
        }
        return Optional.of(reference.orElseThrow()
                .withOrganizationUserOnce(
                        ResolvedUserFilter::new));
    }

    private Optional<ResolvedDeviceFilter> resolveDeviceFilter(
            AuthorizedDeliveryScope authorized,
            String deviceCode,
            Integer portNo) {
        if (deviceCode == null && portNo == null) {
            return Optional.empty();
        }
        var reference = deviceFilters.resolveFilter(
                new DeliveryOrderDeviceFilterQuery(
                        deviceCode,
                        portNo,
                        authorized.persistenceRef()));
        if (reference.isEmpty()) {
            return null;
        }
        return Optional.of(reference.orElseThrow()
                .withFilterKeysOnce(
                        ResolvedDeviceFilter::new));
    }

    private Map<Long, DeliveryOrderDeviceFacts> resolveDeviceFacts(
            DeliveryOrderScope scope,
            List<DeliveryOrderSummaryRow> rows) {
        if (rows.isEmpty()) {
            return Map.of();
        }
        List<DeliveryOrderDeviceFactsRef.FactKey> keys =
                new ArrayList<>(rows.size());
        Map<String, Long> orderIds = new HashMap<>();
        for (DeliveryOrderSummaryRow row : rows) {
            String token = UUID.randomUUID().toString();
            orderIds.put(token, row.id());
            keys.add(new DeliveryOrderDeviceFactsRef.FactKey(
                    token,
                    row.assetId(),
                    row.portId(),
                    row.deliverySessionId(),
                    row.physicalResultId()));
        }
        return resolveDeviceFacts(
                scope,
                keys,
                orderIds);
    }

    private DeliveryOrderDeviceFacts resolveDeviceFact(
            DeliveryOrderScope scope,
            DeliveryOrderRootRow row) {
        String token = UUID.randomUUID().toString();
        Map<Long, DeliveryOrderDeviceFacts> resolved =
                resolveDeviceFacts(
                        scope,
                        List.of(new DeliveryOrderDeviceFactsRef.FactKey(
                                token,
                                row.assetId(),
                                row.portId(),
                                row.deliverySessionId(),
                                row.physicalResultId())),
                        Map.of(token, row.id()));
        return requiredDevice(resolved, row.id());
    }

    private Map<Long, DeliveryOrderDeviceFacts> resolveDeviceFacts(
            DeliveryOrderScope scope,
            List<DeliveryOrderDeviceFactsRef.FactKey> keys,
            Map<String, Long> orderIds) {
        var reference = TransactionBoundDeliveryOrderDeviceFactsRef.issue(
                new DeliveryOrderDeviceFactsRef.BatchKeys(
                        scope.tenantId(),
                        scope.organizationId(),
                        keys));
        List<DeliveryOrderDeviceFacts> facts =
                deviceFacts.facts(reference);
        Map<Long, DeliveryOrderDeviceFacts> result =
                new HashMap<>();
        for (DeliveryOrderDeviceFacts fact : facts) {
            Long orderId = orderIds.get(fact.token());
            if (orderId == null
                    || !fact.resolved()
                    || result.put(orderId, fact) != null) {
                throw new IllegalStateException(
                        "delivery order device facts are incomplete");
            }
        }
        if (result.size() != orderIds.size()) {
            throw new IllegalStateException(
                    "delivery order device facts are incomplete");
        }
        return Map.copyOf(result);
    }

    private Map<Long, OrganizationUserUid> resolveOrganizationUsers(
            DeliveryOrderScope scope,
            List<DeliveryOrderSummaryRow> rows) {
        if (rows.isEmpty()) {
            return Map.of();
        }
        List<TransactionBoundDeliveryOrderIdentityBatchRef
                .OrganizationUserEntry> entries =
                new ArrayList<>(rows.size());
        Map<DeliveryIdentityFactToken, Long> orderIds =
                new HashMap<>();
        for (DeliveryOrderSummaryRow row : rows) {
            DeliveryIdentityFactToken token =
                    DeliveryIdentityFactToken.create();
            orderIds.put(token, row.id());
            entries.add(new TransactionBoundDeliveryOrderIdentityBatchRef
                    .OrganizationUserEntry(
                    token,
                    scope.tenantId(),
                    scope.organizationId(),
                    row.organizationUserId()));
        }
        DeliveryOrderIdentityFacts resolved =
                identityFacts.resolveFacts(
                        TransactionBoundDeliveryOrderIdentityBatchRef.issue(
                                entries,
                                List.of()));
        Map<Long, OrganizationUserUid> result = new HashMap<>();
        resolved.organizationUsers().forEach((token, uid) -> {
            Long orderId = orderIds.get(token);
            if (orderId == null
                    || result.put(orderId, uid) != null) {
                throw new IllegalStateException(
                        "delivery order owner facts are inconsistent");
            }
        });
        if (result.size() != rows.size()) {
            throw new IllegalStateException(
                    "delivery order owner facts are incomplete");
        }
        return Map.copyOf(result);
    }

    private ResolvedDetailIdentities resolveDetailIdentities(
            DeliveryOrderScope scope,
            DeliveryOrderRootRow root,
            List<DeliveryRevisionRow> revisions) {
        DeliveryIdentityFactToken ownerToken =
                DeliveryIdentityFactToken.create();
        List<TransactionBoundDeliveryOrderIdentityBatchRef
                .ReviewerEntry> reviewerEntries =
                new ArrayList<>(revisions.size());
        Map<DeliveryIdentityFactToken, Long> revisionNumbers =
                new HashMap<>();
        for (DeliveryRevisionRow revision : revisions) {
            DeliveryIdentityFactToken token =
                    DeliveryIdentityFactToken.create();
            revisionNumbers.put(token, revision.revisionNo());
            reviewerEntries.add(
                    new TransactionBoundDeliveryOrderIdentityBatchRef
                            .ReviewerEntry(
                            token,
                            scope.tenantId(),
                            scope.organizationId(),
                            ReviewerKind.valueOf(
                                    revision.reviewerKind()),
                            revision.platformAdminId(),
                            revision.staffAccountId()));
        }
        var reference =
                TransactionBoundDeliveryOrderIdentityBatchRef.issue(
                        List.of(new TransactionBoundDeliveryOrderIdentityBatchRef
                                .OrganizationUserEntry(
                                ownerToken,
                                scope.tenantId(),
                                scope.organizationId(),
                                root.organizationUserId())),
                        reviewerEntries);
        DeliveryOrderIdentityFacts resolved =
                identityFacts.resolveFacts(reference);
        OrganizationUserUid owner =
                resolved.organizationUsers().get(ownerToken);
        if (owner == null) {
            throw new IllegalStateException(
                    "delivery order owner identity is incomplete");
        }
        Map<Long, DeliveryOrderIdentityFacts.ReviewerIdentity>
                reviewers = new HashMap<>();
        resolved.reviewers().forEach((token, identity) -> {
            Long revisionNo = revisionNumbers.get(token);
            if (revisionNo == null
                    || reviewers.put(revisionNo, identity) != null) {
                throw new IllegalStateException(
                        "delivery reviewer identities are inconsistent");
            }
        });
        if (reviewers.size() != revisions.size()) {
            throw new IllegalStateException(
                    "delivery reviewer identities are incomplete");
        }
        return new ResolvedDetailIdentities(
                owner,
                Map.copyOf(reviewers));
    }

    private static MiniappDeliveryOrderItem miniappItem(
            DeliveryOrderSummaryRow row,
            DeliveryOrderDeviceFacts device) {
        Reliability reliability = reliability(
                row.rawCalculationStatus(),
                row.netWeightInconsistent());
        return new MiniappDeliveryOrderItem(
                row.deliveryOrderNo(),
                device.deviceCode(),
                device.portNo(),
                instant(row.deviceOccurredAt()),
                instant(row.receivedAt()),
                reliability.weightReliable()
                        ? decimal(row.rawWeightKg())
                        : null,
                reliability.amountReliable()
                        ? money(row.rawAmountCent())
                        : null,
                reliability.weight(),
                reliability.amount(),
                row.reviewStatus(),
                row.currentRevisionNo(),
                decimal(row.finalWeightKg()),
                money(row.finalAmountCent()),
                row.anomalyCodes(),
                row.photoCompleteness());
    }

    private static WebDeliveryOrderItem webItem(
            DeliveryOrderSummaryRow row,
            OrganizationUserUid user,
            DeliveryOrderDeviceFacts device) {
        Reliability reliability = reliability(
                row.rawCalculationStatus(),
                row.netWeightInconsistent());
        return new WebDeliveryOrderItem(
                row.deliveryOrderNo(),
                user.value(),
                device.deviceCode(),
                device.portNo(),
                instant(row.deviceOccurredAt()),
                instant(row.receivedAt()),
                reliability.weightReliable()
                        ? decimal(row.rawWeightKg())
                        : null,
                reliability.amountReliable()
                        ? money(row.rawAmountCent())
                        : null,
                reliability.weight(),
                reliability.amount(),
                row.reviewStatus(),
                row.currentRevisionNo(),
                decimal(row.finalWeightKg()),
                money(row.finalAmountCent()),
                row.anomalyCodes(),
                row.photoCompleteness());
    }

    private static DeliverySource source(
            DeliveryOrderRootRow root,
            DeliveryOrderDeviceFacts device) {
        return new DeliverySource(
                device.eventUid(),
                device.sessionUid(),
                device.deviceCode(),
                device.portNo(),
                instant(root.deviceOccurredAt()),
                instant(root.receivedAt()));
    }

    private static DeliveryRawFacts raw(
            DeliveryOrderRootRow root) {
        Reliability reliability = reliability(
                root.rawCalculationStatus(),
                root.netWeightInconsistent());
        return new DeliveryRawFacts(
                root.initialWeightGram(),
                root.finalWeightGram(),
                root.rawNetWeightGram(),
                reliability.weightReliable()
                        ? decimal(root.rawWeightKg())
                        : null,
                root.unitPriceYuanPerKg() == null
                        ? null
                        : root.unitPriceYuanPerKg()
                        .setScale(4)
                        .toPlainString(),
                reliability.amountReliable()
                        ? money(root.rawAmountCent())
                        : null,
                reliability.weight(),
                reliability.amount(),
                root.negativeWeightAnomaly());
    }

    private static DeliveryReviewProjection review(
            DeliveryOrderRootRow root) {
        return new DeliveryReviewProjection(
                root.reviewStatus(),
                root.currentRevisionNo(),
                BigDecimal.valueOf(
                                root.maxReviewAbsWeightGram(),
                                3)
                        .setScale(2, RoundingMode.DOWN)
                        .toPlainString(),
                decimal(root.finalWeightKg()),
                money(root.finalAmountCent()),
                instant(root.firstApprovedAt()));
    }

    private static MiniappDeliveryAnomaly miniappAnomaly(
            DeliveryAnomalyRow anomaly) {
        return new MiniappDeliveryAnomaly(
                anomaly.category(),
                anomaly.code(),
                instant(anomaly.detectedAt()),
                anomalyMessage(anomaly.code()));
    }

    private WebDeliveryAnomaly webAnomaly(
            DeliveryAnomalyRow anomaly,
            boolean includeDiagnostic) {
        return new WebDeliveryAnomaly(
                anomaly.category(),
                anomaly.code(),
                instant(anomaly.detectedAt()),
                anomalyMessage(anomaly.code()),
                includeDiagnostic
                        ? diagnostic(anomaly.diagnosticJson())
                        : null);
    }

    private static DeliveryPhoto photo(
            DeliveryPhotoRow photo,
            boolean web) {
        return new DeliveryPhoto(
                photo.position(),
                photo.status(),
                "AVAILABLE".equals(photo.status())
                        ? photo.url()
                        : null,
                instant(photo.capturedAt()),
                "PERMANENTLY_MISSING".equals(photo.status())
                        ? web
                        ? safeWebMissingReason(
                                photo.missingReason())
                        : "PHOTO_UNAVAILABLE"
                        : null);
    }

    private static DeliveryRevision revision(
            DeliveryRevisionRow revision,
            DeliveryOrderIdentityFacts.ReviewerIdentity identity) {
        if (identity == null) {
            throw new IllegalStateException(
                    "delivery reviewer identity is missing");
        }
        return new DeliveryRevision(
                revision.revisionUid(),
                revision.revisionNo(),
                revision.revisionType(),
                revision.decision(),
                decimal(revision.beforeWeightKg()),
                money(revision.beforeAmountCent()),
                decimal(revision.afterWeightKg()),
                money(revision.afterAmountCent()),
                money(revision.amountDeltaCent()),
                revision.reason(),
                new DeliveryRevisionOperator(
                        identity.actorKind().name(),
                        identity.actorUid().value(),
                        identity.displayName()),
                instant(revision.reviewedAt()));
    }

    private Map<String, Object> diagnostic(String json) {
        if (json == null || json.isBlank()) {
            return null;
        }
        try {
            @SuppressWarnings("unchecked")
            Map<String, Object> result =
                    objectMapper.readValue(json, Map.class);
            return Collections.unmodifiableMap(
                    new LinkedHashMap<>(result));
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "delivery anomaly diagnostic is invalid",
                    exception);
        }
    }

    private static Reliability reliability(
            String rawCalculationStatus,
            boolean netWeightInconsistent) {
        String weight;
        if (netWeightInconsistent) {
            weight = "INCONSISTENT";
        } else {
            weight = switch (rawCalculationStatus) {
                case "RELIABLE" -> "RELIABLE";
                case "INVALID" -> "INVALID";
                case "UNAVAILABLE" -> "MISSING";
                default -> throw new IllegalStateException(
                        "unsupported raw calculation status");
            };
        }
        String amount = "RELIABLE".equals(weight)
                ? "RELIABLE"
                : "WEIGHT_UNRELIABLE";
        return new Reliability(weight, amount);
    }

    private static String anomalyMessage(String code) {
        return switch (code) {
            case "NEGATIVE_WEIGHT_ANOMALY" ->
                    "投递过程中检测到达到阈值的重量减少，等待人工确认";
            case "DELIVERY_NET_WEIGHT_MISMATCH" ->
                    "设备上报的净重量与后端计算结果不一致，等待人工确认";
            case "RAW_WEIGHT_OUT_OF_RANGE" ->
                    "原始重量超出该订单的审核范围，需要人工填写最终重量";
            default -> "该投递存在需要人工关注的异常事实";
        };
    }

    private static String safeWebMissingReason(String reason) {
        return switch (Objects.toString(reason, "")) {
            case "DEVICE_DID_NOT_PRODUCE_PHOTO",
                 "PHOTO_CAPTURE_FAILED",
                 "UPLOAD_FAILED_PERMANENTLY" -> reason;
            default -> "PHOTO_UNAVAILABLE";
        };
    }

    private static DetailMode detailMode(
            AuthorizedDeliveryScope authorized,
            String reviewStatus) {
        if (authorized.deliveryRead()) {
            return DetailMode.FULL;
        }
        if (authorized.reviewExecute()
                && "PENDING".equals(reviewStatus)) {
            return DetailMode.REVIEW_SAFE;
        }
        if (authorized.deliveryCorrect()
                && "APPROVED".equals(reviewStatus)) {
            return DetailMode.CORRECTION_SAFE;
        }
        throw notFound();
    }

    private static void requireFourPhotos(
            List<DeliveryPhotoRow> photos) {
        if (photos.size() != PHOTO_POSITIONS.size()
                || !photos.stream()
                .map(DeliveryPhotoRow::position)
                .toList()
                .equals(PHOTO_POSITIONS)) {
            throw new IllegalStateException(
                    "delivery order photo slots are incomplete");
        }
    }

    private static DeliveryOrderDeviceFacts requiredDevice(
            Map<Long, DeliveryOrderDeviceFacts> devices,
            long orderId) {
        DeliveryOrderDeviceFacts result = devices.get(orderId);
        if (result == null) {
            throw new IllegalStateException(
                    "delivery order device facts are missing");
        }
        return result;
    }

    private static OrganizationUserUid requiredUser(
            Map<Long, OrganizationUserUid> users,
            long orderId) {
        OrganizationUserUid result = users.get(orderId);
        if (result == null) {
            throw new IllegalStateException(
                    "delivery order owner facts are missing");
        }
        return result;
    }

    private static void requireSameScope(
            long expectedTenantId,
            long expectedOrganizationId,
            long actualTenantId,
            long actualOrganizationId) {
        if (expectedTenantId != actualTenantId
                || expectedOrganizationId != actualOrganizationId) {
            throw new IllegalStateException(
                    "delivery order filter scopes do not match");
        }
    }

    private CursorPage<WebDeliveryOrderItem> emptyPage() {
        return new CursorPage<>(
                List.of(),
                observedAt(),
                null);
    }

    private Instant observedAt() {
        return clock.instant().truncatedTo(ChronoUnit.MILLIS);
    }

    private PageAnchor decodeCursor(
            String cursor,
            String fingerprint) {
        if (cursor == null || cursor.isBlank()) {
            return null;
        }
        DeliveryOrderCursorCodec.DecodedCursor decoded =
                cursorCodec.decode(cursor, fingerprint);
        return new PageAnchor(
                decoded.highWatermark(),
                decoded.lastSortTime(),
                decoded.lastOrderNo());
    }

    private String filterFingerprint(Map<String, ?> fields) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(objectMapper.writeValueAsBytes(fields));
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable",
                    exception);
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "delivery order filters cannot be encoded",
                    exception);
        }
    }

    private static int normalizeLimit(Integer value) {
        int limit = value == null ? DEFAULT_LIMIT : value;
        if (limit < 1 || limit > MAX_LIMIT) {
            throw validation(
                    "limit",
                    "limit 必须在 1 到 100 之间");
        }
        return limit;
    }

    private static String normalizeReviewStatus(String value) {
        String normalized = blankToNull(value);
        if (normalized != null
                && !"PENDING".equals(normalized)
                && !"APPROVED".equals(normalized)) {
            throw validation(
                    "reviewStatus",
                    "reviewStatus 只允许 PENDING 或 APPROVED");
        }
        return normalized;
    }

    private static String normalizePhotoCompleteness(String value) {
        String normalized = blankToNull(value);
        if (normalized != null
                && !"COMPLETE".equals(normalized)
                && !"INCOMPLETE".equals(normalized)) {
            throw validation(
                    "photoCompleteness",
                    "photoCompleteness 只允许 COMPLETE 或 INCOMPLETE");
        }
        return normalized;
    }

    private static String requiredOrderNo(String value) {
        if (value == null || value.isBlank()) {
            throw validation(
                    "deliveryOrderNo",
                    "deliveryOrderNo 不能为空");
        }
        return value.trim();
    }

    private static String blankToNull(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        return value.trim();
    }

    private static Object nullMarker(Object value) {
        return value == null ? "<null>" : value;
    }

    private static LocalDateTime toDatabaseTime(Instant value) {
        return value == null
                ? null
                : LocalDateTime.ofInstant(value, ZoneOffset.UTC);
    }

    private static Instant instant(LocalDateTime value) {
        return value == null
                ? null
                : value.toInstant(ZoneOffset.UTC);
    }

    private static String decimal(BigDecimal value) {
        return value == null
                ? null
                : value.setScale(2).toPlainString();
    }

    private static String money(Long amountCent) {
        return amountCent == null
                ? null
                : BigDecimal.valueOf(amountCent, 2).toPlainString();
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
                "投递订单查询参数不符合接口契约",
                false,
                Map.of(field, message));
    }

    private enum DetailMode {
        FULL,
        REVIEW_SAFE,
        CORRECTION_SAFE
    }

    private record Reliability(
            String weight,
            String amount) {

        boolean weightReliable() {
            return "RELIABLE".equals(weight);
        }

        boolean amountReliable() {
            return "RELIABLE".equals(amount);
        }
    }

    private record PageAnchor(
            long highWatermark,
            LocalDateTime lastSortTime,
            String lastOrderNo) {
    }

    private record PageRows(
            List<DeliveryOrderSummaryRow> items,
            String nextCursor) {
    }

    private record ResolvedScope(
            long tenantId,
            long organizationId,
            Long assetId,
            List<Long> portIds) {

        private ResolvedScope {
            portIds = List.copyOf(portIds);
        }
    }

    private record ResolvedUserFilter(
            long tenantId,
            long organizationId,
            long organizationUserId) {
    }

    private record ResolvedDeviceFilter(
            long tenantId,
            long organizationId,
            Long assetId,
            List<Long> portIds) {

        private ResolvedDeviceFilter {
            portIds = List.copyOf(portIds);
        }
    }

    private record ResolvedDetailIdentities(
            OrganizationUserUid ownerUid,
            Map<Long, DeliveryOrderIdentityFacts.ReviewerIdentity>
                    reviewers) {
    }
}
