package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationDeviceRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationDeviceSnapshot;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationPortRequest;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationPortSnapshot;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ConfigurationReleaseRequest;
import org.enveloping.ecobin.device.api.port.DeviceCommandCanonicalizationPort;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.springframework.stereotype.Component;

import java.io.ByteArrayOutputStream;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.TreeSet;

/**
 * Canonical configuration Implementation shared by persistence, OneNet and
 * UART. It deliberately accepts no floating-point values.
 */
@Component
public class DeviceConfigurationCanonicalizer
        implements DeviceCommandCanonicalizationPort {

    static final int CONFIGURATION_SCHEMA_VERSION = 1;
    private static final long UINT32_MAX = 4_294_967_295L;
    private static final long SAFE_INTEGER_MAX = 9_007_199_254_740_991L;
    private static final byte[] MCU_DOMAIN =
            domain("ECOBIN:UART:MCU-CONFIG:v1");
    private static final Set<String> FULLNESS_MODES = Set.of(
            "INFRARED_ONLY", "WEIGHT_ONLY", "INFRARED_OR_WEIGHT");
    private static final Set<String> SENSOR_KINDS = Set.of(
            "ULTRASONIC", "DIGITAL_INFRARED");

    public NormalizedConfiguration normalize(
            ConfigurationReleaseRequest request,
            int expectedPortCount) {
        if (request == null
                || request.device() == null
                || request.ports() == null) {
            throw valueInvalid("完整配置不能为空");
        }
        ConfigurationDeviceSnapshot device =
                normalizeDevice(request.device());
        List<NormalizedPort> ports = request.ports().stream()
                .map(this::normalizePort)
                .sorted(Comparator.comparingInt(port -> port.view().portNo()))
                .toList();
        TreeSet<Integer> actualPorts = new TreeSet<>();
        ports.forEach(port -> actualPorts.add(port.view().portNo()));
        if (actualPorts.size() != ports.size()
                || actualPorts.size() != expectedPortCount) {
            throw portSetInvalid();
        }
        for (int portNo = 1; portNo <= expectedPortCount; portNo++) {
            if (!actualPorts.contains(portNo)) {
                throw portSetInvalid();
            }
        }

        Map<String, Object> canonical = new LinkedHashMap<>();
        canonical.put("schemaVersion", CONFIGURATION_SCHEMA_VERSION);
        canonical.put("device", contentDevice(device));
        canonical.put("ports", ports.stream()
                .map(this::contentPort)
                .toList());
        byte[] canonicalBytes = canonicalBytes(canonical);
        byte[] contentSha256 = sha256(canonicalBytes);
        return new NormalizedConfiguration(
                device,
                ports,
                canonicalBytes,
                contentSha256,
                hex(contentSha256));
    }

    public byte[] mcuPayloadSha256(
            long versionNo,
            NormalizedConfiguration configuration) {
        return mcuPayloadSha256(
                versionNo,
                configuration.contentSha256(),
                configuration.device(),
                configuration.ports());
    }

    byte[] mcuPayloadSha256(
            long versionNo,
            byte[] contentSha256,
            ConfigurationDeviceSnapshot device,
            List<NormalizedPort> ports) {
        if (versionNo < 1 || versionNo > SAFE_INTEGER_MAX) {
            throw valueInvalid("配置版本超出机器协议范围");
        }
        if (contentSha256 == null || contentSha256.length != 32) {
            throw valueInvalid("配置内容摘要必须是 SHA-256");
        }
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        output.writeBytes(MCU_DOMAIN);
        writeUnsigned(output, versionNo, 8);
        output.writeBytes(contentSha256);
        writeUnsigned(output, ports.size(), 1);
        writeUnsigned(output, device.continueDeliveryWaitMs(), 4);
        writeUnsigned(output, device.negativeWeightThresholdGram(), 4);
        writeUnsigned(output, device.deliveryAutoCloseMs(), 4);
        writeUnsigned(output, device.weightMeasurementTimeoutMs(), 4);
        writeUnsigned(output, device.deliveryDoorTravelWaitMs(), 4);
        writeUnsigned(output, device.cleanSolenoidPulseMs(), 4);
        writeUnsigned(output, device.smokeMonitoringEnabled() ? 1 : 0, 1);
        for (NormalizedPort port : ports) {
            ConfigurationPortSnapshot view = port.view();
            writeUnsigned(output, view.portNo(), 1);
            writeUnsigned(output, view.enabled() ? 1 : 0, 1);
            writeUnsigned(output, port.unitPriceTenThousandths(), 4);
            writeUnsigned(output, fullnessModeCode(port.machineFullnessMode()), 1);
            writeUnsigned(output, port.configuredFullWeightGrams(), 4);
            writeUnsigned(output, view.fullnessInitialDelayMs(), 4);
            writeUnsigned(output, sensorKindCode(view.fullnessSensorKind()), 1);
            writeUnsigned(output, view.fullnessDistanceThresholdMm(), 4);
            writeUnsigned(output, view.fullnessSampleCount(), 1);
            writeUnsigned(
                    output, view.fullnessMinimumValidSampleCount(), 1);
            writeUnsigned(output, view.fullnessEchoTimeoutUs(), 4);
            writeUnsigned(output, view.weightStableWindowMs(), 4);
            writeUnsigned(output, view.weightMaximumFluctuationGram(), 4);
            writeUnsigned(output, view.weightRequiredSampleCount(), 2);
            writeUnsigned(output, view.weightMeasurementTimeoutMs(), 4);
            writeSigned(output, view.weightMinimumGram(), 4);
            writeSigned(output, view.weightMaximumGram(), 4);
            writeUnsigned(output, view.calibrationVersion(), 4);
        }
        return sha256(output.toByteArray());
    }

    public Map<String, Object> commandPayload(
            String applicationUid,
            long versionNo,
            NormalizedConfiguration configuration,
            byte[] mcuPayloadSha256) {
        Map<String, Object> config = new LinkedHashMap<>();
        config.put("version", versionNo);
        config.put("contentSha256", configuration.contentSha256Hex());
        config.put("mcuPayloadSha256", hex(mcuPayloadSha256));

        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("applicationUid", applicationUid);
        payload.put("config", config);
        payload.put("deviceConfig", machineDevice(configuration.device()));
        payload.put("ports", configuration.ports().stream()
                .map(this::machinePort)
                .toList());
        return payload;
    }

    public byte[] payloadSha256(Object payload) {
        return sha256(canonicalBytes(payload));
    }

    public byte[] canonicalBytes(Object value) {
        StringBuilder output = new StringBuilder();
        appendValue(output, value, "$");
        return output.toString().getBytes(StandardCharsets.UTF_8);
    }

    public String hex(byte[] value) {
        return HexFormat.of().formatHex(value);
    }

    private ConfigurationDeviceSnapshot normalizeDevice(
            ConfigurationDeviceRequest request) {
        String displayName = requiredTrimmed(
                request.displayName(), 100, "设备显示名");
        String address = optionalTrimmed(request.address(), 500, "地址");
        Coordinates coordinates = coordinates(
                request.longitude(), request.latitude());
        long edgeHeartbeat = range(
                request.edgeHeartbeatIntervalMs(),
                1, UINT32_MAX, "香橙派心跳周期");
        long edgeMiss = range(
                request.edgeHeartbeatMissThreshold(),
                1, Integer.MAX_VALUE, "香橙派心跳丢失阈值");
        long mcuHeartbeat = range(
                request.mcuHeartbeatIntervalMs(),
                1, UINT32_MAX, "MCU 心跳周期");
        long mcuMiss = range(
                request.mcuHeartbeatMissThreshold(),
                1, Integer.MAX_VALUE, "MCU 心跳丢失阈值");
        long retry = range(
                request.doorCloseRetryLimit(),
                0, Integer.MAX_VALUE, "关门重试次数");
        long continueWait = range(
                request.continueDeliveryWaitMs(),
                1_000, UINT32_MAX, "继续投递等待时间");
        if (continueWait != 30_000) {
            throw valueInvalid("M0 继续投递等待时间必须为 30000ms");
        }
        long negativeThreshold = range(
                request.negativeWeightThresholdGram(),
                1, UINT32_MAX, "负重量阈值");
        long autoClose = range(
                defaultLong(request.deliveryAutoCloseMs(), 120_000),
                1_000, 600_000, "投递门自动关闭时间");
        long measurementTimeout = range(
                defaultLong(request.weightMeasurementTimeoutMs(), 6_000),
                1_000, 6_000, "整机称重超时");
        long travelWait = range(
                defaultLong(request.deliveryDoorTravelWaitMs(), 30_000),
                30_000, 45_000, "投递门机械行程等待");
        long pulse = range(
                defaultLong(request.cleanSolenoidPulseMs(), 1_000),
                1, 5_000, "清运电磁阀脉冲");
        boolean smoke = request.smokeMonitoringEnabled() == null
                || request.smokeMonitoringEnabled();
        return new ConfigurationDeviceSnapshot(
                displayName,
                address,
                coordinates.longitude(),
                coordinates.latitude(),
                edgeHeartbeat,
                edgeMiss,
                mcuHeartbeat,
                mcuMiss,
                retry,
                continueWait,
                negativeThreshold,
                autoClose,
                measurementTimeout,
                travelWait,
                pulse,
                smoke);
    }

    private NormalizedPort normalizePort(ConfigurationPortRequest request) {
        if (request == null || request.portNo() == null) {
            throw portSetInvalid();
        }
        int portNo = Math.toIntExact(range(
                request.portNo().longValue(), 1, 6, "投口编号"));
        String displayName = requiredTrimmed(
                request.displayName(), 32, "投口显示名");
        if (request.enabled() == null) {
            throw valueInvalid("投口启用状态不能为空");
        }
        DecimalInteger price = decimalInteger(
                request.unitPriceYuanPerKg(),
                4,
                1,
                UINT32_MAX,
                "回收单价");
        DecimalInteger fullWeight = decimalInteger(
                request.fullnessWeightKg(),
                3,
                1,
                UINT32_MAX,
                "满溢重量");
        String fullnessMode = requiredTrimmed(
                request.fullnessMode(), 32, "满溢模式").toUpperCase();
        if (!FULLNESS_MODES.contains(fullnessMode)) {
            throw valueInvalid("不支持的满溢模式");
        }
        long deliverySettle = range(
                request.deliverySettleDelayMs(),
                0, UINT32_MAX, "投递稳定等待");
        long fullnessInitial = range(
                request.fullnessInitialDelayMs(),
                0, UINT32_MAX, "满溢初次等待");
        long fullnessRecheck = range(
                request.fullnessRecheckDelayMs(),
                0, UINT32_MAX, "满溢复检等待");
        long doorTimeout = range(
                request.doorAutoCloseTimeoutMs(),
                1_000, UINT32_MAX, "投递门自动关闭超时");
        String sensorKind = request.fullnessSensorKind() == null
                ? "ULTRASONIC"
                : request.fullnessSensorKind().trim().toUpperCase();
        if (!SENSOR_KINDS.contains(sensorKind)) {
            throw valueInvalid("不支持的满溢传感器类型");
        }
        long distance = range(
                defaultLong(request.fullnessDistanceThresholdMm(), 600),
                1, 4_000, "满溢距离阈值");
        int samples = Math.toIntExact(range(
                defaultInteger(request.fullnessSampleCount(), 5),
                3, 9, "满溢采样数"));
        int validSamples = Math.toIntExact(range(
                defaultInteger(
                        request.fullnessMinimumValidSampleCount(), 3),
                1, samples, "满溢最小有效采样数"));
        long echoTimeout = range(
                defaultLong(request.fullnessEchoTimeoutUs(), 30_000),
                100, 100_000, "超声回波超时");
        long stableWindow = range(
                defaultLong(request.weightStableWindowMs(), 1_500),
                1, 6_000, "重量稳定窗口");
        long maximumFluctuation = range(
                defaultLong(
                        request.weightMaximumFluctuationGram(), 20),
                0, UINT32_MAX, "重量最大波动");
        int requiredSamples = Math.toIntExact(range(
                defaultInteger(request.weightRequiredSampleCount(), 10),
                1, 65_535, "重量最小采样数"));
        long weightTimeout = range(
                defaultLong(request.weightMeasurementTimeoutMs(), 6_000),
                1_000, 6_000, "投口称重超时");
        long weightMinimum = range(
                defaultLong(request.weightMinimumGram(), -5_000),
                Integer.MIN_VALUE, Integer.MAX_VALUE, "重量下限");
        long weightMaximum = range(
                defaultLong(request.weightMaximumGram(), 100_000),
                1, Integer.MAX_VALUE, "重量上限");
        if (weightMinimum >= weightMaximum) {
            throw valueInvalid("重量下限必须小于重量上限");
        }
        long calibration = range(
                defaultLong(request.calibrationVersion(), 0),
                0, UINT32_MAX, "标定版本");
        long infraredTimeout = range(
                defaultLong(request.infraredSampleTimeoutMs(), 3_000),
                1, UINT32_MAX, "红外采样超时");
        long doorOperationTimeout = range(
                defaultLong(
                        request.deliveryDoorOperationTimeoutMs(), 60_000),
                1, UINT32_MAX, "投递门操作超时");

        ConfigurationPortSnapshot view = new ConfigurationPortSnapshot(
                portNo,
                displayName,
                request.enabled(),
                price.canonicalDecimal(),
                fullnessMode,
                fullWeight.canonicalDecimal(),
                deliverySettle,
                fullnessInitial,
                fullnessRecheck,
                doorTimeout,
                sensorKind,
                distance,
                samples,
                validSamples,
                echoTimeout,
                stableWindow,
                maximumFluctuation,
                requiredSamples,
                weightTimeout,
                weightMinimum,
                weightMaximum,
                calibration,
                infraredTimeout,
                doorOperationTimeout);
        return new NormalizedPort(
                view,
                price.scaledInteger(),
                fullWeight.scaledInteger(),
                machineFullnessMode(fullnessMode));
    }

    private Map<String, Object> contentDevice(
            ConfigurationDeviceSnapshot device) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("displayName", device.displayName());
        result.put("address", device.address());
        result.put("longitude", device.longitude());
        result.put("latitude", device.latitude());
        result.put(
                "edgeHeartbeatIntervalMs",
                device.edgeHeartbeatIntervalMs());
        result.put(
                "edgeHeartbeatMissThreshold",
                device.edgeHeartbeatMissThreshold());
        result.put(
                "mcuHeartbeatIntervalMs",
                device.mcuHeartbeatIntervalMs());
        result.put(
                "mcuHeartbeatMissThreshold",
                device.mcuHeartbeatMissThreshold());
        result.put("doorCloseRetryLimit", device.doorCloseRetryLimit());
        result.put(
                "continueDeliveryWaitMs",
                device.continueDeliveryWaitMs());
        result.put(
                "negativeWeightThresholdGram",
                device.negativeWeightThresholdGram());
        result.put("deliveryAutoCloseMs", device.deliveryAutoCloseMs());
        result.put(
                "weightMeasurementTimeoutMs",
                device.weightMeasurementTimeoutMs());
        result.put(
                "deliveryDoorTravelWaitMs",
                device.deliveryDoorTravelWaitMs());
        result.put(
                "cleanSolenoidPulseMs",
                device.cleanSolenoidPulseMs());
        result.put(
                "smokeMonitoringEnabled",
                device.smokeMonitoringEnabled());
        return result;
    }

    private Map<String, Object> contentPort(NormalizedPort port) {
        ConfigurationPortSnapshot view = port.view();
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("portNo", view.portNo());
        result.put("displayName", view.displayName());
        result.put("enabled", view.enabled());
        result.put("unitPriceYuanPerKg", view.unitPriceYuanPerKg());
        result.put("fullnessMode", view.fullnessMode());
        result.put("fullnessWeightKg", view.fullnessWeightKg());
        result.put(
                "deliverySettleDelayMs", view.deliverySettleDelayMs());
        result.put(
                "fullnessInitialDelayMs", view.fullnessInitialDelayMs());
        result.put(
                "fullnessRecheckDelayMs", view.fullnessRecheckDelayMs());
        result.put(
                "doorAutoCloseTimeoutMs", view.doorAutoCloseTimeoutMs());
        result.put("fullnessSensorKind", view.fullnessSensorKind());
        result.put(
                "fullnessDistanceThresholdMm",
                view.fullnessDistanceThresholdMm());
        result.put("fullnessSampleCount", view.fullnessSampleCount());
        result.put(
                "fullnessMinimumValidSampleCount",
                view.fullnessMinimumValidSampleCount());
        result.put(
                "fullnessEchoTimeoutUs", view.fullnessEchoTimeoutUs());
        result.put("weightStableWindowMs", view.weightStableWindowMs());
        result.put(
                "weightMaximumFluctuationGram",
                view.weightMaximumFluctuationGram());
        result.put(
                "weightRequiredSampleCount",
                view.weightRequiredSampleCount());
        result.put(
                "weightMeasurementTimeoutMs",
                view.weightMeasurementTimeoutMs());
        result.put("weightMinimumGram", view.weightMinimumGram());
        result.put("weightMaximumGram", view.weightMaximumGram());
        result.put("calibrationVersion", view.calibrationVersion());
        result.put(
                "infraredSampleTimeoutMs", view.infraredSampleTimeoutMs());
        result.put(
                "deliveryDoorOperationTimeoutMs",
                view.deliveryDoorOperationTimeoutMs());
        return result;
    }

    private Map<String, Object> machineDevice(
            ConfigurationDeviceSnapshot device) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put(
                "continueDeliveryWaitMs",
                device.continueDeliveryWaitMs());
        result.put(
                "negativeWeightThresholdGrams",
                device.negativeWeightThresholdGram());
        result.put("deliveryAutoCloseMs", device.deliveryAutoCloseMs());
        result.put(
                "weightMeasurementTimeoutMs",
                device.weightMeasurementTimeoutMs());
        result.put(
                "deliveryDoorTravelWaitMs",
                device.deliveryDoorTravelWaitMs());
        result.put(
                "cleanSolenoidPulseMs",
                device.cleanSolenoidPulseMs());
        result.put(
                "smokeMonitoringEnabled",
                device.smokeMonitoringEnabled());
        return result;
    }

    private Map<String, Object> machinePort(NormalizedPort port) {
        ConfigurationPortSnapshot view = port.view();
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("portNo", view.portNo());
        result.put("displayName", view.displayName());
        result.put("enabled", view.enabled());
        result.put(
                "unitPriceTenThousandths",
                port.unitPriceTenThousandths());
        result.put("fullnessMode", port.machineFullnessMode());
        result.put(
                "configuredFullWeightGrams",
                port.configuredFullWeightGrams());
        result.put(
                "fullnessSettleWaitMs",
                view.fullnessInitialDelayMs());
        result.put("fullnessSensorKind", view.fullnessSensorKind());
        result.put(
                "fullnessDistanceThresholdMm",
                view.fullnessDistanceThresholdMm());
        result.put("fullnessSampleCount", view.fullnessSampleCount());
        result.put(
                "fullnessMinimumValidSampleCount",
                view.fullnessMinimumValidSampleCount());
        result.put(
                "fullnessEchoTimeoutUs", view.fullnessEchoTimeoutUs());
        result.put("weightStableWindowMs", view.weightStableWindowMs());
        result.put(
                "weightMaximumFluctuationGrams",
                view.weightMaximumFluctuationGram());
        result.put(
                "weightRequiredSampleCount",
                view.weightRequiredSampleCount());
        result.put(
                "weightMeasurementTimeoutMs",
                view.weightMeasurementTimeoutMs());
        result.put("weightMinimumGrams", view.weightMinimumGram());
        result.put("weightMaximumGrams", view.weightMaximumGram());
        result.put("calibrationVersion", view.calibrationVersion());
        return result;
    }

    private static String machineFullnessMode(String value) {
        return switch (value) {
            case "INFRARED_ONLY" -> "SENSOR_ONLY";
            case "WEIGHT_ONLY" -> "WEIGHT_ONLY";
            case "INFRARED_OR_WEIGHT" -> "SENSOR_OR_WEIGHT";
            default -> throw valueInvalid("不支持的满溢模式");
        };
    }

    private static int fullnessModeCode(String value) {
        return switch (value) {
            case "SENSOR_ONLY" -> 1;
            case "WEIGHT_ONLY" -> 2;
            case "SENSOR_OR_WEIGHT" -> 3;
            default -> throw valueInvalid("不支持的 MCU 满溢模式");
        };
    }

    private static int sensorKindCode(String value) {
        return switch (value) {
            case "ULTRASONIC" -> 1;
            case "DIGITAL_INFRARED" -> 2;
            default -> throw valueInvalid("不支持的 MCU 满溢传感器");
        };
    }

    private static Coordinates coordinates(
            String longitudeValue,
            String latitudeValue) {
        if (blank(longitudeValue) && blank(latitudeValue)) {
            return new Coordinates(null, null);
        }
        if (blank(longitudeValue) || blank(latitudeValue)) {
            throw valueInvalid("经纬度必须同时提供");
        }
        BigDecimal longitude = decimal(
                longitudeValue, 7, "经度");
        BigDecimal latitude = decimal(latitudeValue, 7, "纬度");
        if (longitude.compareTo(BigDecimal.valueOf(-180)) < 0
                || longitude.compareTo(BigDecimal.valueOf(180)) > 0
                || latitude.compareTo(BigDecimal.valueOf(-90)) < 0
                || latitude.compareTo(BigDecimal.valueOf(90)) > 0) {
            throw valueInvalid("经纬度超出有效范围");
        }
        return new Coordinates(
                canonicalDecimal(longitude, 7),
                canonicalDecimal(latitude, 7));
    }

    private static DecimalInteger decimalInteger(
            String value,
            int scale,
            long minimum,
            long maximum,
            String field) {
        BigDecimal decimal = decimal(value, scale, field);
        long scaled;
        try {
            scaled = decimal.movePointRight(scale)
                    .setScale(0, RoundingMode.UNNECESSARY)
                    .longValueExact();
        } catch (ArithmeticException exception) {
            throw valueInvalid(field + "精度不符合契约");
        }
        if (scaled < minimum || scaled > maximum) {
            throw valueInvalid(field + "超出有效范围");
        }
        return new DecimalInteger(
                scaled,
                decimal.setScale(scale, RoundingMode.UNNECESSARY)
                        .toPlainString());
    }

    private static BigDecimal decimal(
            String value,
            int maximumScale,
            String field) {
        if (blank(value)) {
            throw valueInvalid(field + "不能为空");
        }
        try {
            BigDecimal result = new BigDecimal(value.trim());
            if (result.scale() > maximumScale) {
                throw valueInvalid(field + "小数位过多");
            }
            return result;
        } catch (NumberFormatException exception) {
            throw valueInvalid(field + "不是有效十进制数");
        }
    }

    private static String canonicalDecimal(
            BigDecimal value,
            int maximumScale) {
        BigDecimal normalized = value.setScale(
                Math.max(0, Math.min(value.scale(), maximumScale)),
                RoundingMode.UNNECESSARY).stripTrailingZeros();
        if (normalized.scale() < 0) {
            normalized = normalized.setScale(0);
        }
        return normalized.toPlainString();
    }

    private static long range(
            Number value,
            long minimum,
            long maximum,
            String field) {
        if (value == null) {
            throw valueInvalid(field + "不能为空");
        }
        long normalized = value.longValue();
        if (normalized < minimum || normalized > maximum) {
            throw valueInvalid(field + "超出有效范围");
        }
        return normalized;
    }

    private static long defaultLong(Long value, long fallback) {
        return value == null ? fallback : value;
    }

    private static int defaultInteger(Integer value, int fallback) {
        return value == null ? fallback : value;
    }

    private static String requiredTrimmed(
            String value,
            int maximumLength,
            String field) {
        if (blank(value)) {
            throw valueInvalid(field + "不能为空");
        }
        String result = value.trim();
        if (result.length() > maximumLength) {
            throw valueInvalid(field + "过长");
        }
        return result;
    }

    private static String optionalTrimmed(
            String value,
            int maximumLength,
            String field) {
        if (blank(value)) {
            return null;
        }
        return requiredTrimmed(value, maximumLength, field);
    }

    private static boolean blank(String value) {
        return value == null || value.isBlank();
    }

    private static void writeUnsigned(
            ByteArrayOutputStream output,
            long value,
            int byteCount) {
        long maximum = byteCount == 8
                ? Long.MAX_VALUE
                : (1L << (byteCount * 8)) - 1;
        if (value < 0 || value > maximum) {
            throw valueInvalid("MCU 配置无符号数超出范围");
        }
        for (int offset = byteCount - 1; offset >= 0; offset--) {
            output.write((int) (value >>> (offset * 8)) & 0xff);
        }
    }

    private static void writeSigned(
            ByteArrayOutputStream output,
            long value,
            int byteCount) {
        if (byteCount != 4
                || value < Integer.MIN_VALUE
                || value > Integer.MAX_VALUE) {
            throw valueInvalid("MCU 配置有符号数超出范围");
        }
        writeUnsigned(output, Integer.toUnsignedLong((int) value), byteCount);
    }

    private static byte[] domain(String value) {
        byte[] text = value.getBytes(StandardCharsets.UTF_8);
        byte[] result = new byte[text.length + 1];
        System.arraycopy(text, 0, result, 0, text.length);
        return result;
    }

    private static byte[] sha256(byte[] value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(value);
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable", exception);
        }
    }

    private static void appendValue(
            StringBuilder output,
            Object value,
            String path) {
        if (value == null) {
            output.append("null");
        } else if (value instanceof Boolean bool) {
            output.append(bool ? "true" : "false");
        } else if (value instanceof Byte
                || value instanceof Short
                || value instanceof Integer
                || value instanceof Long) {
            long number = ((Number) value).longValue();
            if (number < -SAFE_INTEGER_MAX || number > SAFE_INTEGER_MAX) {
                throw new IllegalArgumentException(
                        path + ": unsafe canonical JSON integer");
            }
            output.append(number);
        } else if (value instanceof Number) {
            throw new IllegalArgumentException(
                    path + ": floating-point canonical JSON is forbidden");
        } else if (value instanceof String text) {
            appendString(output, text);
        } else if (value instanceof List<?> values) {
            output.append('[');
            for (int index = 0; index < values.size(); index++) {
                if (index != 0) {
                    output.append(',');
                }
                appendValue(
                        output, values.get(index), path + "[" + index + "]");
            }
            output.append(']');
        } else if (value instanceof Map<?, ?> map) {
            List<Map.Entry<String, Object>> entries = new ArrayList<>();
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                if (!(entry.getKey() instanceof String)) {
                    throw new IllegalArgumentException(
                            path + ": canonical JSON key must be text");
                }
                @SuppressWarnings("unchecked")
                Map.Entry<String, Object> typed =
                        (Map.Entry<String, Object>) (Map.Entry<?, ?>) entry;
                entries.add(typed);
            }
            entries.sort(Comparator.comparing(Map.Entry::getKey));
            output.append('{');
            for (int index = 0; index < entries.size(); index++) {
                if (index != 0) {
                    output.append(',');
                }
                Map.Entry<String, Object> entry = entries.get(index);
                appendString(output, entry.getKey());
                output.append(':');
                appendValue(
                        output,
                        entry.getValue(),
                        path + "." + entry.getKey());
            }
            output.append('}');
        } else {
            throw new IllegalArgumentException(
                    path + ": unsupported canonical JSON value "
                            + value.getClass().getName());
        }
    }

    private static void appendString(
            StringBuilder output,
            String value) {
        Objects.requireNonNull(value, "value");
        output.append('"');
        for (int index = 0; index < value.length(); index++) {
            char current = value.charAt(index);
            switch (current) {
                case '"' -> output.append("\\\"");
                case '\\' -> output.append("\\\\");
                case '\b' -> output.append("\\b");
                case '\f' -> output.append("\\f");
                case '\n' -> output.append("\\n");
                case '\r' -> output.append("\\r");
                case '\t' -> output.append("\\t");
                default -> {
                    if (current <= 0x1f) {
                        output.append(String.format("\\u%04x", (int) current));
                    } else {
                        output.append(current);
                    }
                }
            }
        }
        output.append('"');
    }

    private static TargetApiException portSetInvalid() {
        return new TargetApiException(
                422,
                "DEVICE.CONFIGURATION_PORT_SET_INVALID",
                "配置必须恰好覆盖部署的全部投口");
    }

    private static TargetApiException valueInvalid(String detail) {
        return new TargetApiException(
                422,
                "DEVICE.CONFIGURATION_VALUE_INVALID",
                detail);
    }

    public record NormalizedConfiguration(
            ConfigurationDeviceSnapshot device,
            List<NormalizedPort> ports,
            byte[] canonicalBytes,
            byte[] contentSha256,
            String contentSha256Hex) {

        public NormalizedConfiguration {
            ports = List.copyOf(ports);
            canonicalBytes = canonicalBytes.clone();
            contentSha256 = contentSha256.clone();
        }

        @Override
        public byte[] canonicalBytes() {
            return canonicalBytes.clone();
        }

        @Override
        public byte[] contentSha256() {
            return contentSha256.clone();
        }
    }

    public record NormalizedPort(
            ConfigurationPortSnapshot view,
            long unitPriceTenThousandths,
            long configuredFullWeightGrams,
            String machineFullnessMode) {
    }

    private record Coordinates(String longitude, String latitude) {
    }

    private record DecimalInteger(
            long scaledInteger,
            String canonicalDecimal) {
    }
}
