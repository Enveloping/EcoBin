package org.enveloping.ecobin.device.application.target;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

/**
 * 设备部署时自动发布的初始配置选择规则。
 *
 * <p>型号只负责选择硬件配置档案；机构后续仍可通过配置发布接口创建更高版本。</p>
 */
@Component
@ConfigurationProperties(
        prefix = "ecobin.device.initial-configuration")
public final class InitialDeviceConfigurationProperties {

    static final String FIXED_FRAME_DIGITAL_INFRARED =
            "fixed-frame-digital-infrared";

    private String defaultProfile = FIXED_FRAME_DIGITAL_INFRARED;
    private Map<String, String> modelProfiles = new LinkedHashMap<>();
    private String unitPriceYuanPerKg = "0.4500";
    private String fullnessWeightKg = "50.000";

    public String getDefaultProfile() {
        return defaultProfile;
    }

    public void setDefaultProfile(String defaultProfile) {
        this.defaultProfile = defaultProfile;
    }

    public Map<String, String> getModelProfiles() {
        return modelProfiles;
    }

    public void setModelProfiles(Map<String, String> modelProfiles) {
        this.modelProfiles = modelProfiles == null
                ? new LinkedHashMap<>()
                : new LinkedHashMap<>(modelProfiles);
    }

    public String getUnitPriceYuanPerKg() {
        return unitPriceYuanPerKg;
    }

    public void setUnitPriceYuanPerKg(String unitPriceYuanPerKg) {
        this.unitPriceYuanPerKg = unitPriceYuanPerKg;
    }

    public String getFullnessWeightKg() {
        return fullnessWeightKg;
    }

    public void setFullnessWeightKg(String fullnessWeightKg) {
        this.fullnessWeightKg = fullnessWeightKg;
    }

    String profileFor(String modelCode) {
        String normalizedModel = modelCode.trim().toUpperCase(Locale.ROOT);
        String profile = modelProfiles.get(normalizedModel);
        if (profile == null) {
            profile = defaultProfile;
        }
        if (profile == null || profile.isBlank()) {
            throw new IllegalStateException(
                    "initial device configuration profile is missing for model "
                            + normalizedModel);
        }
        return profile.trim().toLowerCase(Locale.ROOT);
    }
}
