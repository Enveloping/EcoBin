package org.enveloping.ecobin.device.application.target;

import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class InitialDeviceConfigurationFactoryTest {

    @Test
    void createsCompleteDigitalInfraredConfigurationForEveryModelPort() {
        InitialDeviceConfigurationProperties properties =
                new InitialDeviceConfigurationProperties();
        properties.setModelProfiles(Map.of(
                "EC-M0",
                InitialDeviceConfigurationProperties
                        .FIXED_FRAME_DIGITAL_INFRARED));
        InitialDeviceConfigurationFactory factory =
                new InitialDeviceConfigurationFactory(properties);

        var request = factory.create("ec-m0", 2);

        assertThat(request.expectedLatestVersion()).isZero();
        assertThat(request.device().continueDeliveryWaitMs())
                .isEqualTo(30_000L);
        assertThat(request.device().deliveryDoorTravelWaitMs())
                .isEqualTo(3_000L);
        assertThat(request.device().negativeWeightThresholdGram())
                .isEqualTo(500L);
        assertThat(request.ports()).hasSize(2);
        assertThat(request.ports())
                .allSatisfy(port -> {
                    assertThat(port.enabled()).isTrue();
                    assertThat(port.fullnessMode())
                            .isEqualTo("INFRARED_OR_WEIGHT");
                    assertThat(port.fullnessSensorKind())
                            .isEqualTo("DIGITAL_INFRARED");
                    assertThat(port.fullnessSampleCount()).isEqualTo(5);
                    assertThat(port.fullnessMinimumValidSampleCount())
                            .isEqualTo(3);
                });
    }

    @Test
    void usesTheConfiguredDefaultProfileForANewModelCode() {
        InitialDeviceConfigurationProperties properties =
                new InitialDeviceConfigurationProperties();
        InitialDeviceConfigurationFactory factory =
                new InitialDeviceConfigurationFactory(properties);

        var request = factory.create("NEW-MODEL", 1);

        assertThat(request.ports().getFirst().fullnessSensorKind())
                .isEqualTo("DIGITAL_INFRARED");
    }
}
