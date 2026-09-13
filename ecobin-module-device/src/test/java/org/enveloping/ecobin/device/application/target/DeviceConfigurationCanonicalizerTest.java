package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationDeviceRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationPortRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationReleaseRequest;
import org.junit.jupiter.api.Test;

import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;

class DeviceConfigurationCanonicalizerTest {

    private final DeviceConfigurationCanonicalizer canonicalizer =
            new DeviceConfigurationCanonicalizer();

    @Test
    void matchesFrozenNativeMcuDigestVector() throws java.io.IOException {
        var request = new ConfigurationReleaseRequest(0L, null,
                new ConfigurationDeviceRequest(5_000L, 3L, 3L, 30_000L, 500L,
                        120_000L, 6_000L, 30_000L, 1_000L, true),
                List.of(port(1, "0.4501"), port(2, "0.4502")));
        var normalized = canonicalizer.normalize(request, 2, 3_600_000L, 3L,
                McuConfigurationProfile.UART_V2_SIMPLIFIED);
        assertThat(HexFormat.of().formatHex(canonicalizer.mcuPayloadSha256(8,
                HexFormat.of().parseHex("a1".repeat(32)), normalized.device(), normalized.ports(), normalized.profile())))
                .isEqualTo("ec5b3394e4a044a5f0aa7f328ba79135dc7d816369a046d71de81dec3adacba1");
        var payload = canonicalizer.commandPayload("10000000-0000-4000-8000-000000000002", 8,
                normalized, canonicalizer.mcuPayloadSha256(8, normalized));
        var mapper = new tools.jackson.databind.ObjectMapper();
        try (var fixture = getClass().getResourceAsStream("/native-configuration-java.json")) {
            var expected = mapper.readTree(fixture);
            assertThat(expected.path("canonicalContent").asString())
                    .isEqualTo(new String(normalized.canonicalBytes(), StandardCharsets.UTF_8));
            assertThat(mapper.readTree(canonicalizer.canonicalBytes(payload)))
                    .isEqualTo(expected.path("payload"));
        }
    }

    @Test
    void explicitNativeProfileFreezesEngineeringPolicyWithoutExpandingCloudPortShape() {
        var request = new InitialDeviceConfigurationFactory(new InitialDeviceConfigurationProperties())
                .create("EC-M0", 2);
        var legacy = canonicalizer.normalize(request, 2, 3_600_000L, 3L);
        var nativeConfig = canonicalizer.normalize(request, 2, 3_600_000L, 3L,
                McuConfigurationProfile.UART_V2_SIMPLIFIED);

        assertThat(legacy.device().weightMeasurementTimeoutMs()).isEqualTo(6_000);
        assertThat(legacy.ports().getFirst().view().weightMaximumFluctuationGram()).isEqualTo(20);
        assertThat(legacy.ports().getFirst().view().weightRequiredSampleCount()).isEqualTo(10);
        assertThat(new String(legacy.canonicalBytes(), StandardCharsets.UTF_8))
                .doesNotContain("mcuConfigurationProfile", "weightPollIntervalMs", "weightMaximumSampleAgeMs");
        assertThat(nativeConfig.contentSha256Hex()).isNotEqualTo(legacy.contentSha256Hex());
        assertThat(new String(nativeConfig.canonicalBytes(), StandardCharsets.UTF_8))
                .contains("\"mcuConfigurationProfile\":\"UART_V2_SIMPLIFIED\"",
                        "\"weightPollIntervalMs\":250", "\"weightResponseTimeoutMs\":200",
                        "\"weightMaximumSampleAgeMs\":750", "\"weightMinimumMedianSampleCount\":5");

        var payload = canonicalizer.commandPayload("application", 2, nativeConfig,
                canonicalizer.mcuPayloadSha256(2, nativeConfig));
        assertThat(payload).containsEntry("mcuConfigurationProfile", "UART_V2_SIMPLIFIED");
        @SuppressWarnings("unchecked")
        var device = (Map<String, Object>) payload.get("deviceConfig");
        @SuppressWarnings("unchecked")
        var ports = (List<Map<String, Object>>) payload.get("ports");
        assertThat(device).containsEntry("weightMeasurementTimeoutMs", 5_000L)
                .doesNotContainKeys("weightPollIntervalMs", "weightResponseTimeoutMs");
        assertThat(ports).allSatisfy(port -> assertThat(port)
                .containsEntry("weightMeasurementTimeoutMs", 5_000L)
                .containsEntry("weightStableWindowMs", 1_500L)
                .containsEntry("weightMaximumFluctuationGrams", 100L)
                .containsEntry("weightRequiredSampleCount", 5)
                .doesNotContainKeys("weightMaximumSampleAgeMs", "weightMinimumMedianSampleCount"));
        assertThat(ports.getFirst()).hasSize(19);
        assertThat((Map<?, ?>) payload.get("config")).hasSize(3);
        assertThat(canonicalizer.commandPayload("application", 1, legacy,
                canonicalizer.mcuPayloadSha256(1, legacy))).doesNotContainKey("mcuConfigurationProfile");
    }

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
