package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationDeviceRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationPortRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationReleaseRequest;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;

/**
 * 根据资产型号和投口数量生成完整的第一版设备配置。
 */
@Component
final class InitialDeviceConfigurationFactory {

    private final InitialDeviceConfigurationProperties properties;

    InitialDeviceConfigurationFactory(
            InitialDeviceConfigurationProperties properties) {
        this.properties = properties;
    }

    ConfigurationReleaseRequest create(
            String hardwareSn,
            String modelCode,
            int portCount) {
        String profile = properties.profileFor(modelCode);
        if (!InitialDeviceConfigurationProperties
                .FIXED_FRAME_DIGITAL_INFRARED.equals(profile)) {
            throw new IllegalStateException(
                    "unsupported initial device configuration profile: "
                            + profile);
        }
        return fixedFrameDigitalInfrared(
                hardwareSn, modelCode, portCount);
    }

    private ConfigurationReleaseRequest fixedFrameDigitalInfrared(
            String hardwareSn,
            String modelCode,
            int portCount) {
        ConfigurationDeviceRequest device =
                new ConfigurationDeviceRequest(
                        "回收箱 " + hardwareSn,
                        null,
                        null,
                        null,
                        30_000L,
                        3L,
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
        List<ConfigurationPortRequest> ports =
                new ArrayList<>(portCount);
        for (int portNo = 1; portNo <= portCount; portNo++) {
            ports.add(new ConfigurationPortRequest(
                    portNo,
                    "投口" + portNo,
                    true,
                    properties.getUnitPriceYuanPerKg(),
                    "INFRARED_OR_WEIGHT",
                    properties.getFullnessWeightKg(),
                    3_000L,
                    5_000L,
                    10_000L,
                    60_000L,
                    "DIGITAL_INFRARED",
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
                    0L,
                    3_000L,
                    60_000L));
        }
        return new ConfigurationReleaseRequest(
                0L,
                "按设备型号 " + modelCode + " 自动发布初始配置",
                false,
                device,
                ports);
    }
}
