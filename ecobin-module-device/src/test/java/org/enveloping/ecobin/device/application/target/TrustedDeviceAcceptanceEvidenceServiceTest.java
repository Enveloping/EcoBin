package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.port.TrustedDeviceAcceptanceChallengePort;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import tools.jackson.databind.json.JsonMapper;

import java.time.Duration;
import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

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
                        "PENDING", true, true),
                healthyEvidence(true, true),
                observedAt,
                observedAt.plusSeconds(1));

        assertThat(failures).isEmpty();
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
                        "PENDING", true, true),
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
                "PENDING", true, true);

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
                "PENDING", true, true);
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

    private static TrustedDeviceAcceptanceEvidenceService service() {
        return new TrustedDeviceAcceptanceEvidenceService(
                mock(JdbcTemplate.class),
                JsonMapper.builder().build(),
                DEVICE_ENTRY_URL_FACTORY,
                mock(TrustedDeviceAcceptanceChallengePort.class),
                mock(ReliablePlatformEdgeConfirmationService.class),
                "0.1.0",
                Duration.ofMinutes(10));
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
