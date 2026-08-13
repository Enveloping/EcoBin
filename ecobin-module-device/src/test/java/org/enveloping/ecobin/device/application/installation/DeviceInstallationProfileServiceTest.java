package org.enveloping.ecobin.device.application.installation;

import org.enveloping.ecobin.device.web.v1.DeviceModels.UpdateDeviceInstallationProfileRequest;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class DeviceInstallationProfileServiceTest {

    @Test
    void normalizesRequiredTextAndExactSevenDigitGcj02Coordinates() {
        var result = DeviceInstallationProfileService.normalize(
                new UpdateDeviceInstallationProfileRequest(
                        0L,
                        " 东门回收箱 ",
                        " 园区东门 1 号岗亭 ",
                        "113.1234567",
                        "23.1000000"));

        assertThat(result.displayName()).isEqualTo("东门回收箱");
        assertThat(result.address()).isEqualTo("园区东门 1 号岗亭");
        assertThat(result.longitude().toPlainString())
                .isEqualTo("113.1234567");
        assertThat(result.latitude().toPlainString())
                .isEqualTo("23.1000000");
    }

    @Test
    void rejectsMissingFieldsAndCoordinatesOutsideTheWorld() {
        assertThatThrownBy(() -> DeviceInstallationProfileService.normalize(
                new UpdateDeviceInstallationProfileRequest(
                        0L, "设备", " ", "113", "23")))
                .isInstanceOf(TargetApiException.class);
        assertThatThrownBy(() -> DeviceInstallationProfileService.normalize(
                new UpdateDeviceInstallationProfileRequest(
                        0L, "设备", "地址", "180.0000001", "23")))
                .isInstanceOf(TargetApiException.class);
        assertThatThrownBy(() -> DeviceInstallationProfileService.normalize(
                new UpdateDeviceInstallationProfileRequest(
                        0L, "设备", "地址", "113", "23.12345678")))
                .isInstanceOf(TargetApiException.class);
        assertThatThrownBy(() -> DeviceInstallationProfileService.normalize(
                new UpdateDeviceInstallationProfileRequest(
                        0L, "设备", "地址", "1e2", "23")))
                .isInstanceOf(TargetApiException.class);
        assertThatThrownBy(() -> DeviceInstallationProfileService.normalize(
                new UpdateDeviceInstallationProfileRequest(
                        0L, "设备", "地址", "+113", "23")))
                .isInstanceOf(TargetApiException.class);
    }
}
