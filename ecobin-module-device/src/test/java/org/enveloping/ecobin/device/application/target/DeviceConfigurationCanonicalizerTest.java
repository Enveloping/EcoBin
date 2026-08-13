package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationDeviceRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationPortRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationReleaseRequest;
import org.junit.jupiter.api.Test;

import java.util.HexFormat;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class DeviceConfigurationCanonicalizerTest {

    private final DeviceConfigurationCanonicalizer canonicalizer =
            new DeviceConfigurationCanonicalizer();

    @Test
    void matchesFrozenTwoPortMcuDigestVector() {
        ConfigurationDeviceRequest device =
                new ConfigurationDeviceRequest(
                        5_000L,
                        3L,
                        3L,
                        30_000L,
                        500L,
                        120_000L,
                        6_000L,
                        30_000L,
                        1_000L,
                        true);
        ConfigurationReleaseRequest request =
                new ConfigurationReleaseRequest(
                        0L,
                        null,
                        device,
                        List.of(port(1, "0.4501"), port(2, "0.4502")));
        var normalized = canonicalizer.normalize(
                request, 2, 3_600_000L, 3L);
        byte[] frozenContentSha = HexFormat.of().parseHex("a1".repeat(32));

        assertThat(normalized.device().edgeHeartbeatIntervalMs())
                .isEqualTo(3_600_000L);
        assertThat(normalized.device().edgeHeartbeatMissThreshold())
                .isEqualTo(3L);
        assertThat(new String(
                normalized.canonicalBytes(),
                java.nio.charset.StandardCharsets.UTF_8))
                .contains("\"schemaVersion\":2")
                .doesNotContain("address", "longitude", "latitude");

        byte[] actual = canonicalizer.mcuPayloadSha256(
                8,
                frozenContentSha,
                normalized.device(),
                normalized.ports());

        assertThat(HexFormat.of().formatHex(actual)).isEqualTo(
                "531a7edd423b9c629e0e88a76a503e2e95e29f3c613e375d8e42f3709accc14b");
    }

    private static ConfigurationPortRequest port(
            int portNo,
            String unitPrice) {
        return new ConfigurationPortRequest(
                portNo,
                "投口" + portNo,
                true,
                unitPrice,
                "INFRARED_OR_WEIGHT",
                "50.000",
                3_000L,
                5_000L,
                10_000L,
                60_000L,
                "ULTRASONIC",
                600L,
                5,
                3,
                30_000L,
                1_500L,
                20L,
                10,
                6_000L,
                -5_000L,
                100_000L,
                4L,
                3_000L,
                60_000L);
    }
}
