package org.enveloping.ecobin.identity.application.security;

import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.persistence.DeliveryScopePersistenceRef;
import org.enveloping.ecobin.identity.api.query.DeliveryScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeliveryScope;
import org.enveloping.ecobin.identity.application.security.DeliveryScopeAuthorizationRepository.Membership;
import org.enveloping.ecobin.identity.application.security.DeliveryScopeAuthorizationRepository.PlatformActor;
import org.enveloping.ecobin.identity.application.security.DeliveryScopeAuthorizationRepository.Scope;
import org.enveloping.ecobin.identity.application.security.DeliveryScopeAuthorizationRepository.StaffActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.enveloping.ecobin.identity.application.web.WebAccountType;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class DeliveryScopeAuthorizationServiceTest {

    private static final long PLATFORM_ID = 10;
    private static final long STAFF_ID = 20;
    private static final long TENANT_ID = 30;
    private static final long ORGANIZATION_ID = 40;
    private static final long AUTH_VERSION = 7;
    private static final UUID PRINCIPAL_UID =
            UUID.fromString("11111111-1111-4111-8111-111111111111");
    private static final UUID SESSION_UID =
            UUID.fromString("22222222-2222-4222-8222-222222222222");
    private static final String TENANT_CODE = "tenant-a";
    private static final String ORGANIZATION_CODE = "organization-a";

    @Mock
    private DeliveryScopeAuthorizationRepository repository;

    @Mock
    private DeliveryScopePersistenceRefFactory referenceFactory;

    @Mock
    private DeliveryScopePersistenceRef persistenceRef;

    private DeliveryScopeAuthorizationService service;

    @BeforeEach
    void setUp() {
        service = new DeliveryScopeAuthorizationService(
                repository,
                referenceFactory);
        lenient().when(referenceFactory.issue(
                        anyLong(),
                        anyLong(),
                        any(),
                        any()))
                .thenReturn(persistenceRef);
    }

    @AfterEach
    void tearDown() {
        TargetWebActorContext.clear();
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(false);
    }

    @Test
    void activePlatformAdministratorReceivesAllCapabilitiesWithoutReadLocks() {
        readOnlyTransaction();
        TargetWebActorContext.set(platformActor());
        when(repository.findPlatformActor(
                PLATFORM_ID,
                PRINCIPAL_UID,
                false))
                .thenReturn(Optional.of(new PlatformActor(
                        PLATFORM_ID,
                        "平台管理员",
                        true,
                        AUTH_VERSION)));
        when(repository.findPlatformScope(
                TENANT_CODE,
                ORGANIZATION_CODE,
                false))
                .thenReturn(Optional.of(scope()));

        AuthorizedDeliveryScope result = service.authorize(
                platformQuery());

        assertTrue(result.platformActor());
        assertTrue(result.deliveryRead());
        assertTrue(result.reviewExecute());
        assertTrue(result.deliveryCorrect());
        assertEquals(PRINCIPAL_UID, result.principalUid());
        assertEquals(SESSION_UID, result.sessionUid());
        assertEquals("平台管理员", result.actorDisplayName());
        assertEquals(TENANT_CODE, result.tenantCode());
        assertEquals(ORGANIZATION_CODE, result.organizationCode());
        assertSame(persistenceRef, result.persistenceRef());
        verify(referenceFactory).issue(
                TENANT_ID,
                ORGANIZATION_ID,
                PLATFORM_ID,
                null);
    }

    @Test
    void platformWriteAuthorizationLocksActorAndTargetScopeRows() {
        writeTransaction();
        TargetWebActorContext.set(platformActor());
        when(repository.findPlatformActor(
                PLATFORM_ID,
                PRINCIPAL_UID,
                true))
                .thenReturn(Optional.of(new PlatformActor(
                        PLATFORM_ID,
                        "平台管理员",
                        true,
                        AUTH_VERSION)));
        when(repository.findPlatformScope(
                TENANT_CODE,
                ORGANIZATION_CODE,
                true))
                .thenReturn(Optional.of(scope()));

        service.authorize(platformQuery());

        verify(repository).findPlatformActor(
                PLATFORM_ID,
                PRINCIPAL_UID,
                true);
        verify(repository).findPlatformScope(
                TENANT_CODE,
                ORGANIZATION_CODE,
                true);
    }

    @Test
    void disabledPlatformAdministratorIsRejectedAsInvalidSession() {
        writeTransaction();
        TargetWebActorContext.set(platformActor());
        when(repository.findPlatformActor(
                PLATFORM_ID,
                PRINCIPAL_UID,
                true))
                .thenReturn(Optional.of(new PlatformActor(
                        PLATFORM_ID,
                        "平台管理员",
                        false,
                        AUTH_VERSION)));

        TargetApiException failure = assertThrows(
                TargetApiException.class,
                () -> service.authorize(platformQuery()));

        assertEquals(401, failure.status());
        assertEquals("AUTH.SESSION_INVALID", failure.code());
        verify(repository, never()).findPlatformScope(
                any(),
                any(),
                any(Boolean.class));
    }

    @Test
    void tenantPrincipalNaturallyReceivesAllCapabilitiesAndWriteLocks() {
        writeTransaction();
        TargetWebActorContext.set(
                staffActor(WebAccountType.TENANT_PRINCIPAL));
        stubStaffActor("TENANT_PRINCIPAL", true);
        when(repository.findStaffScope(
                TENANT_ID,
                ORGANIZATION_CODE,
                true))
                .thenReturn(Optional.of(scope()));

        AuthorizedDeliveryScope result = service.authorize(staffQuery());

        assertFalse(result.platformActor());
        assertTrue(result.deliveryRead());
        assertTrue(result.reviewExecute());
        assertTrue(result.deliveryCorrect());
        verify(repository).findStaffActor(
                STAFF_ID,
                PRINCIPAL_UID,
                true);
        verify(repository).findStaffScope(
                TENANT_ID,
                ORGANIZATION_CODE,
                true);
        verify(repository, never()).findTenantDeliveryCapabilities(
                anyLong(),
                anyLong());
        verify(referenceFactory).issue(
                TENANT_ID,
                ORGANIZATION_ID,
                null,
                STAFF_ID);
    }

    @Test
    void tenantReviewGrantCoversEveryOrganizationInTheTenant() {
        readOnlyTransaction();
        TargetWebActorContext.set(staffActor(WebAccountType.STAFF));
        stubStaffActor("STAFF", false);
        when(repository.findStaffScope(
                TENANT_ID,
                ORGANIZATION_CODE,
                false))
                .thenReturn(Optional.of(scope()));
        when(repository.findTenantDeliveryCapabilities(
                TENANT_ID,
                STAFF_ID))
                .thenReturn(Set.of(
                        DeliveryScopeAuthorizationService.REVIEW_EXECUTE));
        when(repository.findMembership(
                TENANT_ID,
                ORGANIZATION_ID,
                STAFF_ID))
                .thenReturn(Optional.empty());

        AuthorizedDeliveryScope result = service.authorize(staffQuery());

        assertFalse(result.deliveryRead());
        assertTrue(result.reviewExecute());
        assertFalse(result.deliveryCorrect());
        verify(repository, never())
                .findOrganizationDeliveryCapabilities(
                        anyLong(),
                        anyLong(),
                        anyLong());
    }

    @Test
    void ordinaryMemberReceivesOnlyItsOrganizationCorrectionGrant() {
        readOnlyTransaction();
        TargetWebActorContext.set(staffActor(WebAccountType.STAFF));
        stubStaffActor("STAFF", false);
        when(repository.findStaffScope(
                TENANT_ID,
                ORGANIZATION_CODE,
                false))
                .thenReturn(Optional.of(scope()));
        when(repository.findTenantDeliveryCapabilities(
                TENANT_ID,
                STAFF_ID))
                .thenReturn(Set.of());
        when(repository.findMembership(
                TENANT_ID,
                ORGANIZATION_ID,
                STAFF_ID))
                .thenReturn(Optional.of(new Membership(false, true)));
        when(repository.findOrganizationDeliveryCapabilities(
                TENANT_ID,
                ORGANIZATION_ID,
                STAFF_ID))
                .thenReturn(Set.of(
                        DeliveryScopeAuthorizationService.DELIVERY_CORRECT));

        AuthorizedDeliveryScope result = service.authorize(staffQuery());

        assertFalse(result.deliveryRead());
        assertFalse(result.reviewExecute());
        assertTrue(result.deliveryCorrect());
    }

    @Test
    void organizationManagerNaturallyReceivesAllOrganizationCapabilities() {
        readOnlyTransaction();
        TargetWebActorContext.set(staffActor(WebAccountType.STAFF));
        stubStaffActor("STAFF", false);
        when(repository.findStaffScope(
                TENANT_ID,
                ORGANIZATION_CODE,
                false))
                .thenReturn(Optional.of(scope()));
        when(repository.findTenantDeliveryCapabilities(
                TENANT_ID,
                STAFF_ID))
                .thenReturn(Set.of());
        when(repository.findMembership(
                TENANT_ID,
                ORGANIZATION_ID,
                STAFF_ID))
                .thenReturn(Optional.of(new Membership(true, true)));
        when(repository.findOrganizationDeliveryCapabilities(
                TENANT_ID,
                ORGANIZATION_ID,
                STAFF_ID))
                .thenReturn(Set.of());

        AuthorizedDeliveryScope result = service.authorize(staffQuery());

        assertTrue(result.deliveryRead());
        assertTrue(result.reviewExecute());
        assertTrue(result.deliveryCorrect());
    }

    @Test
    void visibleMemberWithoutAnyDeliveryCapabilityReceivesForbidden() {
        readOnlyTransaction();
        TargetWebActorContext.set(staffActor(WebAccountType.STAFF));
        stubStaffActor("STAFF", false);
        when(repository.findStaffScope(
                TENANT_ID,
                ORGANIZATION_CODE,
                false))
                .thenReturn(Optional.of(scope()));
        when(repository.findTenantDeliveryCapabilities(
                TENANT_ID,
                STAFF_ID))
                .thenReturn(Set.of());
        when(repository.findMembership(
                TENANT_ID,
                ORGANIZATION_ID,
                STAFF_ID))
                .thenReturn(Optional.of(new Membership(false, true)));
        when(repository.findOrganizationDeliveryCapabilities(
                TENANT_ID,
                ORGANIZATION_ID,
                STAFF_ID))
                .thenReturn(Set.of());

        TargetApiException failure = assertThrows(
                TargetApiException.class,
                () -> service.authorize(staffQuery()));

        assertEquals(403, failure.status());
        assertEquals("AUTH.CAPABILITY_REQUIRED", failure.code());
        verify(referenceFactory, never()).issue(
                anyLong(),
                anyLong(),
                any(),
                any());
    }

    @Test
    void staffOutsideTheOrganizationVisibilityReceivesNotFound() {
        readOnlyTransaction();
        TargetWebActorContext.set(staffActor(WebAccountType.STAFF));
        stubStaffActor("STAFF", false);
        when(repository.findStaffScope(
                TENANT_ID,
                ORGANIZATION_CODE,
                false))
                .thenReturn(Optional.of(scope()));
        when(repository.findTenantDeliveryCapabilities(
                TENANT_ID,
                STAFF_ID))
                .thenReturn(Set.of());
        when(repository.findMembership(
                TENANT_ID,
                ORGANIZATION_ID,
                STAFF_ID))
                .thenReturn(Optional.empty());

        TargetApiException failure = assertThrows(
                TargetApiException.class,
                () -> service.authorize(staffQuery()));

        assertEquals(404, failure.status());
        assertEquals("RESOURCE.NOT_FOUND", failure.code());
    }

    @Test
    void targetOutsideTheSessionTenantReceivesNotFound() {
        readOnlyTransaction();
        TargetWebActorContext.set(staffActor(WebAccountType.STAFF));
        stubStaffActor("STAFF", false);
        when(repository.findStaffScope(
                TENANT_ID,
                ORGANIZATION_CODE,
                false))
                .thenReturn(Optional.empty());

        TargetApiException failure = assertThrows(
                TargetApiException.class,
                () -> service.authorize(staffQuery()));

        assertEquals(404, failure.status());
        assertEquals("RESOURCE.NOT_FOUND", failure.code());
        verify(repository, never()).findTenantDeliveryCapabilities(
                anyLong(),
                anyLong());
    }

    @Test
    void disabledOrganizationIsImmediatelyHiddenFromStaff() {
        readOnlyTransaction();
        TargetWebActorContext.set(staffActor(WebAccountType.STAFF));
        stubStaffActor("STAFF", false);
        when(repository.findStaffScope(
                TENANT_ID,
                ORGANIZATION_CODE,
                false))
                .thenReturn(Optional.of(scope(false)));

        TargetApiException failure = assertThrows(
                TargetApiException.class,
                () -> service.authorize(staffQuery()));

        assertEquals(404, failure.status());
        assertEquals("RESOURCE.NOT_FOUND", failure.code());
        verify(repository, never()).findTenantDeliveryCapabilities(
                anyLong(),
                anyLong());
        verify(referenceFactory, never()).issue(
                anyLong(),
                anyLong(),
                any(),
                any());
    }

    @Test
    void disabledOrganizationIsLockedAndRejectedForStaffWrites() {
        writeTransaction();
        TargetWebActorContext.set(staffActor(WebAccountType.STAFF));
        stubStaffActor("STAFF", true);
        when(repository.findStaffScope(
                TENANT_ID,
                ORGANIZATION_CODE,
                true))
                .thenReturn(Optional.of(scope(false)));

        TargetApiException failure = assertThrows(
                TargetApiException.class,
                () -> service.authorize(staffQuery()));

        assertEquals(404, failure.status());
        assertEquals("RESOURCE.NOT_FOUND", failure.code());
        verify(repository).findStaffScope(
                TENANT_ID,
                ORGANIZATION_CODE,
                true);
        verify(referenceFactory, never()).issue(
                anyLong(),
                anyLong(),
                any(),
                any());
    }

    private void stubStaffActor(
            String accountKind,
            boolean forUpdate) {
        when(repository.findStaffActor(
                STAFF_ID,
                PRINCIPAL_UID,
                forUpdate))
                .thenReturn(Optional.of(new StaffActor(
                        STAFF_ID,
                        TENANT_ID,
                        accountKind,
                        "工作人员",
                        true,
                        AUTH_VERSION,
                        TENANT_CODE,
                        true)));
    }

    private static DeliveryScopeAuthorizationQuery platformQuery() {
        return new DeliveryScopeAuthorizationQuery(
                true,
                TENANT_CODE,
                ORGANIZATION_CODE);
    }

    private static DeliveryScopeAuthorizationQuery staffQuery() {
        return new DeliveryScopeAuthorizationQuery(
                false,
                null,
                ORGANIZATION_CODE);
    }

    private static Scope scope() {
        return scope(true);
    }

    private static Scope scope(boolean organizationEnabled) {
        return new Scope(
                TENANT_ID,
                TENANT_CODE,
                ORGANIZATION_ID,
                ORGANIZATION_CODE,
                organizationEnabled);
    }

    private static TargetWebActor platformActor() {
        return new TargetWebActor(
                WebAccountType.PLATFORM_ADMIN,
                TrustedAudience.WEB_PLATFORM,
                PLATFORM_ID,
                PRINCIPAL_UID,
                null,
                null,
                null,
                SESSION_UID,
                "旧平台名称",
                null,
                1,
                AUTH_VERSION,
                Instant.parse("2030-01-01T00:00:00Z"),
                Set.of(),
                List.of());
    }

    private static TargetWebActor staffActor(WebAccountType accountType) {
        return new TargetWebActor(
                accountType,
                TrustedAudience.WEB_STAFF,
                STAFF_ID,
                PRINCIPAL_UID,
                TENANT_ID,
                TENANT_CODE,
                "租户甲",
                SESSION_UID,
                "旧工作人员名称",
                null,
                1,
                AUTH_VERSION,
                Instant.parse("2030-01-01T00:00:00Z"),
                Set.of(),
                List.of());
    }

    private static void readOnlyTransaction() {
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(true);
    }

    private static void writeTransaction() {
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(false);
    }
}
