package org.enveloping.ecobin.recycling.application.deliveryorder;

import org.enveloping.ecobin.device.api.persistence.DeliveryOrderDeviceFactsRef;
import org.enveloping.ecobin.device.api.persistence.DeliveryOrderDeviceFilterRef;
import org.enveloping.ecobin.device.api.port.DeliveryOrderDeviceFactsQueryPort;
import org.enveloping.ecobin.device.api.port.DeliveryOrderDeviceFilterQueryPort;
import org.enveloping.ecobin.device.api.query.DeliveryOrderDeviceFilterQuery;
import org.enveloping.ecobin.device.api.result.DeliveryOrderDeviceFacts;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.enveloping.ecobin.identity.api.persistence.DeliveryOrderIdentityBatchRef;
import org.enveloping.ecobin.identity.api.persistence.DeliveryQueryOrganizationUserRef;
import org.enveloping.ecobin.identity.api.persistence.DeliveryScopePersistenceRef;
import org.enveloping.ecobin.identity.api.port.DeliveryOrderIdentityQueryPort;
import org.enveloping.ecobin.identity.api.port.DeliveryScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.port.MiniappDeliveryIdentityQueryPort;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeliveryScope;
import org.enveloping.ecobin.identity.api.result.CurrentMiniappDeliveryIdentity;
import org.enveloping.ecobin.identity.api.result.DeliveryOrderIdentityFacts;
import org.enveloping.ecobin.identity.api.value.DeliveryIdentityFactToken;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.CursorPage;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.MiniappDeliveryOrderDetail;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.MiniappDeliveryOrderItem;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.WebDeliveryOrderItem;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.json.JsonMapper;

import java.lang.reflect.RecordComponent;
import java.math.BigDecimal;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class DeliveryOrderQueryServiceTest {

    private static final long TENANT_ID = 11L;
    private static final long ORGANIZATION_ID = 12L;
    private static final long ORGANIZATION_USER_ID = 13L;
    private static final String TENANT_CODE = "T_demo";
    private static final String ORGANIZATION_CODE = "ORG_demo";
    private static final UUID ORGANIZATION_USER_UID = UUID.fromString(
            "10000000-0000-4000-8000-000000000001");
    private static final UUID PRINCIPAL_UID = UUID.fromString(
            "20000000-0000-4000-8000-000000000001");
    private static final UUID SESSION_UID = UUID.fromString(
            "30000000-0000-4000-8000-000000000001");
    private static final UUID EVENT_UID = UUID.fromString(
            "40000000-0000-4000-8000-000000000001");
    private static final UUID DEVICE_SESSION_UID = UUID.fromString(
            "50000000-0000-4000-8000-000000000001");
    private static final Instant NOW =
            Instant.parse("2026-07-29T12:00:00Z");
    private static final Clock FIXED_CLOCK =
            Clock.fixed(NOW, ZoneOffset.UTC);

    @Mock
    private MiniappDeliveryIdentityQueryPort miniappIdentity;
    @Mock
    private DeliveryScopeAuthorizationPort authorization;
    @Mock
    private DeliveryOrderIdentityQueryPort identityFacts;
    @Mock
    private DeliveryOrderDeviceFactsQueryPort deviceFacts;
    @Mock
    private DeliveryOrderDeviceFilterQueryPort deviceFilters;
    @Mock
    private JdbcDeliveryOrderRepository repository;
    @Mock
    private DeliveryOrderCursorCodec cursorCodec;
    @Mock
    private CurrentMiniappDeliveryIdentity currentMiniapp;
    @Mock
    private DeliveryQueryOrganizationUserRef miniappUserRef;
    @Mock
    private DeliveryScopePersistenceRef scopeRef;
    @Mock
    private DeliveryOrderDeviceFilterRef deviceFilterRef;

    private final Object transactionResourceKey = new Object();
    private final Object transactionResource = new Object();
    private final ObjectMapper objectMapper = JsonMapper.builder()
            .findAndAddModules()
            .build();

    private DeliveryOrderQueryService service;

    @BeforeEach
    void setUp() {
        TransactionSynchronizationManager
                .setActualTransactionActive(true);
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(true);
        TransactionSynchronizationManager.initSynchronization();
        TransactionSynchronizationManager.bindResource(
                transactionResourceKey,
                transactionResource);
        service = new DeliveryOrderQueryService(
                miniappIdentity,
                authorization,
                identityFacts,
                deviceFacts,
                deviceFilters,
                repository,
                cursorCodec,
                objectMapper,
                FIXED_CLOCK);
    }

    @AfterEach
    void tearDown() {
        if (TransactionSynchronizationManager
                .isSynchronizationActive()) {
            TransactionSynchronizationManager.getSynchronizations()
                    .forEach(synchronization ->
                            synchronization.afterCompletion(
                                    TransactionSynchronization
                                            .STATUS_COMMITTED));
            TransactionSynchronizationManager.clearSynchronization();
        }
        if (TransactionSynchronizationManager.hasResource(
                transactionResourceKey)) {
            TransactionSynchronizationManager.unbindResource(
                    transactionResourceKey);
        }
        TransactionSynchronizationManager
                .setCurrentTransactionReadOnly(false);
        TransactionSynchronizationManager
                .setActualTransactionActive(false);
    }

    @Test
    void miniappListIsFixedToCurrentUserAndHasNoOwnershipDto() {
        answerMiniappIdentityForList();
        answerResolvedDeviceFacts();
        DeliveryOrderSummaryRow row =
                summary(101L, "DO2026072900101", time(10));
        when(repository.currentHighWatermark(any()))
                .thenReturn(800L);
        when(repository.findPage(any()))
                .thenReturn(List.of(row));

        var page = service.miniappOrders(
                null,
                20,
                null);

        ArgumentCaptor<DeliveryOrderPageQuery> query =
                ArgumentCaptor.forClass(
                        DeliveryOrderPageQuery.class);
        verify(repository).findPage(query.capture());
        assertThat(query.getValue().scope())
                .isEqualTo(new DeliveryOrderScope(
                        TENANT_ID,
                        ORGANIZATION_ID,
                        ORGANIZATION_USER_ID));
        assertThat(page.items()).hasSize(1);
        assertThat(page.items().getFirst().deploymentCode())
                .isEqualTo("Dp_public_01");
        assertThat(recordComponentNames(
                MiniappDeliveryOrderItem.class))
                .doesNotContain(
                        "ownership",
                        "organizationUserUid");
        assertThat(recordComponentNames(
                MiniappDeliveryOrderDetail.class))
                .doesNotContain("ownership");
        verifyNoInteractions(identityFacts);
    }

    @Test
    void deliveryReadListMergesDeviceAndIdentityPublicFacts() {
        when(authorization.authorize(any()))
                .thenReturn(authorized(true, false, false));
        answerScopeRef();
        answerResolvedDeviceFacts();
        answerOrganizationUserFacts();
        DeliveryOrderSummaryRow row =
                summary(101L, "DO2026072900101", time(10));
        when(repository.currentHighWatermark(any()))
                .thenReturn(800L);
        when(repository.findPage(any()))
                .thenReturn(List.of(row));

        var page = webOrders(20, null, null, null);

        assertThat(page.items()).singleElement()
                .satisfies(item -> {
                    assertThat(item.organizationUserUid())
                            .isEqualTo(
                                    ORGANIZATION_USER_UID);
                    assertThat(item.deploymentCode())
                            .isEqualTo("Dp_public_01");
                    assertThat(item.portNo()).isEqualTo(2);
                });
        verify(deviceFacts).facts(any());
        verify(identityFacts).resolveFacts(any());
    }

    @Test
    void reviewExecuteOnlyListForcesPendingStatus() {
        when(authorization.authorize(any()))
                .thenReturn(authorized(false, true, false));
        answerScopeRef();
        when(repository.currentHighWatermark(any()))
                .thenReturn(800L);
        when(repository.findPage(any()))
                .thenReturn(List.of());

        webOrders(20, "APPROVED", null, null);

        ArgumentCaptor<DeliveryOrderPageQuery> query =
                ArgumentCaptor.forClass(
                        DeliveryOrderPageQuery.class);
        verify(repository).findPage(query.capture());
        assertThat(query.getValue().reviewStatus())
                .isEqualTo("PENDING");
    }

    @Test
    void deliveryCorrectOnlyCannotOpenTheList() {
        when(authorization.authorize(any()))
                .thenReturn(authorized(false, false, true));

        assertThatThrownBy(() ->
                webOrders(20, null, null, null))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        failure -> {
                            assertThat(failure.status())
                                    .isEqualTo(403);
                            assertThat(failure.code())
                                    .isEqualTo(
                                            "AUTH.CAPABILITY_REQUIRED");
                        });
        verify(repository, never()).findPage(any());
    }

    @Test
    void deliveryCorrectOnlyReadsApprovedDetailInSafeMode() {
        when(authorization.authorize(any()))
                .thenReturn(authorized(false, false, true));
        answerScopeRef();
        answerResolvedDeviceFacts();
        answerOrganizationUserFacts();
        DeliveryOrderRootRow root = approvedRoot(
                101L,
                "DO2026072900101");
        when(repository.findDetail(
                new DeliveryOrderScope(
                        TENANT_ID,
                        ORGANIZATION_ID,
                        null),
                root.deliveryOrderNo()))
                .thenReturn(Optional.of(root));
        when(repository.findAnomalies(root.id()))
                .thenReturn(List.of(new DeliveryAnomalyRow(
                        "WEIGHT",
                        "RAW_WEIGHT_OUT_OF_RANGE",
                        "{\"privateInternalKey\":42}",
                        time(11))));
        when(repository.findPhotos(root.id()))
                .thenReturn(completePhotos());

        var detail = service.webOrder(
                true,
                TENANT_CODE,
                ORGANIZATION_CODE,
                root.deliveryOrderNo());

        assertThat(detail.ownership().organizationUserUid())
                .isEqualTo(ORGANIZATION_USER_UID);
        assertThat(detail.revisions()).isEmpty();
        assertThat(detail.anomalies()).singleElement()
                .satisfies(anomaly ->
                        assertThat(anomaly.diagnosticDetails())
                                .isNull());
        verify(repository, never()).findRevisions(anyLong());
    }

    @Test
    void deliveryReadDetailPreservesNullDiagnosticValues() {
        when(authorization.authorize(any()))
                .thenReturn(authorized(true, false, false));
        answerScopeRef();
        answerResolvedDeviceFacts();
        answerOrganizationUserFacts();
        DeliveryOrderRootRow root = approvedRoot(
                102L,
                "DO2026072900102");
        when(repository.findDetail(
                new DeliveryOrderScope(
                        TENANT_ID,
                        ORGANIZATION_ID,
                        null),
                root.deliveryOrderNo()))
                .thenReturn(Optional.of(root));
        when(repository.findAnomalies(root.id()))
                .thenReturn(List.of(new DeliveryAnomalyRow(
                        "WEIGHT",
                        "RAW_WEIGHT_OUT_OF_RANGE",
                        "{\"sample\":null}",
                        time(11))));
        when(repository.findPhotos(root.id()))
                .thenReturn(completePhotos());
        when(repository.findRevisions(root.id()))
                .thenReturn(List.of());

        var detail = service.webOrder(
                true,
                TENANT_CODE,
                ORGANIZATION_CODE,
                root.deliveryOrderNo());

        assertThat(detail.anomalies()).singleElement()
                .satisfies(anomaly -> {
                    assertThat(anomaly.diagnosticDetails())
                            .containsKey("sample");
                    assertThat(anomaly.diagnosticDetails().get("sample"))
                            .isNull();
                });
    }

    @Test
    void miniappDetailOutsideCurrentUserScopeReturnsNotFound() {
        answerMiniappIdentityScope();
        when(repository.findDetail(
                new DeliveryOrderScope(
                        TENANT_ID,
                        ORGANIZATION_ID,
                        ORGANIZATION_USER_ID),
                "DO_OTHER_USER"))
                .thenReturn(Optional.empty());

        assertNotFound(() ->
                service.miniappOrder("DO_OTHER_USER"));
    }

    @Test
    void missingWebDetailReturnsNotFoundInsideAuthorizedScope() {
        when(authorization.authorize(any()))
                .thenReturn(authorized(true, false, false));
        answerScopeRef();
        when(repository.findDetail(
                new DeliveryOrderScope(
                        TENANT_ID,
                        ORGANIZATION_ID,
                        null),
                "DO_MISSING"))
                .thenReturn(Optional.empty());

        assertNotFound(() -> service.webOrder(
                true,
                TENANT_CODE,
                ORGANIZATION_CODE,
                "DO_MISSING"));
    }

    @Test
    void portNumberOnlyPassesEveryResolvedPortIdToRepository() {
        when(authorization.authorize(any()))
                .thenReturn(authorized(true, false, false));
        when(deviceFilters.resolveFilter(any()))
                .thenReturn(Optional.of(deviceFilterRef));
        answerDeviceFilterRef(
                null,
                List.of(201L, 202L, 203L));
        when(repository.currentHighWatermark(any()))
                .thenReturn(800L);
        when(repository.findPage(any()))
                .thenReturn(List.of());

        webOrders(20, null, null, 2);

        ArgumentCaptor<DeliveryOrderDeviceFilterQuery> filterQuery =
                ArgumentCaptor.forClass(
                        DeliveryOrderDeviceFilterQuery.class);
        verify(deviceFilters).resolveFilter(
                filterQuery.capture());
        assertThat(filterQuery.getValue().deploymentCode())
                .isNull();
        assertThat(filterQuery.getValue().portNo()).isEqualTo(2);
        assertThat(filterQuery.getValue().scopeRef())
                .isSameAs(scopeRef);

        ArgumentCaptor<DeliveryOrderPageQuery> pageQuery =
                ArgumentCaptor.forClass(
                        DeliveryOrderPageQuery.class);
        verify(repository).findPage(pageQuery.capture());
        assertThat(pageQuery.getValue().deploymentId()).isNull();
        assertThat(pageQuery.getValue().portIds())
                .containsExactly(201L, 202L, 203L);
    }

    @Test
    void pageFetchesLimitPlusOneAndAnchorsAfterLastReturnedRow() {
        when(authorization.authorize(any()))
                .thenReturn(authorized(true, false, false));
        answerScopeRef();
        answerResolvedDeviceFacts();
        answerOrganizationUserFacts();
        DeliveryOrderSummaryRow first =
                summary(101L, "DO2026072900103", time(13));
        DeliveryOrderSummaryRow second =
                summary(102L, "DO2026072900102", time(12));
        DeliveryOrderSummaryRow lookAhead =
                summary(103L, "DO2026072900101", time(11));
        when(repository.currentHighWatermark(any()))
                .thenReturn(900L);
        when(repository.findPage(any()))
                .thenReturn(List.of(first, second, lookAhead));
        when(cursorCodec.encode(
                eq(900L),
                eq(second.sortOccurredAt()),
                eq(second.deliveryOrderNo()),
                anyString()))
                .thenReturn("next-cursor");

        var page = webOrders(2, null, null, null);

        ArgumentCaptor<DeliveryOrderPageQuery> query =
                ArgumentCaptor.forClass(
                        DeliveryOrderPageQuery.class);
        verify(repository).findPage(query.capture());
        assertThat(query.getValue().limitPlusOne())
                .isEqualTo(3);
        assertThat(page.items())
                .extracting(item -> item.deliveryOrderNo())
                .containsExactly(
                        first.deliveryOrderNo(),
                        second.deliveryOrderNo());
        assertThat(page.nextCursor()).isEqualTo("next-cursor");
        verify(cursorCodec).encode(
                eq(900L),
                eq(second.sortOccurredAt()),
                eq(second.deliveryOrderNo()),
                anyString());
    }

    @Test
    void unresolvedDeviceTupleFailsInsteadOfDroppingOrder() {
        answerMiniappIdentityForList();
        DeliveryOrderSummaryRow row =
                summary(101L, "DO2026072900101", time(10));
        when(repository.currentHighWatermark(any()))
                .thenReturn(800L);
        when(repository.findPage(any()))
                .thenReturn(List.of(row));
        when(deviceFacts.facts(any()))
                .thenAnswer(invocation -> {
                    DeliveryOrderDeviceFactsRef reference =
                            invocation.getArgument(0);
                    return reference.withFactKeysOnce(keys ->
                            keys.facts().stream()
                                    .map(key ->
                                            DeliveryOrderDeviceFacts
                                                    .missing(
                                                            key.token()))
                                    .toList());
                });

        assertThatThrownBy(() ->
                service.miniappOrders(null, 20, null))
                .isInstanceOf(IllegalStateException.class)
                .hasMessage(
                        "delivery order device facts are incomplete");
    }

    private void answerMiniappIdentityForList() {
        answerMiniappIdentityScope();
        when(currentMiniapp.tenantCode()).thenReturn(TENANT_CODE);
        when(currentMiniapp.organizationCode())
                .thenReturn(ORGANIZATION_CODE);
        when(currentMiniapp.organizationUserUid())
                .thenReturn(new OrganizationUserUid(
                        ORGANIZATION_USER_UID));
    }

    private void answerMiniappIdentityScope() {
        when(miniappIdentity.current()).thenReturn(currentMiniapp);
        when(currentMiniapp.deliveryQueryUserRef())
                .thenReturn(miniappUserRef);
        when(miniappUserRef.withDeliveryQueryUserOnce(any()))
                .thenAnswer(invocation -> {
                    DeliveryQueryOrganizationUserRef
                            .DeliveryQueryUserFunction<?> function =
                            invocation.getArgument(0);
                    return function.apply(
                            TENANT_ID,
                            ORGANIZATION_ID,
                            ORGANIZATION_USER_ID,
                            ORGANIZATION_USER_UID);
                });
    }

    private void answerScopeRef() {
        when(scopeRef.withScopeOnce(any()))
                .thenAnswer(invocation -> {
                    DeliveryScopePersistenceRef.ScopeFunction<?>
                            function = invocation.getArgument(0);
                    return function.apply(
                            TENANT_ID,
                            ORGANIZATION_ID,
                            501L,
                            null);
                });
    }

    private void answerDeviceFilterRef(
            Long deploymentId,
            List<Long> portIds) {
        when(deviceFilterRef.withFilterKeysOnce(any()))
                .thenAnswer(invocation -> {
                    DeliveryOrderDeviceFilterRef.FilterFunction<?>
                            function = invocation.getArgument(0);
                    return function.apply(
                            TENANT_ID,
                            ORGANIZATION_ID,
                            deploymentId,
                            portIds);
                });
    }

    private void answerResolvedDeviceFacts() {
        when(deviceFacts.facts(any()))
                .thenAnswer(invocation -> {
                    DeliveryOrderDeviceFactsRef reference =
                            invocation.getArgument(0);
                    return reference.withFactKeysOnce(keys ->
                            keys.facts().stream()
                                    .map(key ->
                                            new DeliveryOrderDeviceFacts(
                                                    key.token(),
                                                    EVENT_UID,
                                                    DEVICE_SESSION_UID,
                                                    "Dp_public_01",
                                                    2))
                                    .toList());
                });
    }

    private void answerOrganizationUserFacts() {
        when(identityFacts.resolveFacts(any()))
                .thenAnswer(invocation -> {
                    DeliveryOrderIdentityBatchRef reference =
                            invocation.getArgument(0);
                    Map<DeliveryIdentityFactToken, OrganizationUserUid>
                            users = new HashMap<>();
                    reference.consumeOnce(
                            new DeliveryOrderIdentityBatchRef.EntrySink() {
                                @Override
                                public void organizationUser(
                                        DeliveryIdentityFactToken token,
                                        long tenantKey,
                                        long organizationKey,
                                        long organizationUserKey) {
                                    assertThat(tenantKey)
                                            .isEqualTo(TENANT_ID);
                                    assertThat(organizationKey)
                                            .isEqualTo(
                                                    ORGANIZATION_ID);
                                    assertThat(organizationUserKey)
                                            .isEqualTo(
                                                    ORGANIZATION_USER_ID);
                                    users.put(
                                            token,
                                            new OrganizationUserUid(
                                                    ORGANIZATION_USER_UID));
                                }

                                @Override
                                public void reviewer(
                                        DeliveryIdentityFactToken token,
                                        long tenantKey,
                                        long organizationKey,
                                        DeliveryOrderIdentityBatchRef
                                                .ReviewerKind reviewerKind,
                                        Long platformAdminKey,
                                        Long staffAccountKey) {
                                    throw new AssertionError(
                                            "reviewer facts were not expected");
                                }
                            });
                    return new DeliveryOrderIdentityFacts(
                            users,
                            Map.of());
                });
    }

    private AuthorizedDeliveryScope authorized(
            boolean deliveryRead,
            boolean reviewExecute,
            boolean deliveryCorrect) {
        return new AuthorizedDeliveryScope(
                true,
                PRINCIPAL_UID,
                SESSION_UID,
                "Platform tester",
                TENANT_CODE,
                ORGANIZATION_CODE,
                deliveryRead,
                reviewExecute,
                deliveryCorrect,
                false,
                scopeRef);
    }

    private CursorPage<WebDeliveryOrderItem> webOrders(
            Integer limit,
            String reviewStatus,
            String deploymentCode,
            Integer portNo) {
        return service.webOrders(
                true,
                TENANT_CODE,
                ORGANIZATION_CODE,
                null,
                limit,
                reviewStatus,
                null,
                null,
                null,
                deploymentCode,
                portNo,
                null,
                null);
    }

    private static DeliveryOrderSummaryRow summary(
            long id,
            String orderNo,
            LocalDateTime sortTime) {
        return new DeliveryOrderSummaryRow(
                id,
                orderNo,
                ORGANIZATION_USER_ID,
                101L,
                201L,
                301L,
                401L,
                sortTime,
                sortTime.plusSeconds(2),
                sortTime,
                new BigDecimal("1.25"),
                100L,
                "RELIABLE",
                false,
                "PENDING",
                0L,
                null,
                null,
                List.of(),
                "COMPLETE");
    }

    private static DeliveryOrderRootRow approvedRoot(
            long id,
            String orderNo) {
        return new DeliveryOrderRootRow(
                id,
                orderNo,
                ORGANIZATION_USER_ID,
                101L,
                201L,
                301L,
                401L,
                time(10),
                time(10).plusSeconds(2),
                "RELIABLE",
                1_000L,
                "RELIABLE",
                2_250L,
                1_250L,
                new BigDecimal("1.25"),
                100L,
                "RELIABLE",
                false,
                new BigDecimal("0.8000"),
                false,
                "APPROVED",
                1L,
                100_000L,
                new BigDecimal("1.25"),
                100L,
                time(11));
    }

    private static List<DeliveryPhotoRow> completePhotos() {
        return List.of(
                photo("BEFORE_INNER"),
                photo("BEFORE_OUTER"),
                photo("AFTER_INNER"),
                photo("AFTER_OUTER"));
    }

    private static DeliveryPhotoRow photo(String position) {
        return new DeliveryPhotoRow(
                position,
                "AVAILABLE",
                "https://example.test/" + position,
                time(10),
                null);
    }

    private static LocalDateTime time(int minute) {
        return LocalDateTime.of(
                2026,
                7,
                29,
                10,
                minute);
    }

    private static List<String> recordComponentNames(
            Class<?> recordType) {
        return Arrays.stream(recordType.getRecordComponents())
                .map(RecordComponent::getName)
                .toList();
    }

    private static void assertNotFound(Runnable invocation) {
        assertThatThrownBy(invocation::run)
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        failure -> {
                            assertThat(failure.status())
                                    .isEqualTo(404);
                                    assertThat(failure.code())
                                            .isEqualTo(
                                            "RESOURCE.NOT_FOUND");
                        });
    }
}
