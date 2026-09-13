package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationDeviceRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationPortRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationReleaseRequest;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.springframework.jdbc.core.JdbcTemplate;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Map;
import java.util.Set;

/** Resolves one complete tenant rule before configuration hashing and publication. */
final class DevicePolicyProvider {
    private final JdbcTemplate jdbc;

    DevicePolicyProvider(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    Policy current(Long tenantId) {
        return jdbc.queryForObject("""
                SELECT defaults.policy_version default_version, tenant.policy_version tenant_version,
                       COALESCE(tenant.configuration_mode, 'INHERIT') configuration_mode,
                       CASE WHEN tenant.configuration_mode = 'CUSTOM' THEN tenant.unit_price_yuan_per_kg ELSE defaults.unit_price_yuan_per_kg END price,
                       CASE WHEN tenant.configuration_mode = 'CUSTOM' THEN tenant.fullness_mode ELSE defaults.fullness_mode END mode,
                       CASE WHEN tenant.configuration_mode = 'CUSTOM' THEN tenant.fullness_weight_kg ELSE defaults.fullness_weight_kg END weight,
                       CASE WHEN tenant.configuration_mode = 'CUSTOM' THEN tenant.negative_weight_threshold_g ELSE defaults.negative_weight_threshold_g END negative_threshold
                FROM dev_device_default_policy defaults
                LEFT JOIN dev_tenant_device_policy tenant ON tenant.tenant_id = ?
                WHERE defaults.singleton_id = 1
                """, (rs, ignored) -> {
            boolean custom = "CUSTOM".equals(rs.getString("configuration_mode"));
            return new Policy(custom ? null : rs.getLong("default_version"),
                    custom ? rs.getLong("tenant_version") : null,
                    rs.getBigDecimal("price").setScale(4).toPlainString(), rs.getString("mode"),
                    rs.getBigDecimal("weight").setScale(3).toPlainString(), rs.getLong("negative_threshold"));
        }, tenantId);
    }

    static String normalizeWeight(String mode, String weight) {
        if (!Set.of("INFRARED_ONLY", "WEIGHT_ONLY", "INFRARED_OR_WEIGHT").contains(mode == null ? "" : mode)) {
            throw invalid("请选择仅重量、仅红外或重量/红外任一满足");
        }
        return decimal(weight, 7, 3, "4294967.295", "满溢净重必须为 0.001～4294967.295 千克，最多三位小数");
    }

    static String normalizePrice(String price) {
        return decimal(price, 6, 4, "429496.7295", "单价必须为 0.0001～429496.7295 元/千克，最多四位小数");
    }

    static long normalizeNegativeThreshold(Long threshold) {
        if (threshold == null || threshold < 1 || threshold > 4294967295L) {
            throw invalid("重量减少异常阈值必须为 1～4294967295 克的整数");
        }
        return threshold;
    }

    private static String decimal(String text, int integerDigits, int scale, String maximum, String message) {
        try {
            if (text == null || !text.matches("[0-9]{1," + integerDigits + "}(\\.[0-9]{1," + scale + "})?")) throw new IllegalArgumentException();
            BigDecimal value = new BigDecimal(text).setScale(scale, RoundingMode.UNNECESSARY);
            if (value.signum() <= 0 || value.compareTo(new BigDecimal(maximum)) > 0) throw new IllegalArgumentException();
            return value.toPlainString();
        } catch (IllegalArgumentException | ArithmeticException error) { throw invalid(message); }
    }

    private static TargetApiException invalid(String message) {
        return new TargetApiException(400, "COMMON.INVALID_REQUEST", message, false, Map.of());
    }

    record Policy(Long defaultVersion, Long tenantVersion, String price, String mode, String weightKg, long negativeThreshold) {
        ConfigurationReleaseRequest apply(ConfigurationReleaseRequest source) {
            if (source == null || source.device() == null || source.ports() == null) throw invalid("设备配置缺少设备或投口参数");
            var device = source.device();
            return new ConfigurationReleaseRequest(source.expectedLatestVersion(), source.reason(),
                    new ConfigurationDeviceRequest(device.mcuHeartbeatIntervalMs(), device.mcuHeartbeatMissThreshold(),
                            device.doorCloseRetryLimit(), device.continueDeliveryWaitMs(), negativeThreshold,
                            device.deliveryAutoCloseMs(), device.weightMeasurementTimeoutMs(), device.deliveryDoorTravelWaitMs(),
                            device.cleanSolenoidPulseMs(), device.smokeMonitoringEnabled()),
                    source.ports().stream().map(port -> new ConfigurationPortRequest(
                            port.portNo(), port.displayName(), port.enabled(), price, mode, weightKg,
                            port.deliverySettleDelayMs(), port.fullnessInitialDelayMs(), port.fullnessRecheckDelayMs(),
                            port.doorAutoCloseTimeoutMs(), port.fullnessSensorKind(), port.fullnessDistanceThresholdMm(),
                            port.fullnessSampleCount(), port.fullnessMinimumValidSampleCount(), port.fullnessEchoTimeoutUs(),
                            port.weightStableWindowMs(), port.weightMaximumFluctuationGram(), port.weightRequiredSampleCount(),
                            port.weightMeasurementTimeoutMs(), port.weightMinimumGram(), port.weightMaximumGram(),
                            port.calibrationVersion(), port.infraredSampleTimeoutMs(), port.deliveryDoorOperationTimeoutMs()
                    )).toList());
        }
    }
}
