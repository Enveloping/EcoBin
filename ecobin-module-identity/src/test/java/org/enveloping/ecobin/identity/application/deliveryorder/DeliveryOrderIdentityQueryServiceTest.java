package org.enveloping.ecobin.identity.application.deliveryorder;

import org.enveloping.ecobin.identity.api.error.DeliveryIdentityFactMismatchException;
import org.enveloping.ecobin.identity.api.error.DeliveryIdentityFactMismatchException.Reason;
import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.persistence.DeliveryOrderIdentityBatchRef;
import org.enveloping.ecobin.identity.api.persistence.DeliveryOrderIdentityBatchRef.EntrySink;
import org.enveloping.ecobin.identity.api.persistence.DeliveryOrderIdentityBatchRef.ReviewerKind;
import org.enveloping.ecobin.identity.api.persistence.DeliveryOrganizationUserFilterRef;
import org.enveloping.ecobin.identity.api.query.DeliveryOrganizationUserFilterQuery;
import org.enveloping.ecobin.identity.api.result.DeliveryOrderIdentityFacts;
import org.enveloping.ecobin.identity.api.value.DeliveryIdentityFactToken;
import org.enveloping.ecobin.identity.api.value.IdentityPrincipalKind;
import org.enveloping.ecobin.identity.application.deliveryorder.DeliveryOrderIdentityRepository.OrganizationScopeRow;
import org.enveloping.ecobin.identity.application.deliveryorder.DeliveryOrderIdentityRepository.OrganizationUserFilterRow;
import org.enveloping.ecobin.identity.application.deliveryorder.DeliveryOrderIdentityRepository.OrganizationUserRow;
import org.enveloping.ecobin.identity.application.deliveryorder.DeliveryOrderIdentityRepository.PlatformAdminRow;
import org.enveloping.ecobin.identity.application.deliveryorder.DeliveryOrderIdentityRepository.StaffAccountRow;
import org.enveloping.ecobin.identity.application.persistence.DeliveryOrganizationUserFilterRefFactory;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class DeliveryOrderIdentityQueryServiceTest {

    private static final long TENANT_ID = 11;
    private static final long ORGANIZATION_ID = 22;
    private static final long USER_ID = 33;
    private static final long PLATFORM_ID = 44;
    private static final long STAFF_ID = 55;
    private static final String TENANT_CODE = "tenant-a";
    private static final String ORGANIZATION_CODE = "organization-a";
    private static final UUID USER_UID =
            UUID.fromString("11111111-1111-4111-8111-111111111111");
    private static final UUID PLATFORM_UID =
            UUID.fromString("22222222-2222-4222-8222-222222222222");
    private static final UUID STAFF_UID =
            UUID.fromString("33333333-3333-4333-8333-333333333333");

    @Mock
    private DeliveryOrderIdentityRepository repository;

    @Mock
    private DeliveryOrganizationUserFilterRefFactory filterRefFactory;

    @Mock
    private DeliveryOrganizationUserFilterRef filterRef;

    private DeliveryOrderIdentityQueryService service;

    @BeforeEach
    void setUp() {
        service = new DeliveryOrderIdentityQueryService(
                repository,
                filterRefFactory);
    }

    @Test
    void resolvesUsersAndBothReviewerKindsWithOpaqueTokens() {
        DeliveryIdentityFactToken userToken =
                DeliveryIdentityFactToken.create();
        DeliveryIdentityFactToken platformToken =
                DeliveryIdentityFactToken.create();
        DeliveryIdentityFactToken staffToken =
                DeliveryIdentityFactToken.create();
        TestBatchRef batch = new TestBatchRef(sink -> {
            sink.organizationUser(
                    userToken,
                    TENANT_ID,
                    ORGANIZATION_ID,
                    USER_ID);
            sink.reviewer(
                    platformToken,
                    TENANT_ID,
                    ORGANIZATION_ID,
                    ReviewerKind.PLATFORM_ADMIN,
                    PLATFORM_ID,
                    null);
            sink.reviewer(
                    staffToken,
                    TENANT_ID,
                    ORGANIZATION_ID,
                    ReviewerKind.STAFF,
                    null,
                    STAFF_ID);
        });
        stubScope();
        when(repository.findOrganizationUsers(Set.of(USER_ID)))
                .thenReturn(Map.of(
                        USER_ID,
                        new OrganizationUserRow(
                                TENANT_ID,
                                ORGANIZATION_ID,
                                USER_ID,
                                USER_UID)));
        when(repository.findPlatformAdministrators(Set.of(PLATFORM_ID)))
                .thenReturn(Map.of(
                        PLATFORM_ID,
                        new PlatformAdminRow(
                                PLATFORM_ID,
                                PLATFORM_UID,
                                "平台审核员")));
        when(repository.findStaffAccounts(Set.of(STAFF_ID)))
                .thenReturn(Map.of(
                        STAFF_ID,
                        new StaffAccountRow(
                                TENANT_ID,
                                STAFF_ID,
                                STAFF_UID,
                                "STAFF",
                                "机构审核员")));

        DeliveryOrderIdentityFacts result =
                service.resolveFacts(batch);

        assertEquals(1, batch.consumeCount());
        assertEquals(
                USER_UID,
                result.organizationUsers()
                        .get(userToken)
                        .value());
        assertEquals(
                IdentityPrincipalKind.PLATFORM_ADMIN,
                result.reviewers()
                        .get(platformToken)
                        .actorKind());
        assertEquals(
                PLATFORM_UID,
                result.reviewers()
                        .get(platformToken)
                        .actorUid()
                        .value());
        assertEquals(
                "平台审核员",
                result.reviewers()
                        .get(platformToken)
                        .displayName());
        assertEquals(
                IdentityPrincipalKind.STAFF_ACCOUNT,
                result.reviewers().get(staffToken).actorKind());
        assertEquals(
                STAFF_UID,
                result.reviewers()
                        .get(staffToken)
                        .actorUid()
                        .value());
    }

    @Test
    void rejectsAnOrganizationScopeThatBelongsToAnotherTenant() {
        DeliveryIdentityFactToken token =
                DeliveryIdentityFactToken.create();
        TestBatchRef batch = new TestBatchRef(sink ->
                sink.organizationUser(
                        token,
                        TENANT_ID,
                        ORGANIZATION_ID,
                        USER_ID));
        when(repository.findOrganizationScopes(Set.of(ORGANIZATION_ID)))
                .thenReturn(Map.of(
                        ORGANIZATION_ID,
                        new OrganizationScopeRow(
                                999,
                                ORGANIZATION_ID)));

        DeliveryIdentityFactMismatchException failure = assertThrows(
                DeliveryIdentityFactMismatchException.class,
                () -> service.resolveFacts(batch));

        assertEquals(
                Reason.ORGANIZATION_SCOPE_MISMATCH,
                failure.reason());
        verify(repository, never()).findOrganizationUsers(Set.of(USER_ID));
    }

    @Test
    void rejectsAMissingOrganizationUserInsteadOfDroppingTheMapping() {
        DeliveryIdentityFactToken token =
                DeliveryIdentityFactToken.create();
        TestBatchRef batch = new TestBatchRef(sink ->
                sink.organizationUser(
                        token,
                        TENANT_ID,
                        ORGANIZATION_ID,
                        USER_ID));
        stubScope();
        when(repository.findOrganizationUsers(Set.of(USER_ID)))
                .thenReturn(Map.of());

        DeliveryIdentityFactMismatchException failure = assertThrows(
                DeliveryIdentityFactMismatchException.class,
                () -> service.resolveFacts(batch));

        assertEquals(
                Reason.ORGANIZATION_USER_MISMATCH,
                failure.reason());
    }

    @Test
    void rejectsAnOrganizationUserFromAnotherOrganization() {
        DeliveryIdentityFactToken token =
                DeliveryIdentityFactToken.create();
        TestBatchRef batch = new TestBatchRef(sink ->
                sink.organizationUser(
                        token,
                        TENANT_ID,
                        ORGANIZATION_ID,
                        USER_ID));
        stubScope();
        when(repository.findOrganizationUsers(Set.of(USER_ID)))
                .thenReturn(Map.of(
                        USER_ID,
                        new OrganizationUserRow(
                                TENANT_ID,
                                999,
                                USER_ID,
                                USER_UID)));

        DeliveryIdentityFactMismatchException failure = assertThrows(
                DeliveryIdentityFactMismatchException.class,
                () -> service.resolveFacts(batch));

        assertEquals(
                Reason.ORGANIZATION_USER_MISMATCH,
                failure.reason());
    }

    @Test
    void rejectsPublicUidFromADifferentInternalOrganizationUser() {
        DeliveryIdentityFactToken token =
                DeliveryIdentityFactToken.create();
        TestBatchRef batch = new TestBatchRef(sink ->
                sink.organizationUser(
                        token,
                        TENANT_ID,
                        ORGANIZATION_ID,
                        USER_ID));
        stubScope();
        long anotherUserId = USER_ID + 1;
        UUID anotherUserUid =
                UUID.fromString(
                        "44444444-4444-4444-8444-444444444444");
        when(repository.findOrganizationUsers(Set.of(USER_ID)))
                .thenReturn(Map.of(
                        USER_ID,
                        new OrganizationUserRow(
                                TENANT_ID,
                                ORGANIZATION_ID,
                                anotherUserId,
                                anotherUserUid)));

        DeliveryIdentityFactMismatchException failure = assertThrows(
                DeliveryIdentityFactMismatchException.class,
                () -> service.resolveFacts(batch));

        assertEquals(
                Reason.ORGANIZATION_USER_MISMATCH,
                failure.reason());
    }

    @Test
    void rejectsAStaffReviewerFromAnotherTenant() {
        DeliveryIdentityFactToken token =
                DeliveryIdentityFactToken.create();
        TestBatchRef batch = new TestBatchRef(sink ->
                sink.reviewer(
                        token,
                        TENANT_ID,
                        ORGANIZATION_ID,
                        ReviewerKind.STAFF,
                        null,
                        STAFF_ID));
        stubScope();
        when(repository.findPlatformAdministrators(Set.of()))
                .thenReturn(Map.of());
        when(repository.findStaffAccounts(Set.of(STAFF_ID)))
                .thenReturn(Map.of(
                        STAFF_ID,
                        new StaffAccountRow(
                                999,
                                STAFF_ID,
                                STAFF_UID,
                                "STAFF",
                                "其他租户审核员")));

        DeliveryIdentityFactMismatchException failure = assertThrows(
                DeliveryIdentityFactMismatchException.class,
                () -> service.resolveFacts(batch));

        assertEquals(Reason.REVIEWER_MISMATCH, failure.reason());
    }

    @Test
    void mapsTenantPrincipalStaffRowToItsPublicActorKind() {
        DeliveryIdentityFactToken token =
                DeliveryIdentityFactToken.create();
        TestBatchRef batch = new TestBatchRef(sink ->
                sink.reviewer(
                        token,
                        TENANT_ID,
                        ORGANIZATION_ID,
                        ReviewerKind.STAFF,
                        null,
                        STAFF_ID));
        stubScope();
        when(repository.findStaffAccounts(Set.of(STAFF_ID)))
                .thenReturn(Map.of(
                        STAFF_ID,
                        new StaffAccountRow(
                                TENANT_ID,
                                STAFF_ID,
                                STAFF_UID,
                                "TENANT_PRINCIPAL",
                                "租户主体")));

        DeliveryOrderIdentityFacts result =
                service.resolveFacts(batch);

        assertEquals(
                IdentityPrincipalKind.TENANT_PRINCIPAL,
                result.reviewers().get(token).actorKind());
        assertEquals(
                STAFF_UID,
                result.reviewers()
                        .get(token)
                        .actorUid()
                        .value());
    }

    @Test
    void rejectsReviewerKindAndInternalKeyMismatch() {
        DeliveryIdentityFactToken token =
                DeliveryIdentityFactToken.create();
        TestBatchRef batch = new TestBatchRef(sink ->
                sink.reviewer(
                        token,
                        TENANT_ID,
                        ORGANIZATION_ID,
                        ReviewerKind.STAFF,
                        PLATFORM_ID,
                        null));

        IllegalArgumentException failure = assertThrows(
                IllegalArgumentException.class,
                () -> service.resolveFacts(batch));

        assertTrue(
                failure.getMessage().contains("does not match actor keys"));
        verify(repository, never()).findOrganizationScopes(Set.of());
    }

    @Test
    void rejectsDuplicateTokensWithinTheSameFactKind() {
        DeliveryIdentityFactToken token =
                DeliveryIdentityFactToken.create();
        TestBatchRef batch = new TestBatchRef(sink -> {
            sink.organizationUser(
                    token,
                    TENANT_ID,
                    ORGANIZATION_ID,
                    USER_ID);
            sink.organizationUser(
                    token,
                    TENANT_ID,
                    ORGANIZATION_ID,
                    USER_ID);
        });

        IllegalArgumentException failure = assertThrows(
                IllegalArgumentException.class,
                () -> service.resolveFacts(batch));

        assertTrue(failure.getMessage().contains("duplicate"));
    }

    @Test
    void closesTheBatchSinkWhenCallerConsumptionReturns() {
        AtomicReference<EntrySink> retained = new AtomicReference<>();
        TestBatchRef batch = new TestBatchRef(retained::set);
        when(repository.findOrganizationScopes(Set.of()))
                .thenReturn(Map.of());
        when(repository.findOrganizationUsers(Set.of()))
                .thenReturn(Map.of());
        when(repository.findPlatformAdministrators(Set.of()))
                .thenReturn(Map.of());
        when(repository.findStaffAccounts(Set.of()))
                .thenReturn(Map.of());

        service.resolveFacts(batch);

        IllegalStateException failure = assertThrows(
                IllegalStateException.class,
                () -> retained.get().organizationUser(
                        DeliveryIdentityFactToken.create(),
                        TENANT_ID,
                        ORGANIZATION_ID,
                        USER_ID));
        assertTrue(failure.getMessage().contains("closed"));
    }

    @Test
    void resolvesOrganizationUserFilterToIdentityOwnedReference() {
        DeliveryOrganizationUserFilterQuery query = filterQuery();
        when(repository.findOrganizationUserFilter(
                TENANT_CODE,
                ORGANIZATION_CODE,
                query.organizationUserUid()))
                .thenReturn(Optional.of(new OrganizationUserFilterRow(
                        TENANT_ID,
                        ORGANIZATION_ID,
                        USER_ID,
                        TENANT_CODE,
                        ORGANIZATION_CODE,
                        USER_UID)));
        when(filterRefFactory.issue(
                TENANT_ID,
                ORGANIZATION_ID,
                USER_ID))
                .thenReturn(filterRef);

        Optional<DeliveryOrganizationUserFilterRef> result =
                service.resolveOrganizationUserFilter(query);

        assertTrue(result.isPresent());
        assertSame(filterRef, result.orElseThrow());
    }

    @Test
    void unknownOrWrongScopeFilterReturnsEmptyWithoutIssuingAReference() {
        DeliveryOrganizationUserFilterQuery query = filterQuery();
        when(repository.findOrganizationUserFilter(
                TENANT_CODE,
                ORGANIZATION_CODE,
                query.organizationUserUid()))
                .thenReturn(Optional.of(new OrganizationUserFilterRow(
                        TENANT_ID,
                        ORGANIZATION_ID,
                        USER_ID,
                        "different-tenant",
                        ORGANIZATION_CODE,
                        USER_UID)));

        Optional<DeliveryOrganizationUserFilterRef> result =
                service.resolveOrganizationUserFilter(query);

        assertFalse(result.isPresent());
        verify(filterRefFactory, never()).issue(
                anyLong(),
                anyLong(),
                anyLong());
    }

    @Test
    void missingFilterReturnsExplicitEmpty() {
        DeliveryOrganizationUserFilterQuery query = filterQuery();
        when(repository.findOrganizationUserFilter(
                TENANT_CODE,
                ORGANIZATION_CODE,
                query.organizationUserUid()))
                .thenReturn(Optional.empty());

        Optional<DeliveryOrganizationUserFilterRef> result =
                service.resolveOrganizationUserFilter(query);

        assertTrue(result.isEmpty());
        verify(filterRefFactory, never()).issue(
                anyLong(),
                anyLong(),
                anyLong());
    }

    private void stubScope() {
        when(repository.findOrganizationScopes(Set.of(ORGANIZATION_ID)))
                .thenReturn(Map.of(
                        ORGANIZATION_ID,
                        new OrganizationScopeRow(
                                TENANT_ID,
                                ORGANIZATION_ID)));
    }

    private static DeliveryOrganizationUserFilterQuery filterQuery() {
        return new DeliveryOrganizationUserFilterQuery(
                TENANT_CODE,
                ORGANIZATION_CODE,
                new OrganizationUserUid(USER_UID));
    }

    private static final class TestBatchRef
            implements DeliveryOrderIdentityBatchRef {

        private final Consumer<EntrySink> entries;
        private final AtomicInteger consumption = new AtomicInteger();

        private TestBatchRef(Consumer<EntrySink> entries) {
            this.entries = entries;
        }

        @Override
        public void consumeOnce(EntrySink sink) {
            if (consumption.incrementAndGet() != 1) {
                throw new IllegalStateException(
                        "test batch was already consumed");
            }
            entries.accept(sink);
        }

        private int consumeCount() {
            return consumption.get();
        }
    }
}
