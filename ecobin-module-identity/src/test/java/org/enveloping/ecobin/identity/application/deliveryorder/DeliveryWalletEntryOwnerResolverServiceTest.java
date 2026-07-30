package org.enveloping.ecobin.identity.application.deliveryorder;

import org.enveloping.ecobin.identity.api.error.DeliveryIdentityFactMismatchException;
import org.enveloping.ecobin.identity.api.error.DeliveryIdentityFactMismatchException.Reason;
import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletEntryOwnerRef;
import org.enveloping.ecobin.identity.api.persistence.DeliveryWalletEntryOwnerRequestRef;
import org.enveloping.ecobin.identity.application.deliveryorder.DeliveryOrderIdentityRepository.OrganizationUserRow;
import org.enveloping.ecobin.identity.application.persistence.DeliveryWalletEntryOwnerRefFactory;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Map;
import java.util.Set;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class DeliveryWalletEntryOwnerResolverServiceTest {

    private static final long TENANT_ID = 11;
    private static final long ORGANIZATION_ID = 22;
    private static final long USER_ID = 33;
    private static final UUID USER_UID =
            UUID.fromString("11111111-1111-4111-8111-111111111111");
    private static final UUID ANOTHER_USER_UID =
            UUID.fromString("22222222-2222-4222-8222-222222222222");

    @Mock
    private DeliveryOrderIdentityRepository repository;

    @Mock
    private DeliveryWalletEntryOwnerRefFactory referenceFactory;

    @Mock
    private DeliveryWalletEntryOwnerRef ownerRef;

    private DeliveryWalletEntryOwnerResolverService service;

    @BeforeEach
    void setUp() {
        service = new DeliveryWalletEntryOwnerResolverService(
                repository,
                referenceFactory);
    }

    @Test
    void identityIssuesInternalIdAndPublicUidAsOneTrustedReference() {
        when(repository.findOrganizationUsers(Set.of(USER_ID)))
                .thenReturn(Map.of(
                        USER_ID,
                        new OrganizationUserRow(
                                TENANT_ID,
                                ORGANIZATION_ID,
                                USER_ID,
                                USER_UID)));
        when(referenceFactory.issue(
                TENANT_ID,
                ORGANIZATION_ID,
                USER_ID,
                USER_UID))
                .thenReturn(ownerRef);

        DeliveryWalletEntryOwnerRef result = service.resolve(
                new TestRequestRef(
                        TENANT_ID,
                        ORGANIZATION_ID,
                        USER_ID));

        assertThat(result).isSameAs(ownerRef);
    }

    @Test
    void mismatchedInternalIdAndPublicUidIsRejectedBeforeReferenceIssue() {
        long anotherUserId = USER_ID + 1;
        when(repository.findOrganizationUsers(Set.of(USER_ID)))
                .thenReturn(Map.of(
                        USER_ID,
                        new OrganizationUserRow(
                                TENANT_ID,
                                ORGANIZATION_ID,
                                anotherUserId,
                                ANOTHER_USER_UID)));

        assertThatThrownBy(() ->
                service.resolve(new TestRequestRef(
                        TENANT_ID,
                        ORGANIZATION_ID,
                        USER_ID)))
                .isInstanceOfSatisfying(
                        DeliveryIdentityFactMismatchException.class,
                        failure -> assertThat(failure.reason())
                                .isEqualTo(
                                        Reason.ORGANIZATION_USER_MISMATCH));
        verify(referenceFactory, never()).issue(
                TENANT_ID,
                ORGANIZATION_ID,
                USER_ID,
                ANOTHER_USER_UID);
    }

    private static final class TestRequestRef
            implements DeliveryWalletEntryOwnerRequestRef {

        private final long tenantId;
        private final long organizationId;
        private final long organizationUserId;
        private boolean consumed;

        private TestRequestRef(
                long tenantId,
                long organizationId,
                long organizationUserId) {
            this.tenantId = tenantId;
            this.organizationId = organizationId;
            this.organizationUserId = organizationUserId;
        }

        @Override
        public synchronized <T> T withRequestedOwnerOnce(
                RequestedOwnerFunction<T> function) {
            if (consumed) {
                throw new IllegalStateException(
                        "test owner request already consumed");
            }
            consumed = true;
            return function.apply(
                    tenantId,
                    organizationId,
                    organizationUserId);
        }
    }
}
