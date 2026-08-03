package org.enveloping.ecobin.device.application.deliveryquery;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class MiniappDeliveryDeviceQueryPolicyTest {

    private static final LocalDateTime NOW =
            LocalDateTime.of(2026, 7, 29, 2, 0, 0);
    private static final byte[] CONTENT_SHA =
            java.util.HexFormat.of().parseHex("aa".repeat(32));
    private static final byte[] MCU_PAYLOAD_SHA =
            java.util.HexFormat.of().parseHex("bb".repeat(32));

    @Test
    void exactTrustedSnapshotLeavesHealthyPortUnblocked() {
        var evaluation = MiniappDeliveryDeviceQueryPolicy.evaluate(
                deployment(
                        CONTENT_SHA,
                        CONTENT_SHA,
                        NOW.minusSeconds(2),
                        false),
                List.of(healthyPort()),
                NOW);

        assertThat(evaluation.exactConfiguration()).isTrue();
        assertThat(evaluation.commonBlockers()).isEmpty();
        assertThat(evaluation.ports()).singleElement()
                .satisfies(port -> {
                    assertThat(port.displayName())
                            .isEqualTo("塑料投口");
                    assertThat(port.unitPriceYuanPerKg())
                            .isEqualTo("0.4500");
                    assertThat(port.fullnessMode())
                            .isEqualTo("INFRARED_OR_WEIGHT");
                    assertThat(port.blockers()).isEmpty();
                });
    }

    @Test
    void fixedFrameLastObservationLeavesHealthyPortUnblocked() {
        var fixedFramePort = portWithWeightKind(
                "LAST_OBSERVED",
                0L,
                0L,
                0L);

        var evaluation = MiniappDeliveryDeviceQueryPolicy.evaluate(
                deployment(
                        CONTENT_SHA,
                        CONTENT_SHA,
                        NOW.minusSeconds(2),
                        false),
                List.of(fixedFramePort),
                NOW);

        assertThat(evaluation.ports()).singleElement()
                .satisfies(port -> assertThat(port.blockers()).isEmpty());
    }

    @Test
    void oldTrustedSnapshotRemainsUsableWhileOccupancyStillBlocks() {
        var evaluation = MiniappDeliveryDeviceQueryPolicy.evaluate(
                deployment(
                        CONTENT_SHA,
                        CONTENT_SHA,
                        NOW.minusMinutes(1),
                        true),
                List.of(healthyPort()),
                NOW);

        assertThat(evaluation.commonBlockers())
                .containsExactly(
                        MiniappDeliveryDeviceQueryPolicy.DEVICE_BUSY);
        assertThat(evaluation.ports().getFirst().blockers())
                .containsExactly(
                        MiniappDeliveryDeviceQueryPolicy.DEVICE_BUSY);
    }

    @Test
    void configurationReceiptMismatchDoesNotBlockBackendAdmission() {
        var evaluation = MiniappDeliveryDeviceQueryPolicy.evaluate(
                deployment(
                        CONTENT_SHA,
                        java.util.HexFormat.of().parseHex(
                                "cc".repeat(32)),
                        NOW.minusSeconds(2),
                        false),
                List.of(healthyPort()),
                NOW);

        assertThat(evaluation.exactConfiguration()).isTrue();
        assertThat(evaluation.commonBlockers()).isEmpty();
        assertThat(evaluation.ports()).singleElement()
                .satisfies(port -> {
                    assertThat(port.displayName()).isEqualTo("塑料投口");
                    assertThat(port.unitPriceYuanPerKg())
                            .isEqualTo("0.4500");
                    assertThat(port.fullnessMode())
                            .isEqualTo("INFRARED_OR_WEIGHT");
                    assertThat(port.blockers()).isEmpty();
                });
    }

    @Test
    void runtimeSensorAndSafetyProjectionAreEdgeOwned() {
        var port = new MiniappDeliveryDeviceQueryRepository
                .PortSnapshotRow(
                301L,
                2,
                "塑料投口",
                true,
                new BigDecimal("0.4500"),
                "INFRARED_OR_WEIGHT",
                4L,
                "ACTUATOR_FAULT",
                "ENERGIZED",
                "DRIVER_FAULT",
                "OK",
                "UNSTABLE",
                false,
                null,
                "NONE",
                4L,
                "ALARM",
                "SENSOR_FAULT",
                1L,
                "SAFETY_BLOCKED",
                901L,
                9001L,
                "DEVICE_RUNTIME_SNAPSHOT",
                1053L);

        var evaluation = MiniappDeliveryDeviceQueryPolicy.evaluate(
                deployment(
                        CONTENT_SHA,
                        CONTENT_SHA,
                        NOW.minusSeconds(2),
                        false),
                List.of(port),
                NOW);

        assertThat(evaluation.ports().getFirst().blockers()).isEmpty();
    }

    private static MiniappDeliveryDeviceQueryRepository
            .DeploymentSnapshotRow deployment(
                    byte[] applicationContentSha,
                    byte[] orangePiContentSha,
                    LocalDateTime receivedAt,
                    boolean busy) {
        return new MiniappDeliveryDeviceQueryRepository
                .DeploymentSnapshotRow(
                101L,
                201L,
                "Dp_demo_01",
                "IN_USE",
                "ENABLED",
                true,
                busy,
                401L,
                8L,
                "校园回收机",
                "教学楼一层",
                5_000L,
                3L,
                CONTENT_SHA,
                MCU_PAYLOAD_SHA,
                "APPLIED",
                8L,
                applicationContentSha,
                MCU_PAYLOAD_SHA,
                NOW.minusMinutes(1),
                "ONLINE",
                "SAFE",
                "OK",
                "HEALTHY",
                9001L,
                "DEVICE_RUNTIME_SNAPSHOT",
                1053L,
                receivedAt,
                8L,
                orangePiContentSha,
                MCU_PAYLOAD_SHA);
    }

    private static MiniappDeliveryDeviceQueryRepository.PortSnapshotRow
            healthyPort() {
        return portWithWeightKind(
                "STABLE_WINDOW_MEAN",
                13_250L,
                4L,
                4L);
    }

    private static MiniappDeliveryDeviceQueryRepository.PortSnapshotRow
            portWithWeightKind(
                    String valueKind,
                    long weightGrams,
                    long runtimeCalibrationVersion,
                    long configuredCalibrationVersion) {
        return new MiniappDeliveryDeviceQueryRepository
                .PortSnapshotRow(
                301L,
                2,
                "塑料投口",
                true,
                new BigDecimal("0.4500"),
                "INFRARED_OR_WEIGHT",
                configuredCalibrationVersion,
                "UNKNOWN",
                "DEENERGIZED",
                "OK",
                "OK",
                "STABLE",
                true,
                weightGrams,
                valueKind,
                runtimeCalibrationVersion,
                "NORMAL",
                "OK",
                0L,
                "SAFE",
                null,
                9001L,
                "DEVICE_RUNTIME_SNAPSHOT",
                1053L);
    }
}
