package org.enveloping.ecobin.recycling.application.deliveryconfiguration;

import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.persistence.DeliveryScopePersistenceRef;
import org.enveloping.ecobin.identity.api.port.DeliveryScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeliveryScope;
import org.enveloping.ecobin.recycling.web.v1.DeliveryConfigurationModels
        .DeliveryConfigurationReleaseRequest;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import tools.jackson.databind.ObjectMapper;

import java.time.Clock;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class OrganizationDeliveryConfigurationServiceTest {

    private static final long TENANT_ID = 11L;
    private static final long ORGANIZATION_ID = 22L;
    private static final long STAFF_ID = 33L;
    private static final UUID OPERATION_UID = UUID.fromString(
            "10000000-0000-4000-8000-000000000001");
    private static final UUID PRINCIPAL_UID = UUID.fromString(
            "20000000-0000-4000-8000-000000000001");
    private static final UUID SESSION_UID = UUID.fromString(
            "30000000-0000-4000-8000-000000000001");
    private static final Instant NOW =
            Instant.parse("2026-08-01T03:04:05.123Z");

    @Mock
    private DeliveryScopeAuthorizationPort authorization;
    @Mock
    private OrganizationDeliveryConfigurationRepository repository;
    @Mock
    private AuditPort audit;
    @Mock
    private DeliveryScopePersistenceRef persistenceRef;

    private OrganizationDeliveryConfigurationService service;

    @BeforeEach
    @SuppressWarnings("unchecked")
    void setUp() {
        service = new OrganizationDeliveryConfigurationService(
                authorization,
                repository,
                audit,
                new ObjectMapper(),
                Clock.fixed(NOW, ZoneOffset.UTC));
        lenient().when(authorization.authorize(any()))
                .thenReturn(authorized(true));
        lenient().when(persistenceRef.withScopeOnce(any()))
                .thenAnswer(invocation -> {
                    DeliveryScopePersistenceRef.ScopeFunction<Object>
                            function = invocation.getArgument(0);
                    return function.apply(
                            TENANT_ID,
                            ORGANIZATION_ID,
                            null,
                            STAFF_ID);
                });
    }

    @Test
    void readsTheCurrentImmutableRuleInPublicUnits() {
        when(repository.findCurrent(scope()))
                .thenReturn(Optional.of(row(
                        1L,
                        -1_000L,
                        100_000L,
                        true)));

        var result = service.current(
                false,
                null,
                "organization-a");

        assertThat(result.versionNo()).isEqualTo(1);
        assertThat(result.reviewMode()).isEqualTo("ALL_MANUAL");
        assertThat(result.openBalanceFloorYuan())
                .isEqualTo("-10.00");
        assertThat(result.maxReviewAbsoluteWeightKg())
                .isEqualTo("100.000");
        assertThat(result.current()).isTrue();
    }

    @Test
    void paginatesImmutableVersionsByDescendingVersion() {
        when(repository.findVersions(scope(), null, 3))
                .thenReturn(List.of(
                        row(3, -3_000L, 300_000L, true),
                        row(2, -2_000L, 200_000L, false),
                        row(1, -1_000L, 100_000L, false)));

        var page = service.versions(
                false,
                null,
                "organization-a",
                null,
                2);

        assertThat(page.items())
                .extracting(item -> item.versionNo())
                .containsExactly(3L, 2L);
        assertThat(page.nextBeforeVersionNo()).isEqualTo(2L);
    }

    @Test
    void publishesOneNewVersionSwitchesTheHeadAndAudits() {
        DeliveryConfigurationRow current =
                row(1, -1_000L, 100_000L, true);
        when(audit.findSuccessful(OPERATION_UID))
                .thenReturn(Optional.empty());
        when(repository.lockCurrent(scope()))
                .thenReturn(Optional.of(current));
        when(repository.insertVersion(any(), any()))
                .thenReturn(99L);

        var result = service.release(
                false,
                null,
                "organization-a",
                OPERATION_UID,
                request(1, "-20.00", "150.000"));

        assertThat(result.versionNo()).isEqualTo(2);
        assertThat(result.openBalanceFloorYuan())
                .isEqualTo("-20.00");
        assertThat(result.maxReviewAbsoluteWeightKg())
                .isEqualTo("150.000");
        assertThat(result.publishedBy()).isEqualTo("规则管理员");
        ArgumentCaptor<NewDeliveryConfigurationVersion> inserted =
                ArgumentCaptor.forClass(
                        NewDeliveryConfigurationVersion.class);
        verify(repository).insertVersion(
                any(),
                inserted.capture());
        assertThat(inserted.getValue().versionNo()).isEqualTo(2);
        assertThat(inserted.getValue().openBalanceFloorCent())
                .isEqualTo(-2_000L);
        assertThat(inserted.getValue()
                .maxReviewAbsoluteWeightGram())
                .isEqualTo(150_000L);
        assertThat(inserted.getValue().publicationSource())
                .isEqualTo("STAFF");
        assertThat(inserted.getValue().publishedByStaffAccountId())
                .isEqualTo(STAFF_ID);
        verify(repository).switchCurrent(
                scope(),
                current,
                99L,
                2L,
                LocalDateTime.ofInstant(NOW, ZoneOffset.UTC));
        ArgumentCaptor<AuditEntry> auditEntry =
                ArgumentCaptor.forClass(AuditEntry.class);
        verify(audit).append(auditEntry.capture());
        assertThat(auditEntry.getValue().actionCode())
                .isEqualTo("delivery.configuration.release");
        assertThat(auditEntry.getValue().operationUid())
                .isEqualTo(OPERATION_UID);
    }

    @Test
    void refusesAnAutomaticReviewModeBeforeExecutionExists() {
        assertThatThrownBy(() -> service.release(
                false,
                null,
                "organization-a",
                OPERATION_UID,
                new DeliveryConfigurationReleaseRequest(
                        1L,
                        "AUTO_AFTER_24H",
                        "-10.00",
                        "100.000",
                        "not available")))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        failure -> {
                            assertThat(failure.status()).isEqualTo(422);
                            assertThat(failure.code()).isEqualTo(
                                    "DELIVERY.REVIEW_MODE_NOT_AVAILABLE");
                        });

        verify(repository, never()).lockCurrent(any());
    }

    @Test
    void reportsTheActualCurrentVersionOnConflict() {
        when(audit.findSuccessful(OPERATION_UID))
                .thenReturn(Optional.empty());
        when(repository.lockCurrent(scope()))
                .thenReturn(Optional.of(
                        row(2, -1_000L, 100_000L, true)));

        assertThatThrownBy(() -> service.release(
                false,
                null,
                "organization-a",
                OPERATION_UID,
                request(1, "-20.00", "150.000")))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        failure -> {
                            assertThat(failure.status()).isEqualTo(409);
                            assertThat(failure.code()).isEqualTo(
                                    "DELIVERY.CONFIGURATION_VERSION_CONFLICT");
                            assertThat(failure.details())
                                    .containsEntry(
                                            "currentVersion",
                                            2L);
                        });

        verify(repository, never()).insertVersion(any(), any());
    }

    @Test
    void requiresTheDedicatedManagementCapability() {
        when(authorization.authorize(any()))
                .thenReturn(authorized(false));

        assertThatThrownBy(() -> service.current(
                false,
                null,
                "organization-a"))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        failure -> {
                            assertThat(failure.status()).isEqualTo(403);
                            assertThat(failure.details())
                                    .containsEntry(
                                            "requiredCapability",
                                            "delivery.configuration.manage");
                        });

        verify(persistenceRef, never()).withScopeOnce(any());
    }

    private AuthorizedDeliveryScope authorized(
            boolean configurationManage) {
        return new AuthorizedDeliveryScope(
                false,
                PRINCIPAL_UID,
                SESSION_UID,
                "规则管理员",
                "tenant-a",
                "organization-a",
                !configurationManage,
                false,
                false,
                configurationManage,
                persistenceRef);
    }

    private static DeliveryConfigurationScope scope() {
        return new DeliveryConfigurationScope(
                TENANT_ID,
                ORGANIZATION_ID);
    }

    private static DeliveryConfigurationReleaseRequest request(
            long expectedVersion,
            String floor,
            String maximumWeight) {
        return new DeliveryConfigurationReleaseRequest(
                expectedVersion,
                "ALL_MANUAL",
                floor,
                maximumWeight,
                "adjust pilot rule");
    }

    private static DeliveryConfigurationRow row(
            long version,
            long floorCent,
            long maximumWeightGram,
            boolean current) {
        byte[] digest = new byte[32];
        digest[31] = (byte) version;
        return new DeliveryConfigurationRow(
                version,
                version,
                digest,
                "ALL_MANUAL",
                floorCent,
                maximumWeightGram,
                "STAFF",
                PRINCIPAL_UID,
                "规则管理员",
                LocalDateTime.ofInstant(
                        NOW.minusSeconds(version),
                        ZoneOffset.UTC),
                current,
                7L);
    }
}
