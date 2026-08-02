package org.enveloping.ecobin.device.application.startdelivery;

import org.enveloping.ecobin.device.api.command.StartDeliveryDeviceCommand;
import org.enveloping.ecobin.device.api.persistence.DeliverySessionBusinessFactsRef;
import org.enveloping.ecobin.device.api.persistence.DeviceDeliveryPortRef;
import org.enveloping.ecobin.device.api.port.StartDeliveryBusinessFactsPort;
import org.enveloping.ecobin.device.api.result.LockedStartDeliveryBusinessFacts;
import org.enveloping.ecobin.device.api.value.DeliveryRuleSnapshot;
import org.enveloping.ecobin.device.application.target.DeviceConfigurationCanonicalizer;
import org.enveloping.ecobin.framework.reliability.DeviceCommandTaskRef;
import org.enveloping.ecobin.framework.reliability.DeviceCommandTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskRegistrationPort;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.persistence.DeliverySessionOrganizationUserRef;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InOrder;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class StartDeliveryDeviceParticipationServiceTest {

    private static final long TENANT_ID = 11L;
    private static final long ORGANIZATION_ID = 12L;
    private static final long USER_ID = 13L;
    private static final long ASSET_ID = 101L;
    private static final long DEPLOYMENT_ID = 102L;
    private static final long CONFIGURATION_ID = 201L;
    private static final long PORT_ID = 202L;
    private static final long PORT_CONFIGURATION_ID = 203L;
    private static final String DEPLOYMENT_CODE = "Dp_demo_01";
    private static final UUID OPERATION_UID = UUID.fromString(
            "10000000-0000-4000-8000-000000000001");
    private static final UUID BAG_UID = UUID.fromString(
            "30000000-0000-4000-8000-000000000002");
    private static final LocalDateTime NOW =
            LocalDateTime.of(2026, 7, 24, 1, 0, 0);
    private static final byte[] CONTENT_SHA =
            java.util.HexFormat.of().parseHex("aa".repeat(32));
    private static final byte[] MCU_SHA =
            java.util.HexFormat.of().parseHex("bb".repeat(32));

    @Mock
    private StartDeliveryDeviceRepository repository;
    @Mock
    private StartDeliveryBusinessFactsPort businessFacts;
    @Mock
    private DeviceDeliveryPortRefFactory portRefFactory;
    @Mock
    private ReliableDeviceTaskRegistrationPort taskRegistration;
    @Mock
    private DeviceCommandTaskRefFactory taskRefFactory;
    @Mock
    private DeliverySessionOrganizationUserRef userRef;
    @Mock
    private DeviceDeliveryPortRef devicePortRef;
    @Mock
    private DeviceCommandTaskRef commandTaskRef;

    private final DeviceConfigurationCanonicalizer canonicalizer =
            new DeviceConfigurationCanonicalizer();
    private final ObjectMapper objectMapper = new ObjectMapper();
    private StartDeliveryDeviceParticipationService service;

    @BeforeEach
    void setUp() {
        TransactionSynchronizationManager
                .setActualTransactionActive(true);
        TransactionSynchronizationManager.initSynchronization();

        service = new StartDeliveryDeviceParticipationService(
                repository,
                businessFacts,
                portRefFactory,
                taskRegistration,
                taskRefFactory,
                canonicalizer,
                objectMapper);
        when(userRef.withDeliverySessionUserOnce(any()))
                .thenAnswer(invocation -> {
                    DeliverySessionOrganizationUserRef
                            .DeliverySessionUserFunction<?> function =
                            invocation.getArgument(0);
                    return function.apply(
                            TENANT_ID,
                            ORGANIZATION_ID,
                            USER_ID);
                });
    }

    @AfterEach
    void tearDown() {
        if (TransactionSynchronizationManager
                .isSynchronizationActive()) {
            TransactionSynchronizationManager.clearSynchronization();
        }
        TransactionSynchronizationManager
                .setActualTransactionActive(false);
    }

    @Test
    void persistsGoldenSemanticEnvelopeAndSingleAttemptTask()
            throws Exception {
        stubHappyPath();

        var result = service.start(command());

        ArgumentCaptor<StartDeliveryDeviceRepository.SessionInsert>
                sessionCaptor =
                ArgumentCaptor.forClass(
                        StartDeliveryDeviceRepository.SessionInsert.class);
        verify(repository).insertSession(sessionCaptor.capture());
        StartDeliveryDeviceRepository.SessionInsert session =
                sessionCaptor.getValue();
        assertThat(session.sessionUid()).isEqualTo(result.sessionUid());
        assertThat(session.bagUid()).isEqualTo(BAG_UID);
        assertThat(session.bagCode()).isEqualTo("BAG-001");
        assertThat(session.deviceConfigurationVersion()).isEqualTo(8);
        assertThat(session.negativeWeightThresholdGrams())
                .isEqualTo(500);
        assertThat(session.continueDeliveryWaitMs()).isEqualTo(30_000);
        assertThat(session.authorizationExpiresAt())
                .isEqualTo(NOW.plusSeconds(60));
        assertThat(session.resultRecoveryDeadlineAt())
                .isEqualTo(NOW.plusSeconds(60).plusHours(24));

        ArgumentCaptor<StartDeliveryDeviceRepository.CommandInsert>
                commandCaptor =
                ArgumentCaptor.forClass(
                        StartDeliveryDeviceRepository.CommandInsert.class);
        verify(repository).insertCommand(commandCaptor.capture());
        StartDeliveryDeviceRepository.CommandInsert persistedCommand =
                commandCaptor.getValue();
        JsonNode envelope = objectMapper.readTree(
                persistedCommand.semanticEnvelopeJson());
        JsonNode payload = envelope.get("payload");
        assertThat(envelope.get("schemaVersion").asInt()).isEqualTo(1);
        assertThat(envelope.get("commandType").asText())
                .isEqualTo("START_DELIVERY_SESSION");
        assertThat(envelope.get("deploymentCode").asText())
                .isEqualTo(DEPLOYMENT_CODE);
        assertThat(envelope.get("target").get("type").asText())
                .isEqualTo("DELIVERY_SESSION");
        assertThat(envelope.get("target").get("uid").asText())
                .isEqualTo(result.sessionUid().toString());
        assertThat(envelope.get("cosGrant").isNull()).isTrue();
        assertThat(payload.size()).isEqualTo(8);
        assertThat(payload.get("sessionUid").asText())
                .isEqualTo(result.sessionUid().toString());
        assertThat(payload.get("portNo").asInt()).isEqualTo(2);
        assertThat(payload.get("bagUid").asText())
                .isEqualTo(BAG_UID.toString());
        assertThat(payload.get("config").get("version").asLong())
                .isEqualTo(8);
        assertThat(payload.get("config").get("contentSha256").asText())
                .isEqualTo("aa".repeat(32));
        assertThat(payload.get("config").get("mcuPayloadSha256").asText())
                .isEqualTo("bb".repeat(32));
        assertThat(payload.get("unitPriceTenThousandths").asLong())
                .isEqualTo(4_500);
        assertThat(payload.get("continueDeliveryWaitMs").asLong())
                .isEqualTo(30_000);
        assertThat(payload.get("negativeWeightThresholdGrams").asLong())
                .isEqualTo(500);
        assertThat(payload.get("deliveryAutoCloseMs").asLong())
                .isEqualTo(120_000);
        assertThat(
                java.time.Duration.between(
                        java.time.Instant.parse(
                                envelope.get("issuedAt").asText()),
                        java.time.Instant.parse(
                                envelope.get("expiresAt").asText())))
                .isEqualTo(
                        StartDeliveryDeviceParticipationService
                                .START_AUTHORIZATION_WINDOW);

        Map<String, Object> expectedPayload =
                expectedPayload(result.sessionUid());
        byte[] expectedPayloadSha =
                canonicalizer.payloadSha256(expectedPayload);
        assertThat(envelope.get("payloadSha256").asText())
                .isEqualTo(canonicalizer.hex(expectedPayloadSha));
        Map<String, Object> expectedEnvelope =
                expectedEnvelope(
                        result.commandUid(),
                        result.sessionUid(),
                        expectedPayload,
                        expectedPayloadSha);
        assertThat(persistedCommand.semanticEnvelopeSha256())
                .containsExactly(
                        canonicalizer.payloadSha256(expectedEnvelope));

        ArgumentCaptor<ReliableDeviceTaskRegistration> taskCaptor =
                ArgumentCaptor.forClass(
                        ReliableDeviceTaskRegistration.class);
        verify(taskRegistration).register(taskCaptor.capture());
        ReliableDeviceTaskRegistration task = taskCaptor.getValue();
        assertThat(task.taskType())
                .isEqualTo("START_DELIVERY_SESSION");
        assertThat(task.targetType()).isEqualTo("DELIVERY_SESSION");
        assertThat(task.targetStableKey())
                .isEqualTo(result.sessionUid().toString());
        assertThat(task.taskKey()).isEqualTo(
                "START_DELIVERY_SESSION:"
                        + result.sessionUid().toString().toUpperCase());
        assertThat(task.maxAutoAttempts()).isEqualTo(1);
        assertThat(task.supersedePriorPendingTasks()).isFalse();
        assertThat(task.correlationUid()).isEqualTo(OPERATION_UID);
        assertThat(task.causationUid()).isNull();
        assertThat(task.payloadSha256()).containsExactly(
                persistedCommand.semanticEnvelopeSha256());

        InOrder order = inOrder(repository, businessFacts);
        order.verify(repository).lockActiveSessionIds(
                TENANT_ID,
                ORGANIZATION_ID,
                USER_ID);
        order.verify(repository).lockAsset(ASSET_ID);
        order.verify(repository).lockActiveDeployment(ASSET_ID);
        order.verify(repository).lockDeployment(DEPLOYMENT_ID);
        order.verify(repository).lockDeploymentRuntime(
                TENANT_ID,
                ORGANIZATION_ID,
                DEPLOYMENT_ID);
        order.verify(repository).lockOccupancy(ASSET_ID);
        order.verify(repository).lockLatestConfiguration(
                TENANT_ID,
                ORGANIZATION_ID,
                DEPLOYMENT_ID);
        order.verify(repository).lockConfigurationApplication(
                TENANT_ID,
                ORGANIZATION_ID,
                DEPLOYMENT_ID,
                CONFIGURATION_ID);
        order.verify(repository).lockPort(
                TENANT_ID,
                ORGANIZATION_ID,
                DEPLOYMENT_ID,
                2);
        order.verify(repository).lockPortConfiguration(
                TENANT_ID,
                ORGANIZATION_ID,
                DEPLOYMENT_ID,
                CONFIGURATION_ID,
                PORT_ID);
        order.verify(repository).lockPortRuntime(
                TENANT_ID,
                ORGANIZATION_ID,
                DEPLOYMENT_ID,
                PORT_ID);
        order.verify(businessFacts).lockForStart(any());
        order.verify(repository).insertSession(any());
        order.verify(repository).insertOccupancy(
                ASSET_ID,
                TENANT_ID,
                ORGANIZATION_ID,
                DEPLOYMENT_ID,
                301L,
                NOW);
        order.verify(repository).insertCommand(any());
    }

    @Test
    void rejectsReadOnlyOrangePiStorageBeforeWritingIntent() {
        stubHappyPath();
        when(repository.lockDeploymentRuntime(
                TENANT_ID,
                ORGANIZATION_ID,
                DEPLOYMENT_ID)).thenReturn(Optional.of(
                        runtime("OK", "READ_ONLY")));

        assertThatThrownBy(() -> service.start(command()))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        exception -> {
                            assertThat(exception.status()).isEqualTo(422);
                            assertThat(exception.code()).isEqualTo(
                                    "DEVICE.DEPLOYMENT_UNAVAILABLE");
                        });

        verify(businessFacts, never()).lockForStart(any());
        verify(repository, never()).insertSession(any());
        verify(repository, never()).insertOccupancy(
                any(Long.class),
                any(Long.class),
                any(Long.class),
                any(Long.class),
                any(Long.class),
                any(LocalDateTime.class));
        verify(repository, never()).insertCommand(any());
    }

    @Test
    void rejectsPortWithoutTrustedStableWeight() {
        stubHappyPath();
        when(repository.lockPortRuntime(
                TENANT_ID,
                ORGANIZATION_ID,
                DEPLOYMENT_ID,
                PORT_ID)).thenReturn(Optional.of(
                        portRuntime("UNSTABLE", false)));

        assertThatThrownBy(() -> service.start(command()))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        exception -> {
                            assertThat(exception.status()).isEqualTo(422);
                            assertThat(exception.code()).isEqualTo(
                                    "DEVICE.PORT_UNAVAILABLE");
                        });

        verify(businessFacts, never()).lockForStart(any());
        verify(repository, never()).insertSession(any());
    }

    @Test
    void acceptsFixedFrameLastObservedWeight() {
        stubHappyPath();
        when(repository.lockPortRuntime(
                TENANT_ID,
                ORGANIZATION_ID,
                DEPLOYMENT_ID,
                PORT_ID)).thenReturn(Optional.of(
                        portRuntime(
                                "STABLE",
                                true,
                                "LAST_OBSERVED")));

        service.start(command());

        verify(repository).insertSession(any());
        verify(repository).insertCommand(any());
    }

    @Test
    void rejectsASecondActiveSessionForTheSameUserFirst() {
        when(repository.lockActiveSessionIds(
                TENANT_ID,
                ORGANIZATION_ID,
                USER_ID)).thenReturn(List.of(999L));

        assertThatThrownBy(() -> service.start(command()))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        exception -> {
                            assertThat(exception.status()).isEqualTo(409);
                            assertThat(exception.code()).isEqualTo(
                                    "DELIVERY.SESSION_ALREADY_ACTIVE");
                        });

        verify(repository, never()).findAssetIdByDeploymentCode(any());
    }

    private void stubHappyPath() {
        when(repository.lockActiveSessionIds(
                TENANT_ID,
                ORGANIZATION_ID,
                USER_ID)).thenReturn(List.of());
        when(repository.findAssetIdByDeploymentCode(DEPLOYMENT_CODE))
                .thenReturn(Optional.of(ASSET_ID));
        when(repository.lockAsset(ASSET_ID)).thenReturn(Optional.of(
                new StartDeliveryDeviceRepository.AssetRow(
                        ASSET_ID,
                        "SN-DEMO-001",
                        "IN_USE",
                        2)));
        when(repository.lockActiveDeployment(ASSET_ID))
                .thenReturn(Optional.of(
                        new StartDeliveryDeviceRepository
                                .ActiveDeploymentRow(
                                ASSET_ID,
                                TENANT_ID,
                                ORGANIZATION_ID,
                                DEPLOYMENT_ID)));
        when(repository.lockDeployment(DEPLOYMENT_ID))
                .thenReturn(Optional.of(
                        new StartDeliveryDeviceRepository.DeploymentRow(
                                DEPLOYMENT_ID,
                                TENANT_ID,
                                ORGANIZATION_ID,
                                ASSET_ID,
                                DEPLOYMENT_CODE,
                                "ENABLED",
                                true)));
        when(repository.lockDeploymentRuntime(
                TENANT_ID,
                ORGANIZATION_ID,
                DEPLOYMENT_ID)).thenReturn(Optional.of(
                        runtime("OK", "HEALTHY")));
        when(repository.lockOccupancy(ASSET_ID))
                .thenReturn(Optional.empty());
        when(repository.lockLatestConfiguration(
                TENANT_ID,
                ORGANIZATION_ID,
                DEPLOYMENT_ID)).thenReturn(Optional.of(configuration()));
        when(repository.lockConfigurationApplication(
                TENANT_ID,
                ORGANIZATION_ID,
                DEPLOYMENT_ID,
                CONFIGURATION_ID)).thenReturn(Optional.of(
                        new StartDeliveryDeviceRepository
                                .ConfigurationApplicationRow(
                                "APPLIED",
                                8L,
                                CONTENT_SHA,
                                MCU_SHA,
                                NOW.minusMinutes(1))));
        when(repository.lockPort(
                TENANT_ID,
                ORGANIZATION_ID,
                DEPLOYMENT_ID,
                2)).thenReturn(Optional.of(
                        new StartDeliveryDeviceRepository.PortRow(
                                PORT_ID,
                                2)));
        when(repository.lockPortConfiguration(
                TENANT_ID,
                ORGANIZATION_ID,
                DEPLOYMENT_ID,
                CONFIGURATION_ID,
                PORT_ID)).thenReturn(Optional.of(
                        new StartDeliveryDeviceRepository
                                .PortConfigurationRow(
                                PORT_CONFIGURATION_ID,
                                true,
                                new BigDecimal("0.4500"),
                                "INFRARED_OR_WEIGHT",
                                4L)));
        when(repository.lockPortRuntime(
                TENANT_ID,
                ORGANIZATION_ID,
                DEPLOYMENT_ID,
                PORT_ID)).thenReturn(Optional.of(
                        portRuntime("STABLE", true)));
        when(repository.databaseNow()).thenReturn(NOW);
        lenient().when(portRefFactory.issue(
                TENANT_ID,
                ORGANIZATION_ID,
                DEPLOYMENT_ID,
                PORT_ID)).thenReturn(devicePortRef);
        lenient().when(businessFacts.lockForStart(any())).thenReturn(
                new LockedStartDeliveryBusinessFacts(
                        BAG_UID,
                        "BAG-001",
                        businessReference()));
        lenient().when(repository.insertSession(any()))
                .thenReturn(301L);
        lenient().when(repository.insertCommand(any()))
                .thenReturn(401L);
        lenient().when(taskRefFactory.issue(
                TENANT_ID,
                ORGANIZATION_ID,
                DEPLOYMENT_ID,
                401L)).thenReturn(commandTaskRef);
        lenient().when(taskRegistration.register(any())).thenReturn(
                UUID.fromString(
                        "40000000-0000-4000-8000-000000000001"));
    }

    private StartDeliveryDeviceCommand command() {
        return new StartDeliveryDeviceCommand(
                OPERATION_UID,
                DEPLOYMENT_CODE,
                2,
                userRef,
                new DeliveryRuleSnapshot(
                        3,
                        "cc".repeat(32),
                        -500,
                        100_000));
    }

    private static StartDeliveryDeviceRepository.ConfigurationRow
            configuration() {
        return new StartDeliveryDeviceRepository.ConfigurationRow(
                CONFIGURATION_ID,
                8,
                CONTENT_SHA,
                MCU_SHA,
                5_000,
                3,
                30_000,
                500,
                120_000);
    }

    private static StartDeliveryDeviceRepository.DeploymentRuntimeRow
            runtime(
                    String storageHealth,
                    String storageState) {
        return new StartDeliveryDeviceRepository.DeploymentRuntimeRow(
                "ONLINE",
                "SAFE",
                storageHealth,
                storageState,
                9001L,
                "DEVICE_RUNTIME_SNAPSHOT",
                1053L,
                NOW.minusSeconds(1),
                8L,
                CONTENT_SHA,
                MCU_SHA);
    }

    private static StartDeliveryDeviceRepository.PortRuntimeRow
            portRuntime(
                    String measurementStatus,
                    boolean valueAvailable) {
        return portRuntime(
                measurementStatus,
                valueAvailable,
                valueAvailable ? "STABLE_WINDOW_MEAN" : "NONE");
    }

    private static StartDeliveryDeviceRepository.PortRuntimeRow
            portRuntime(
                    String measurementStatus,
                    boolean valueAvailable,
                    String valueKind) {
        return new StartDeliveryDeviceRepository.PortRuntimeRow(
                "UNKNOWN",
                "DEENERGIZED",
                "OK",
                "OK",
                measurementStatus,
                valueAvailable,
                valueAvailable ? 13_250L : null,
                valueKind,
                4L,
                "NORMAL",
                "OK",
                0L,
                "SAFE",
                null,
                9001L,
                "DEVICE_RUNTIME_SNAPSHOT",
                1053L);
    }

    private static DeliverySessionBusinessFactsRef businessReference() {
        return new DeliverySessionBusinessFactsRef() {
            private boolean consumed;

            @Override
            public <T> T withBusinessForeignKeysOnce(
                    java.util.function.Function<
                            BusinessForeignKeys,
                            T> function) {
                if (consumed) {
                    throw new IllegalStateException("already consumed");
                }
                consumed = true;
                return function.apply(
                        new BusinessForeignKeys(
                                TENANT_ID,
                                ORGANIZATION_ID,
                                501L,
                                502L));
            }
        };
    }

    private Map<String, Object> expectedPayload(UUID sessionUid) {
        Map<String, Object> config = new LinkedHashMap<>();
        config.put("version", 8L);
        config.put("contentSha256", "aa".repeat(32));
        config.put("mcuPayloadSha256", "bb".repeat(32));
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("sessionUid", sessionUid.toString());
        payload.put("portNo", 2);
        payload.put("bagUid", BAG_UID.toString());
        payload.put("config", config);
        payload.put("unitPriceTenThousandths", 4_500L);
        payload.put("continueDeliveryWaitMs", 30_000L);
        payload.put("negativeWeightThresholdGrams", 500L);
        payload.put("deliveryAutoCloseMs", 120_000L);
        return payload;
    }

    private Map<String, Object> expectedEnvelope(
            UUID commandUid,
            UUID sessionUid,
            Map<String, Object> payload,
            byte[] payloadSha) {
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schemaVersion", 1);
        envelope.put("commandUid", commandUid.toString());
        envelope.put("commandType", "START_DELIVERY_SESSION");
        envelope.put("deploymentCode", DEPLOYMENT_CODE);
        envelope.put(
                "target",
                Map.of(
                        "type",
                        "DELIVERY_SESSION",
                        "uid",
                        sessionUid.toString()));
        envelope.put(
                "issuedAt",
                NOW.toInstant(ZoneOffset.UTC).toString());
        envelope.put(
                "expiresAt",
                NOW.plusSeconds(60)
                        .toInstant(ZoneOffset.UTC)
                        .toString());
        envelope.put("payloadSchemaVersion", 1);
        envelope.put(
                "payloadSha256",
                canonicalizer.hex(payloadSha));
        envelope.put("payload", payload);
        envelope.put("cosGrant", null);
        return envelope;
    }
}
