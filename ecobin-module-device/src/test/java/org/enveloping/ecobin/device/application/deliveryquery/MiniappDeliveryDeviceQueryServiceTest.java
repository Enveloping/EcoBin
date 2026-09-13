package org.enveloping.ecobin.device.application.deliveryquery;

import org.enveloping.ecobin.device.api.persistence.DeliverySessionBusinessQueryRef;
import org.enveloping.ecobin.device.api.query.OwnedDeliverySessionQuery;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.persistence.DeliveryQueryOrganizationUserRef;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.time.LocalDateTime;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class MiniappDeliveryDeviceQueryServiceTest {

    private static final long TENANT_ID = 11L;
    private static final long ORGANIZATION_ID = 12L;
    private static final long USER_ID = 13L;
    private static final UUID USER_UID = UUID.fromString(
            "10000000-0000-4000-8000-000000000001");
    private static final UUID SESSION_UID = UUID.fromString(
            "20000000-0000-4000-8000-000000000001");
    private static final LocalDateTime STARTED_AT =
            LocalDateTime.of(2026, 7, 29, 2, 0, 0);

    @Mock
    private MiniappDeliveryDeviceQueryRepository repository;
    @Mock
    private DeliveryReadQueryRefFactory queryRefFactory;
    @Mock
    private DeliveryQueryOrganizationUserRef userRef;
    @Mock
    private DeliverySessionBusinessQueryRef sessionBusinessRef;

    private MiniappDeliveryDeviceQueryService service;

    @BeforeEach
    void setUp() {
        TransactionSynchronizationManager
                .setActualTransactionActive(true);
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(true);
        TransactionSynchronizationManager.initSynchronization();
        service = new MiniappDeliveryDeviceQueryService(
                repository,
                queryRefFactory);
        when(userRef.withDeliveryQueryUserOnce(any()))
                .thenAnswer(invocation -> {
                    DeliveryQueryOrganizationUserRef
                            .DeliveryQueryUserFunction<?> function =
                            invocation.getArgument(0);
                    return function.apply(
                            TENANT_ID,
                            ORGANIZATION_ID,
                            USER_ID,
                            USER_UID);
                });
    }

    @AfterEach
    void tearDown() {
        if (TransactionSynchronizationManager
                .isSynchronizationActive()) {
            TransactionSynchronizationManager.clearSynchronization();
        }
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(false);
        TransactionSynchronizationManager
                .setActualTransactionActive(false);
    }

    @Test
    void missingAndSomeoneElsesSessionShareTheSameNotFoundBoundary() {
        when(repository.findOwnedSession(
                TENANT_ID,
                ORGANIZATION_ID,
                USER_ID,
                SESSION_UID)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.ownedSession(
                new OwnedDeliverySessionQuery(
                        SESSION_UID,
                        userRef)))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        exception -> {
                            assertThat(exception.status()).isEqualTo(404);
                            assertThat(exception.code())
                                    .isEqualTo("RESOURCE.NOT_FOUND");
                        });

        verify(repository).findOwnedSession(
                TENANT_ID,
                ORGANIZATION_ID,
                USER_ID,
                SESSION_UID);
    }

    @Test
    void returnsOnlyAnOwnedSessionAndIssuesOpaqueBusinessReference() {
        when(repository.findOwnedSession(
                TENANT_ID,
                ORGANIZATION_ID,
                USER_ID,
                SESSION_UID)).thenReturn(Optional.of(
                        new MiniappDeliveryDeviceQueryRepository
                                .OwnedSessionRow(
                                301L,
                                SESSION_UID,
                                "IN_PROGRESS",
                                "Dv_0123456789abcdefghijklmn",
                                2,
                                STARTED_AT,
                                null,
                                null,
                                null,
                                null)));
        when(queryRefFactory.issueSessionBusiness(
                TENANT_ID,
                ORGANIZATION_ID,
                301L)).thenReturn(sessionBusinessRef);

        var result = service.ownedSession(
                new OwnedDeliverySessionQuery(
                        SESSION_UID,
                        userRef));

        assertThat(result.sessionUid()).isEqualTo(SESSION_UID);
        assertThat(result.deviceCode())
                .isEqualTo("Dv_0123456789abcdefghijklmn");
        assertThat(result.firstPhysicalProgressAt())
                .isEqualTo(
                        STARTED_AT.toInstant(
                                java.time.ZoneOffset.UTC));
        assertThat(result.businessQueryRef())
                .isSameAs(sessionBusinessRef);
    }
}
