package org.enveloping.ecobin.identity.application.deliveryorder;

import org.enveloping.ecobin.identity.api.error.DeliveryIdentityFactMismatchException;
import org.enveloping.ecobin.identity.api.error.DeliveryIdentityFactMismatchException.Reason;
import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.id.PrincipalUid;
import org.enveloping.ecobin.identity.api.persistence.DeliveryOrderIdentityBatchRef;
import org.enveloping.ecobin.identity.api.persistence.DeliveryOrderIdentityBatchRef.EntrySink;
import org.enveloping.ecobin.identity.api.persistence.DeliveryOrderIdentityBatchRef.ReviewerKind;
import org.enveloping.ecobin.identity.api.persistence.DeliveryOrganizationUserFilterRef;
import org.enveloping.ecobin.identity.api.port.DeliveryOrderIdentityQueryPort;
import org.enveloping.ecobin.identity.api.query.DeliveryOrganizationUserFilterQuery;
import org.enveloping.ecobin.identity.api.result.DeliveryOrderIdentityFacts;
import org.enveloping.ecobin.identity.api.result.DeliveryOrderIdentityFacts.ReviewerIdentity;
import org.enveloping.ecobin.identity.api.value.DeliveryIdentityFactToken;
import org.enveloping.ecobin.identity.api.value.IdentityPrincipalKind;
import org.enveloping.ecobin.identity.application.deliveryorder.DeliveryOrderIdentityRepository.OrganizationScopeRow;
import org.enveloping.ecobin.identity.application.deliveryorder.DeliveryOrderIdentityRepository.OrganizationUserFilterRow;
import org.enveloping.ecobin.identity.application.deliveryorder.DeliveryOrderIdentityRepository.OrganizationUserRow;
import org.enveloping.ecobin.identity.application.deliveryorder.DeliveryOrderIdentityRepository.PlatformAdminRow;
import org.enveloping.ecobin.identity.application.deliveryorder.DeliveryOrderIdentityRepository.StaffAccountRow;
import org.enveloping.ecobin.identity.application.persistence.DeliveryOrganizationUserFilterRefFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;

@Service
public class DeliveryOrderIdentityQueryService
        implements DeliveryOrderIdentityQueryPort {

    private final DeliveryOrderIdentityRepository repository;
    private final DeliveryOrganizationUserFilterRefFactory
            filterRefFactory;

    public DeliveryOrderIdentityQueryService(
            DeliveryOrderIdentityRepository repository,
            DeliveryOrganizationUserFilterRefFactory filterRefFactory) {
        this.repository = repository;
        this.filterRefFactory = filterRefFactory;
    }

    @Override
    @Transactional(
            propagation = Propagation.MANDATORY,
            readOnly = true)
    public DeliveryOrderIdentityFacts resolveFacts(
            DeliveryOrderIdentityBatchRef batchRef) {
        Objects.requireNonNull(batchRef, "batchRef");
        BatchCollector collector = new BatchCollector();
        try {
            batchRef.consumeOnce(collector);
        } finally {
            collector.close();
        }

        validateOrganizationScopes(collector);
        Map<DeliveryIdentityFactToken, OrganizationUserUid> users =
                resolveOrganizationUsers(collector.organizationUsers());
        Map<DeliveryIdentityFactToken, ReviewerIdentity> reviewers =
                resolveReviewers(collector.reviewers());
        return new DeliveryOrderIdentityFacts(users, reviewers);
    }

    @Override
    @Transactional(
            propagation = Propagation.MANDATORY,
            readOnly = true)
    public Optional<DeliveryOrganizationUserFilterRef>
    resolveOrganizationUserFilter(
            DeliveryOrganizationUserFilterQuery query) {
        Objects.requireNonNull(query, "query");
        Optional<OrganizationUserFilterRow> row =
                repository.findOrganizationUserFilter(
                        query.tenantCode(),
                        query.organizationCode(),
                        query.organizationUserUid());
        if (row.isEmpty()) {
            return Optional.empty();
        }
        OrganizationUserFilterRow resolved = row.get();
        if (!query.tenantCode().equals(resolved.tenantCode())
                || !query.organizationCode().equals(
                resolved.organizationCode())
                || !query.organizationUserUid().value().equals(
                resolved.organizationUserUid())) {
            return Optional.empty();
        }
        return Optional.of(filterRefFactory.issue(
                resolved.tenantId(),
                resolved.organizationId(),
                resolved.organizationUserId()));
    }

    private void validateOrganizationScopes(BatchCollector collector) {
        Set<Long> organizationIds = new LinkedHashSet<>();
        collector.organizationUsers().values().forEach(item ->
                organizationIds.add(item.organizationKey()));
        collector.reviewers().values().forEach(item ->
                organizationIds.add(item.organizationKey()));
        Map<Long, OrganizationScopeRow> scopes =
                repository.findOrganizationScopes(organizationIds);
        collector.organizationUsers().values().forEach(item ->
                requireScope(
                        scopes,
                        item.tenantKey(),
                        item.organizationKey()));
        collector.reviewers().values().forEach(item ->
                requireScope(
                        scopes,
                        item.tenantKey(),
                        item.organizationKey()));
    }

    private Map<DeliveryIdentityFactToken, OrganizationUserUid>
    resolveOrganizationUsers(
            Map<DeliveryIdentityFactToken, OrganizationUserKey> requested) {
        Set<Long> ids = keys(requested.values(),
                OrganizationUserKey::organizationUserKey);
        Map<Long, OrganizationUserRow> rows =
                repository.findOrganizationUsers(ids);
        LinkedHashMap<DeliveryIdentityFactToken, OrganizationUserUid>
                result = new LinkedHashMap<>();
        requested.forEach((token, key) -> {
            OrganizationUserRow row =
                    rows.get(key.organizationUserKey());
            if (row == null
                    || row.tenantId() != key.tenantKey()
                    || row.organizationId()
                    != key.organizationKey()
                    || row.organizationUserId()
                    != key.organizationUserKey()) {
                throw mismatch(
                        Reason.ORGANIZATION_USER_MISMATCH,
                        "投递订单关联的机构用户身份不存在或作用域不匹配");
            }
            result.put(
                    token,
                    new OrganizationUserUid(
                            row.organizationUserUid()));
        });
        return Map.copyOf(result);
    }

    private Map<DeliveryIdentityFactToken, ReviewerIdentity>
    resolveReviewers(
            Map<DeliveryIdentityFactToken, ReviewerKey> requested) {
        Set<Long> platformIds = new LinkedHashSet<>();
        Set<Long> staffIds = new LinkedHashSet<>();
        requested.values().forEach(key -> {
            if (key.reviewerKind() == ReviewerKind.PLATFORM_ADMIN) {
                platformIds.add(key.platformAdminKey());
            } else {
                staffIds.add(key.staffAccountKey());
            }
        });
        Map<Long, PlatformAdminRow> platforms =
                repository.findPlatformAdministrators(platformIds);
        Map<Long, StaffAccountRow> staff =
                repository.findStaffAccounts(staffIds);
        LinkedHashMap<DeliveryIdentityFactToken, ReviewerIdentity>
                result = new LinkedHashMap<>();
        requested.forEach((token, key) -> result.put(
                token,
                reviewer(key, platforms, staff)));
        return Map.copyOf(result);
    }

    private static ReviewerIdentity reviewer(
            ReviewerKey key,
            Map<Long, PlatformAdminRow> platforms,
            Map<Long, StaffAccountRow> staff) {
        if (key.reviewerKind() == ReviewerKind.PLATFORM_ADMIN) {
            PlatformAdminRow row =
                    platforms.get(key.platformAdminKey());
            if (row == null) {
                throw mismatch(
                        Reason.REVIEWER_MISMATCH,
                        "投递修订关联的平台审核人员不存在");
            }
            return new ReviewerIdentity(
                    IdentityPrincipalKind.PLATFORM_ADMIN,
                    new PrincipalUid(row.platformAdminUid()),
                    row.displayName());
        }
        StaffAccountRow row = staff.get(key.staffAccountKey());
        if (row == null || row.tenantId() != key.tenantKey()) {
            throw mismatch(
                    Reason.REVIEWER_MISMATCH,
                    "投递修订关联的工作人员不存在或租户不匹配");
        }
        IdentityPrincipalKind actorKind = switch (row.accountKind()) {
            case "TENANT_PRINCIPAL" ->
                    IdentityPrincipalKind.TENANT_PRINCIPAL;
            case "STAFF" -> IdentityPrincipalKind.STAFF_ACCOUNT;
            default -> throw mismatch(
                    Reason.REVIEWER_MISMATCH,
                    "投递修订关联的工作人员类型无法识别");
        };
        return new ReviewerIdentity(
                actorKind,
                new PrincipalUid(row.staffAccountUid()),
                row.displayName());
    }

    private static void requireScope(
            Map<Long, OrganizationScopeRow> scopes,
            long tenantKey,
            long organizationKey) {
        OrganizationScopeRow scope = scopes.get(organizationKey);
        if (scope == null || scope.tenantId() != tenantKey) {
            throw mismatch(
                    Reason.ORGANIZATION_SCOPE_MISMATCH,
                    "投递身份事实的租户机构作用域不存在或不匹配");
        }
    }

    private static <T> Set<Long> keys(
            Collection<T> values,
            LongKey<T> key) {
        LinkedHashSet<Long> result = new LinkedHashSet<>();
        values.forEach(value -> result.add(key.value(value)));
        return Set.copyOf(result);
    }

    private static DeliveryIdentityFactMismatchException mismatch(
            Reason reason,
            String message) {
        return new DeliveryIdentityFactMismatchException(
                reason,
                message);
    }

    @FunctionalInterface
    private interface LongKey<T> {

        long value(T item);
    }

    private static final class BatchCollector implements EntrySink {

        private final LinkedHashMap<
                DeliveryIdentityFactToken,
                OrganizationUserKey> organizationUsers =
                new LinkedHashMap<>();
        private final LinkedHashMap<
                DeliveryIdentityFactToken,
                ReviewerKey> reviewers = new LinkedHashMap<>();
        private boolean closed;

        @Override
        public synchronized void organizationUser(
                DeliveryIdentityFactToken token,
                long tenantKey,
                long organizationKey,
                long organizationUserKey) {
            requireOpen();
            requireToken(token);
            OrganizationUserKey prior = organizationUsers.putIfAbsent(
                    token,
                    new OrganizationUserKey(
                            positive(tenantKey, "tenantKey"),
                            positive(
                                    organizationKey,
                                    "organizationKey"),
                            positive(
                                    organizationUserKey,
                                    "organizationUserKey")));
            if (prior != null) {
                throw new IllegalArgumentException(
                        "duplicate organization-user identity token");
            }
        }

        @Override
        public synchronized void reviewer(
                DeliveryIdentityFactToken token,
                long tenantKey,
                long organizationKey,
                ReviewerKind reviewerKind,
                Long platformAdminKey,
                Long staffAccountKey) {
            requireOpen();
            requireToken(token);
            Objects.requireNonNull(reviewerKind, "reviewerKind");
            if (reviewerKind == ReviewerKind.PLATFORM_ADMIN
                    && (platformAdminKey == null
                    || staffAccountKey != null)
                    || reviewerKind == ReviewerKind.STAFF
                    && (platformAdminKey != null
                    || staffAccountKey == null)) {
                throw new IllegalArgumentException(
                        "reviewer kind does not match actor keys");
            }
            ReviewerKey prior = reviewers.putIfAbsent(
                    token,
                    new ReviewerKey(
                            positive(tenantKey, "tenantKey"),
                            positive(
                                    organizationKey,
                                    "organizationKey"),
                            reviewerKind,
                            nullablePositive(
                                    platformAdminKey,
                                    "platformAdminKey"),
                            nullablePositive(
                                    staffAccountKey,
                                    "staffAccountKey")));
            if (prior != null) {
                throw new IllegalArgumentException(
                        "duplicate reviewer identity token");
            }
        }

        synchronized Map<
                DeliveryIdentityFactToken,
                OrganizationUserKey> organizationUsers() {
            return Map.copyOf(organizationUsers);
        }

        synchronized Map<
                DeliveryIdentityFactToken,
                ReviewerKey> reviewers() {
            return Map.copyOf(reviewers);
        }

        synchronized void close() {
            closed = true;
        }

        private void requireOpen() {
            if (closed) {
                throw new IllegalStateException(
                        "delivery identity batch sink is closed");
            }
        }

        private static void requireToken(
                DeliveryIdentityFactToken token) {
            Objects.requireNonNull(token, "token");
        }

        private static long positive(long value, String name) {
            if (value <= 0) {
                throw new IllegalArgumentException(
                        name + " must be positive");
            }
            return value;
        }

        private static Long nullablePositive(
                Long value,
                String name) {
            if (value != null && value <= 0) {
                throw new IllegalArgumentException(
                        name + " must be positive");
            }
            return value;
        }
    }

    private record OrganizationUserKey(
            long tenantKey,
            long organizationKey,
            long organizationUserKey) {
    }

    private record ReviewerKey(
            long tenantKey,
            long organizationKey,
            ReviewerKind reviewerKind,
            Long platformAdminKey,
            Long staffAccountKey) {
    }
}
