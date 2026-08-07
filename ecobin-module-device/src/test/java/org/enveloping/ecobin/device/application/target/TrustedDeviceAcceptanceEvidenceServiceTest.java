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

    @Test
    void simulationProvenanceDoesNotFailFunctionalAcceptance() {
        TrustedDeviceAcceptanceEvidenceService service = service();
        LocalDateTime observedAt = LocalDateTime.of(
                2026, 8, 7, 12, 0);

        var failures = service.failures(
                new TrustedDeviceAcceptanceEvidenceService.AssetState(
                        1L, 1, "PENDING", true),
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
                evidence.mcuSimulated(),
                evidence.camerasSimulated(),
                evidence.verifiedPortCount(),
                evidence.verifiedCameraCount(),
                evidence.sensorSampleSha256(),
                evidence.cameraCaptureSha256(),
                evidence.cameraUploadSha256());

        var failures = service.failures(
                new TrustedDeviceAcceptanceEvidenceService.AssetState(
                        1L, 1, "PENDING", true),
                evidence,
                observedAt,
                observedAt.plusSeconds(1));

        assertThat(failures)
                .containsExactly("SENSOR_SELF_TEST_FAILED")
                .doesNotContain("MCU_SIMULATED", "CAMERAS_SIMULATED");
    }

    private static TrustedDeviceAcceptanceEvidenceService service() {
        return new TrustedDeviceAcceptanceEvidenceService(
                mock(JdbcTemplate.class),
                JsonMapper.builder().build(),
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
                mcuSimulated,
                camerasSimulated,
                1,
                2,
                "1".repeat(64),
                "2".repeat(64),
                "3".repeat(64));
    }
}
