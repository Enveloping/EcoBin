package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.port.TrustedDeviceAcceptanceChallengePort;
import org.enveloping.ecobin.device.api.result.DeviceAcceptanceChallengeConsumeResult;
import org.enveloping.ecobin.device.api.result.DeviceAcceptanceEvidenceApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceAcceptanceEvent;
import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRef;
import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistrationPort;
import org.enveloping.ecobin.framework.reliability.TrustedPlatformInboxRef;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class TrustedDeviceAcceptanceEvidenceServiceTest {

    private static final String DEVICE_PUBLIC_CODE = "Dv_test-public-code";
    private static final DeviceEntryUrlFactory DEVICE_ENTRY_URL_FACTORY =
            new DeviceEntryUrlFactory(
                    "https://www.jinshoubao.com/device-entry/");

    @Test
    void simulationProvenanceDoesNotFailFunctionalAcceptance() {
        TrustedDeviceAcceptanceEvidenceService service = service();
        LocalDateTime observedAt = LocalDateTime.of(
                2026, 8, 7, 12, 0);

        var failures = service.failures(
                new TrustedDeviceAcceptanceEvidenceService.AssetState(
                        1L, DEVICE_PUBLIC_CODE, 1, 1L, new byte[32],
                        "PENDING", 0L, true, true),
                healthyEvidence(true, true),
                observedAt,
                observedAt.plusSeconds(1));

        assertThat(failures).isEmpty();
    }

    @Test
    void configuredRuntimeVersionIsAcceptedButRetiredCandidatesAndUnknownVersionAreRejected() {
        TrustedDeviceAcceptanceEvidenceService service = service(
                "hardware-runtime-20260831-13, hardware-runtime-20260903-19, "
                        + "hardware-runtime-20260903-21, "
                        + "hardware-runtime-20260904-23");
        LocalDateTime observedAt = LocalDateTime.of(
                2026, 9, 3, 2, 0);
        var asset = new TrustedDeviceAcceptanceEvidenceService.AssetState(
                1L, DEVICE_PUBLIC_CODE, 1, 1L, new byte[32],
                "PENDING", 0L, true, true);

        assertThat(service.failures(
                asset,
                withEdgeSoftwareVersion(
                        healthyEvidence(false, false),
                        "hardware-runtime-20260831-13"),
                observedAt,
                observedAt.plusSeconds(1)))
                .isEmpty();
        assertThat(service.failures(
                asset,
                withEdgeSoftwareVersion(
                        healthyEvidence(false, false),
                        "hardware-runtime-20260902-15"),
                observedAt,
                observedAt.plusSeconds(1)))
                .containsExactly("UNSUPPORTED_EDGE_SOFTWARE");
        assertThat(service.failures(
                asset,
                withEdgeSoftwareVersion(
                        healthyEvidence(false, false),
                        "hardware-runtime-20260903-16"),
                observedAt,
                observedAt.plusSeconds(1)))
                .containsExactly("UNSUPPORTED_EDGE_SOFTWARE");
        assertThat(service.failures(
                asset,
                withEdgeSoftwareVersion(
                        healthyEvidence(false, false),
                        "hardware-runtime-20260903-17"),
                observedAt,
                observedAt.plusSeconds(1)))
                .containsExactly("UNSUPPORTED_EDGE_SOFTWARE");
        assertThat(service.failures(
                asset,
                withEdgeSoftwareVersion(
                        healthyEvidence(false, false),
                        "hardware-runtime-20260903-18"),
                observedAt,
                observedAt.plusSeconds(1)))
                .containsExactly("UNSUPPORTED_EDGE_SOFTWARE");
        assertThat(service.failures(
                asset,
                withEdgeSoftwareVersion(
                        healthyEvidence(false, false),
                        "hardware-runtime-20260903-19"),
                observedAt,
                observedAt.plusSeconds(1)))
                .isEmpty();
        assertThat(service.failures(
                asset,
                withEdgeSoftwareVersion(
                        healthyEvidence(false, false),
                        "hardware-runtime-20260903-20"),
                observedAt,
                observedAt.plusSeconds(1)))
                .containsExactly("UNSUPPORTED_EDGE_SOFTWARE");
        assertThat(service.failures(
                asset,
                withEdgeSoftwareVersion(
                        healthyEvidence(false, false),
                        "hardware-runtime-20260903-21"),
                observedAt,
                observedAt.plusSeconds(1)))
                .isEmpty();
        assertThat(service.failures(
                asset,
                withEdgeSoftwareVersion(
                        healthyEvidence(false, false),
                        "hardware-runtime-20260904-23"),
                observedAt,
                observedAt.plusSeconds(1)))
                .isEmpty();
        assertThat(service.failures(
                asset,
                withEdgeSoftwareVersion(
                        healthyEvidence(false, false),
                        "hardware-runtime-unknown"),
                observedAt,
                observedAt.plusSeconds(1)))
                .containsExactly("UNSUPPORTED_EDGE_SOFTWARE");
    }

    @Test
    void unsynchronizedClockDoesNotFailOtherwiseHealthyAcceptance() {
        TrustedDeviceAcceptanceEvidenceService service = service();
        var healthy = healthyEvidence(false, false);
        var unsynchronized = new TrustedDeviceAcceptanceEvidenceService.Evidence(
                healthy.challengeUid(),
                healthy.factoryBagRevision(),
                healthy.factoryBagSetSha256(),
                healthy.edgeSoftwareVersion(),
                healthy.edgeProtocolVersion(),
                healthy.edgeStoreInstanceUid(),
                healthy.mcuFirmwareVersion(),
                healthy.persistentStoreHealthy(),
                false,
                healthy.configurationPersistenceHealthy(),
                healthy.mcuCommunicationHealthy(),
                healthy.mcuRemoteUpdateCapable(),
                healthy.sensorsHealthy(),
                healthy.camerasCaptureHealthy(),
                healthy.cameraUploadHealthy(),
                healthy.deviceEntryUrlStored(),
                healthy.deviceEntryUrlSha256(),
                healthy.mcuSimulated(),
                healthy.camerasSimulated(),
                healthy.verifiedPortCount(),
                healthy.verifiedCameraCount(),
                healthy.sensorSampleSha256(),
                healthy.cameraCaptureSha256(),
                healthy.cameraUploadSha256());

        assertThat(service.failures(
                new TrustedDeviceAcceptanceEvidenceService.AssetState(
                        1L, DEVICE_PUBLIC_CODE, 1, 1L, new byte[32],
                        "PENDING", 0L, true, true),
                unsynchronized,
                null,
                LocalDateTime.of(2026, 8, 7, 12, 0)))
                .isEmpty();
    }

    @Test
    void functionalSensorFailureStillBlocksSimulatedHardware() {
        TrustedDeviceAcceptanceEvidenceService service = service();
        LocalDateTime observedAt = LocalDateTime.of(
                2026, 8, 7, 12, 0);
        var evidence = healthyEvidence(true, true);
        evidence = new TrustedDeviceAcceptanceEvidenceService.Evidence(
                evidence.challengeUid(),
                evidence.factoryBagRevision(),
                evidence.factoryBagSetSha256(),
                evidence.edgeSoftwareVersion(),
                evidence.edgeProtocolVersion(),
                evidence.edgeStoreInstanceUid(),
                evidence.mcuFirmwareVersion(),
                evidence.persistentStoreHealthy(),
                evidence.trustedTimeHealthy(),
                evidence.configurationPersistenceHealthy(),
                evidence.mcuCommunicationHealthy(),
                evidence.mcuRemoteUpdateCapable(),
                false,
                evidence.camerasCaptureHealthy(),
                evidence.cameraUploadHealthy(),
                evidence.deviceEntryUrlStored(),
                evidence.deviceEntryUrlSha256(),
                evidence.mcuSimulated(),
                evidence.camerasSimulated(),
                evidence.verifiedPortCount(),
                evidence.verifiedCameraCount(),
                evidence.sensorSampleSha256(),
                evidence.cameraCaptureSha256(),
                evidence.cameraUploadSha256());

        var failures = service.failures(
                new TrustedDeviceAcceptanceEvidenceService.AssetState(
                        1L, DEVICE_PUBLIC_CODE, 1, 1L, new byte[32],
                        "PENDING", 0L, true, true),
                evidence,
                observedAt,
                observedAt.plusSeconds(1));

        assertThat(failures)
                .containsExactly("SENSOR_SELF_TEST_FAILED")
                .doesNotContain("MCU_SIMULATED", "CAMERAS_SIMULATED");
    }

    @Test
    void missingOrDifferentStoredUrlBlocksAcceptance() {
        TrustedDeviceAcceptanceEvidenceService service = service();
        LocalDateTime observedAt = LocalDateTime.of(
                2026, 8, 7, 12, 0);
        var asset = new TrustedDeviceAcceptanceEvidenceService.AssetState(
                1L, DEVICE_PUBLIC_CODE, 1, 1L, new byte[32],
                "PENDING", 0L, true, true);

        assertThat(service.failures(
                asset,
                entryEvidence(false, "0".repeat(64)),
                observedAt,
                observedAt.plusSeconds(1)))
                .containsExactly(
                        "DEVICE_ENTRY_URL_NOT_STORED",
                        "DEVICE_ENTRY_URL_SHA256_MISMATCH");
        assertThat(service.failures(
                asset,
                entryEvidence(true, "4".repeat(64)),
                observedAt,
                observedAt.plusSeconds(1)))
                .containsExactly("DEVICE_ENTRY_URL_SHA256_MISMATCH");
    }

    @Test
    void lateEvidenceCannotCrossFactoryBagGeneration() {
        var current = new TrustedDeviceAcceptanceEvidenceService.AssetState(
                1L, DEVICE_PUBLIC_CODE, 1, 7L, new byte[32],
                "PENDING", 0L, true, true);
        var matching = healthyEvidence(false, false);
        matching = withFactoryBagGeneration(
                matching, 7L, "0".repeat(64));

        assertThat(TrustedDeviceAcceptanceEvidenceService
                .matchesFactoryBagGeneration(current, matching)).isTrue();
        assertThat(TrustedDeviceAcceptanceEvidenceService
                .matchesFactoryBagGeneration(
                        current,
                        withFactoryBagGeneration(
                                matching, 6L, "0".repeat(64))))
                .isFalse();
        assertThat(TrustedDeviceAcceptanceEvidenceService
                .matchesFactoryBagGeneration(
                        current,
                        withFactoryBagGeneration(
                                matching, 7L, "1".repeat(64))))
                .isFalse();
    }

    @Test
    void olderFactoryBagEvidenceConvergesWithoutChangingAcceptance() {
        AcceptanceApplyFixture fixture = acceptanceApplyFixture();

        DeviceAcceptanceEvidenceApplyResult first = fixture.service().apply(
                acceptanceEvent(6L, "1".repeat(64)));
        DeviceAcceptanceEvidenceApplyResult duplicate = fixture.service().apply(
                acceptanceEvent(6L, "1".repeat(64)));

        assertThat(first).isEqualTo(
                new DeviceAcceptanceEvidenceApplyResult(
                        13L, "PASSED", false));
        assertThat(duplicate).isEqualTo(first);
        verifyNoInteractions(fixture.challengePort());
        verify(fixture.acceptanceJdbc(), never()).update(
                anyString(), any(Object[].class));

        ArgumentCaptor<ReliablePlatformDeviceControlTaskRegistration> captor =
                ArgumentCaptor.forClass(
                        ReliablePlatformDeviceControlTaskRegistration.class);
        verify(fixture.registrationPort(), times(1)).register(
                captor.capture());
        ReliablePlatformDeviceControlTaskRegistration registration =
                captor.getValue();
        assertThat(registration.taskKey()).isEqualTo(
                "CONFIRM_EDGE_EVENT:"
                        + "30000000-0000-4000-8000-000000000001");
        JsonNode envelope = JsonMapper.builder().build().readTree(
                registration.executionEnvelope());
        assertThat(envelope.path("payload").path("outcome").asText())
                .isEqualTo("BUSINESS_APPLIED");
        assertThat(envelope.path("payload").path("effectKind").asText())
                .isEqualTo("NO_ACTION_REQUIRED");
    }

    @Test
    void cancelledMatchingChallengeConvergesWithoutChangingAcceptance() {
        AcceptanceApplyFixture fixture = acceptanceApplyFixture();
        when(fixture.challengePort().consume(
                eq(13L),
                eq(UUID.fromString(
                        "40000000-0000-4000-8000-000000000001")),
                eq(UUID.fromString(
                        "50000000-0000-4000-8000-000000000001")),
                eq(7L),
                any(byte[].class),
                any(LocalDateTime.class)))
                .thenReturn(DeviceAcceptanceChallengeConsumeResult.CANCELLED);

        DeviceAcceptanceEvidenceApplyResult first = fixture.service().apply(
                acceptanceEvent(7L, "0".repeat(64), 4, false));
        DeviceAcceptanceEvidenceApplyResult duplicate = fixture.service().apply(
                acceptanceEvent(7L, "0".repeat(64), 4, false));

        assertThat(first).isEqualTo(
                new DeviceAcceptanceEvidenceApplyResult(
                        13L, "PASSED", false));
        assertThat(duplicate).isEqualTo(first);
        verify(fixture.challengePort(), times(2)).consume(
                eq(13L),
                eq(UUID.fromString(
                        "40000000-0000-4000-8000-000000000001")),
                eq(UUID.fromString(
                        "50000000-0000-4000-8000-000000000001")),
                eq(7L),
                any(byte[].class),
                any(LocalDateTime.class));
        verify(fixture.acceptanceJdbc(), never()).update(
                anyString(), any(Object[].class));

        ArgumentCaptor<ReliablePlatformDeviceControlTaskRegistration> captor =
                ArgumentCaptor.forClass(
                        ReliablePlatformDeviceControlTaskRegistration.class);
        verify(fixture.registrationPort(), times(1)).register(
                captor.capture());
        ReliablePlatformDeviceControlTaskRegistration registration =
                captor.getValue();
        assertThat(registration.taskKey()).isEqualTo(
                "CONFIRM_EDGE_EVENT:"
                        + "30000000-0000-4000-8000-000000000001");
        JsonNode envelope = JsonMapper.builder().build().readTree(
                registration.executionEnvelope());
        assertThat(envelope.path("payload").path("outcome").asText())
                .isEqualTo("BUSINESS_APPLIED");
        assertThat(envelope.path("payload").path("effectKind").asText())
                .isEqualTo("NO_ACTION_REQUIRED");
    }

    @Test
    void currentFactoryBagRevisionWithDifferentDigestIsRejected() {
        AcceptanceApplyFixture fixture = acceptanceApplyFixture();

        assertThatThrownBy(() -> fixture.service().apply(
                acceptanceEvent(7L, "1".repeat(64))))
                .isInstanceOf(IllegalArgumentException.class);

        verifyNoInteractions(fixture.challengePort());
        verify(fixture.acceptanceJdbc(), never()).update(
                anyString(), any(Object[].class));
        verifyNoInteractions(fixture.registrationPort());
    }

    @Test
    void futureFactoryBagRevisionIsRejected() {
        AcceptanceApplyFixture fixture = acceptanceApplyFixture();

        assertThatThrownBy(() -> fixture.service().apply(
                acceptanceEvent(8L, "0".repeat(64))))
                .isInstanceOf(IllegalArgumentException.class);

        verifyNoInteractions(fixture.challengePort());
        verify(fixture.acceptanceJdbc(), never()).update(
                anyString(), any(Object[].class));
        verifyNoInteractions(fixture.registrationPort());
    }

    @Test
    void v4StoresExplicitFalseCapabilityWithoutFailingAcceptance() {
        AcceptanceApplyFixture fixture = acceptanceApplyFixture();

        DeviceAcceptanceEvidenceApplyResult result = fixture.service().apply(
                acceptanceEvent(7L, "0".repeat(64), 4, false));

        assertThat(result).isEqualTo(
                new DeviceAcceptanceEvidenceApplyResult(
                        13L, "PASSED", true));
        ArgumentCaptor<Object[]> insertArgs =
                ArgumentCaptor.forClass(Object[].class);
        verify(fixture.acceptanceJdbc()).update(
                contains("INSERT INTO dev_device_acceptance_evidence"),
                insertArgs.capture());
        assertThat(insertArgs.getValue()).hasSize(32);
        assertThat(insertArgs.getValue()[6]).isEqualTo(4);
        assertThat(insertArgs.getValue()[17]).isEqualTo(false);

        ArgumentCaptor<Object[]> assetArgs =
                ArgumentCaptor.forClass(Object[].class);
        verify(fixture.acceptanceJdbc()).update(
                contains("SET mcu_remote_update_capable = ?"),
                assetArgs.capture());
        assertThat(assetArgs.getValue()[0]).isEqualTo(false);
    }

    @Test
    void v3StoresUnknownCapabilityForBackwardCompatibility() {
        AcceptanceApplyFixture fixture = acceptanceApplyFixture();

        fixture.service().apply(
                acceptanceEvent(7L, "0".repeat(64), 3, null));

        ArgumentCaptor<Object[]> insertArgs =
                ArgumentCaptor.forClass(Object[].class);
        verify(fixture.acceptanceJdbc()).update(
                contains("INSERT INTO dev_device_acceptance_evidence"),
                insertArgs.capture());
        assertThat(insertArgs.getValue()[6]).isEqualTo(3);
        assertThat(insertArgs.getValue()[17]).isNull();
        ArgumentCaptor<Object[]> assetArgs =
                ArgumentCaptor.forClass(Object[].class);
        verify(fixture.acceptanceJdbc()).update(
                contains("SET mcu_remote_update_capable = ?"),
                assetArgs.capture());
        assertThat(assetArgs.getValue()[0]).isNull();
    }

    @Test
    void v4RejectsMissingCapabilityBeforeTouchingPersistence() {
        AcceptanceApplyFixture fixture = acceptanceApplyFixture();

        assertThatThrownBy(() -> fixture.service().apply(
                acceptanceEvent(7L, "0".repeat(64), 4, null)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("mcuRemoteUpdateCapable");

        verify(fixture.acceptanceJdbc(), never()).update(
                anyString(), any(Object[].class));
        verifyNoInteractions(fixture.challengePort());
    }

    private static TrustedDeviceAcceptanceEvidenceService service() {
        return service("0.1.0");
    }

    private static TrustedDeviceAcceptanceEvidenceService service(
            String supportedSoftwareVersions) {
        return new TrustedDeviceAcceptanceEvidenceService(
                mock(JdbcTemplate.class),
                JsonMapper.builder().build(),
                DEVICE_ENTRY_URL_FACTORY,
                mock(TrustedDeviceAcceptanceChallengePort.class),
                mock(ReliablePlatformEdgeConfirmationService.class),
                mock(FactorySealAuthorizationService.class),
                supportedSoftwareVersions,
                Duration.ofMinutes(10));
    }

    @SuppressWarnings("unchecked")
    private static AcceptanceApplyFixture acceptanceApplyFixture() {
        JdbcTemplate acceptanceJdbc = mock(JdbcTemplate.class);
        var asset = new TrustedDeviceAcceptanceEvidenceService.AssetState(
                13L,
                DEVICE_PUBLIC_CODE,
                1,
                7L,
                new byte[32],
                "PASSED",
                1L,
                true,
                true);
        when(acceptanceJdbc.query(
                contains("FROM dev_device_asset asset"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenReturn(List.of(asset));
        when(acceptanceJdbc.query(
                contains("FROM dev_device_acceptance_evidence"),
                any(RowMapper.class),
                any(Object[].class)))
                .thenReturn(List.of());
        LocalDateTime receivedAt = LocalDateTime.of(
                2026, 8, 7, 12, 0, 1);
        when(acceptanceJdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class))
                .thenReturn(receivedAt);
        when(acceptanceJdbc.update(
                anyString(), any(Object[].class))).thenReturn(1);

        JdbcTemplate confirmationJdbc = mock(JdbcTemplate.class);
        when(confirmationJdbc.queryForObject(
                contains("FROM ops_reliable_task"),
                eq(Integer.class),
                anyString()))
                .thenReturn(0, 1);
        PlatformDeviceAssetTaskRefFactory taskRefFactory =
                mock(PlatformDeviceAssetTaskRefFactory.class);
        when(taskRefFactory.issue(13L))
                .thenReturn(mock(PlatformDeviceAssetTaskRef.class));
        ReliablePlatformDeviceControlTaskRegistrationPort registrationPort =
                mock(ReliablePlatformDeviceControlTaskRegistrationPort.class);
        ReliablePlatformEdgeConfirmationService confirmationService =
                new ReliablePlatformEdgeConfirmationService(
                        JsonMapper.builder().build(),
                        confirmationJdbc,
                        new DeviceConfigurationCanonicalizer(),
                        taskRefFactory,
                        registrationPort);
        TrustedDeviceAcceptanceChallengePort challengePort =
                mock(TrustedDeviceAcceptanceChallengePort.class);
        when(challengePort.consume(
                anyLong(),
                any(UUID.class),
                any(UUID.class),
                anyLong(),
                any(byte[].class),
                any(LocalDateTime.class)))
                .thenReturn(DeviceAcceptanceChallengeConsumeResult.CONSUMED);
        TrustedDeviceAcceptanceEvidenceService service =
                new TrustedDeviceAcceptanceEvidenceService(
                        acceptanceJdbc,
                        JsonMapper.builder().build(),
                        DEVICE_ENTRY_URL_FACTORY,
                        challengePort,
                        confirmationService,
                        mock(FactorySealAuthorizationService.class),
                        "0.1.0",
                        Duration.ofMinutes(10));
        return new AcceptanceApplyFixture(
                service,
                acceptanceJdbc,
                challengePort,
                registrationPort);
    }

    @SuppressWarnings("unchecked")
    private static TrustedDeviceAcceptanceEvent acceptanceEvent(
            long factoryBagRevision,
            String factoryBagSetSha256) {
        return acceptanceEvent(
                factoryBagRevision, factoryBagSetSha256, 3, null);
    }

    private static TrustedDeviceAcceptanceEvent acceptanceEvent(
            long factoryBagRevision,
            String factoryBagSetSha256,
            int evidenceSchemaVersion,
            Boolean mcuRemoteUpdateCapable) {
        TrustedPlatformInboxRef sourceInbox =
                mock(TrustedPlatformInboxRef.class);
        when(sourceInbox.use(any())).thenAnswer(invocation -> {
            TrustedPlatformInboxRef.PlatformInboxFunction<Object> function =
                    invocation.getArgument(0);
            return function.apply(71L);
        });
        return new TrustedDeviceAcceptanceEvent(
                sourceInbox,
                2,
                acceptancePayload(
                        factoryBagRevision,
                        factoryBagSetSha256,
                        evidenceSchemaVersion,
                        mcuRemoteUpdateCapable));
    }

    private static String acceptancePayload(
            long factoryBagRevision,
            String factoryBagSetSha256,
            int evidenceSchemaVersion,
            Boolean mcuRemoteUpdateCapable) {
        String payload = acceptancePayload(
                factoryBagRevision, factoryBagSetSha256);
        payload = payload.replace(
                "\"evidenceSchemaVersion\": 3",
                "\"evidenceSchemaVersion\": " + evidenceSchemaVersion);
        if (mcuRemoteUpdateCapable != null) {
            payload = payload.replace(
                    "\"mcuCommunicationHealthy\": true,",
                    "\"mcuCommunicationHealthy\": true,\n"
                            + "                      "
                            + "\"mcuRemoteUpdateCapable\": "
                            + mcuRemoteUpdateCapable + ",");
        }
        return payload;
    }

    private static String acceptancePayload(
            long factoryBagRevision,
            String factoryBagSetSha256) {
        return """
                {
                  "trustedSource": {
                    "productId": "test-product",
                    "deviceName": "test-device-1"
                  },
                  "event": {
                    "schemaVersion": 2,
                    "eventUid": "30000000-0000-4000-8000-000000000001",
                    "eventType": "DEVICE_ACCEPTANCE_EVIDENCE",
                    "commandUid": "40000000-0000-4000-8000-000000000001",
                    "occurredAt": "2026-08-07T12:00:00Z",
                    "clockQuality": "SYNCED",
                    "target": {
                      "type": "DEVICE_ASSET",
                      "uid": "test-device-1"
                    },
                    "payloadSha256":
                      "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "payload": {
                      "evidenceSchemaVersion": 3,
                      "challengeUid":
                        "50000000-0000-4000-8000-000000000001",
                      "factoryBagRevision": %d,
                      "factoryBagSetSha256": "%s",
                      "edgeSoftwareVersion": "0.1.0",
                      "edgeProtocolVersion": "2",
                      "edgeStoreInstanceUid":
                        "60000000-0000-4000-8000-000000000001",
                      "mcuFirmwareVersion": "fixed-frame-1.0.0",
                      "persistentStoreHealthy": true,
                      "trustedTimeHealthy": true,
                      "configurationPersistenceHealthy": true,
                      "mcuCommunicationHealthy": true,
                      "sensorsHealthy": true,
                      "camerasCaptureHealthy": true,
                      "cameraUploadHealthy": true,
                      "deviceEntryUrlStored": true,
                      "deviceEntryUrlSha256": "%s",
                      "mcuSimulated": false,
                      "camerasSimulated": false,
                      "verifiedPortCount": 1,
                      "verifiedCameraCount": 2,
                      "sensorSampleSha256":
                        "1111111111111111111111111111111111111111111111111111111111111111",
                      "cameraCaptureSha256":
                        "2222222222222222222222222222222222222222222222222222222222222222",
                      "cameraUploadSha256":
                        "3333333333333333333333333333333333333333333333333333333333333333"
                    }
                  }
                }
                """.formatted(
                factoryBagRevision,
                factoryBagSetSha256,
                DEVICE_ENTRY_URL_FACTORY.create(
                        DEVICE_PUBLIC_CODE).sha256Hex());
    }

    private record AcceptanceApplyFixture(
            TrustedDeviceAcceptanceEvidenceService service,
            JdbcTemplate acceptanceJdbc,
            TrustedDeviceAcceptanceChallengePort challengePort,
            ReliablePlatformDeviceControlTaskRegistrationPort
                    registrationPort) {
    }

    private static TrustedDeviceAcceptanceEvidenceService.Evidence
            healthyEvidence(
                    boolean mcuSimulated,
                    boolean camerasSimulated) {
        return new TrustedDeviceAcceptanceEvidenceService.Evidence(
                "10000000-0000-4000-8000-000000000001",
                1L,
                "0".repeat(64),
                "0.1.0",
                "2",
                "20000000-0000-4000-8000-000000000001",
                "fixed-frame-1.0.0",
                true,
                true,
                true,
                true,
                true,
                true,
                true,
                true,
                true,
                DEVICE_ENTRY_URL_FACTORY.create(
                        DEVICE_PUBLIC_CODE).sha256Hex(),
                mcuSimulated,
                camerasSimulated,
                1,
                2,
                "1".repeat(64),
                "2".repeat(64),
                "3".repeat(64));
    }

    private static TrustedDeviceAcceptanceEvidenceService.Evidence
            entryEvidence(boolean stored, String sha256) {
        var evidence = healthyEvidence(false, false);
        return new TrustedDeviceAcceptanceEvidenceService.Evidence(
                evidence.challengeUid(),
                evidence.factoryBagRevision(),
                evidence.factoryBagSetSha256(),
                evidence.edgeSoftwareVersion(),
                evidence.edgeProtocolVersion(),
                evidence.edgeStoreInstanceUid(),
                evidence.mcuFirmwareVersion(),
                evidence.persistentStoreHealthy(),
                evidence.trustedTimeHealthy(),
                evidence.configurationPersistenceHealthy(),
                evidence.mcuCommunicationHealthy(),
                evidence.mcuRemoteUpdateCapable(),
                evidence.sensorsHealthy(),
                evidence.camerasCaptureHealthy(),
                evidence.cameraUploadHealthy(),
                stored,
                sha256,
                evidence.mcuSimulated(),
                evidence.camerasSimulated(),
                evidence.verifiedPortCount(),
                evidence.verifiedCameraCount(),
                evidence.sensorSampleSha256(),
                evidence.cameraCaptureSha256(),
                evidence.cameraUploadSha256());
    }

    private static TrustedDeviceAcceptanceEvidenceService.Evidence
            withEdgeSoftwareVersion(
                    TrustedDeviceAcceptanceEvidenceService.Evidence evidence,
                    String edgeSoftwareVersion) {
        return new TrustedDeviceAcceptanceEvidenceService.Evidence(
                evidence.challengeUid(),
                evidence.factoryBagRevision(),
                evidence.factoryBagSetSha256(),
                edgeSoftwareVersion,
                evidence.edgeProtocolVersion(),
                evidence.edgeStoreInstanceUid(),
                evidence.mcuFirmwareVersion(),
                evidence.persistentStoreHealthy(),
                evidence.trustedTimeHealthy(),
                evidence.configurationPersistenceHealthy(),
                evidence.mcuCommunicationHealthy(),
                evidence.mcuRemoteUpdateCapable(),
                evidence.sensorsHealthy(),
                evidence.camerasCaptureHealthy(),
                evidence.cameraUploadHealthy(),
                evidence.deviceEntryUrlStored(),
                evidence.deviceEntryUrlSha256(),
                evidence.mcuSimulated(),
                evidence.camerasSimulated(),
                evidence.verifiedPortCount(),
                evidence.verifiedCameraCount(),
                evidence.sensorSampleSha256(),
                evidence.cameraCaptureSha256(),
                evidence.cameraUploadSha256());
    }

    private static TrustedDeviceAcceptanceEvidenceService.Evidence
            withFactoryBagGeneration(
                    TrustedDeviceAcceptanceEvidenceService.Evidence evidence,
                    long revision,
                    String digest) {
        return new TrustedDeviceAcceptanceEvidenceService.Evidence(
                evidence.challengeUid(),
                revision,
                digest,
                evidence.edgeSoftwareVersion(),
                evidence.edgeProtocolVersion(),
                evidence.edgeStoreInstanceUid(),
                evidence.mcuFirmwareVersion(),
                evidence.persistentStoreHealthy(),
                evidence.trustedTimeHealthy(),
                evidence.configurationPersistenceHealthy(),
                evidence.mcuCommunicationHealthy(),
                evidence.mcuRemoteUpdateCapable(),
                evidence.sensorsHealthy(),
                evidence.camerasCaptureHealthy(),
                evidence.cameraUploadHealthy(),
                evidence.deviceEntryUrlStored(),
                evidence.deviceEntryUrlSha256(),
                evidence.mcuSimulated(),
                evidence.camerasSimulated(),
                evidence.verifiedPortCount(),
                evidence.verifiedCameraCount(),
                evidence.sensorSampleSha256(),
                evidence.cameraCaptureSha256(),
                evidence.cameraUploadSha256());
    }
}
