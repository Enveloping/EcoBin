package org.enveloping.ecobin.device.application.startdelivery;

import org.enveloping.ecobin.device.api.value.DeviceRuntimeWeightPolicy;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;

import java.security.MessageDigest;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Objects;
import java.util.Set;

final class StartDeliveryDevicePolicy {

    private static final Set<String> KNOWN_ACTUATOR_FAILURES = Set.of(
            "TIMEOUT",
            "ACTUATOR_FAULT",
            "SWITCH_FAULT",
            "DISCONNECTED");

    private StartDeliveryDevicePolicy() {
    }

    static void requireNoActiveSession(List<Long> sessionIds) {
        if (!sessionIds.isEmpty()) {
            throw new TargetApiException(
                    409,
                    "DELIVERY.SESSION_ALREADY_ACTIVE",
                    "当前用户已有未结束的投递会话");
        }
    }

    static void requireDeploymentAvailable(
            StartDeliveryDeviceRepository.AssetRow asset,
            StartDeliveryDeviceRepository.ActiveDeploymentRow active,
            StartDeliveryDeviceRepository.DeploymentRow deployment,
            long tenantId,
            long organizationId,
            String deploymentCode,
            int portNo) {
        boolean valid = asset.id() == active.assetId()
                && asset.id() == deployment.assetId()
                && active.deploymentId() == deployment.id()
                && active.tenantId() == tenantId
                && active.organizationId() == organizationId
                && deployment.tenantId() == tenantId
                && deployment.organizationId() == organizationId
                && Objects.equals(
                        deployment.publicCode(),
                        deploymentCode)
                && "IN_USE".equals(asset.lifecycleStatus())
                && "ENABLED".equals(deployment.lifecycleStatus())
                && deployment.businessEnabled()
                && portNo <= asset.expectedPortCount();
        if (!valid) {
            throw deploymentUnavailable();
        }
    }

    static void requireUnoccupied(
            StartDeliveryDeviceRepository.OccupancyRow occupancy) {
        if (occupancy != null) {
            throw new TargetApiException(
                    409,
                    "DEVICE.DEVICE_BUSY",
                    "设备当前正在执行其他投递或清运作业");
        }
    }

    static void requireExactAppliedConfiguration(
            StartDeliveryDeviceRepository.ConfigurationRow configuration,
            StartDeliveryDeviceRepository.ConfigurationApplicationRow
                    application,
            StartDeliveryDeviceRepository.DeploymentRuntimeRow runtime) {
        boolean applicationExact =
                "APPLIED".equals(application.status())
                        && application.appliedAt() != null
                        && Objects.equals(
                                application.reportedVersion(),
                                configuration.version())
                        && sameDigest(
                                application.reportedContentSha256(),
                                configuration.contentSha256())
                        && sameDigest(
                                application.reportedMcuPayloadSha256(),
                                configuration.mcuPayloadSha256());
        boolean orangePiExact =
                Objects.equals(
                        runtime.orangePiConfigurationVersion(),
                        configuration.version())
                        && sameDigest(
                                runtime.orangePiConfigurationContentSha256(),
                                configuration.contentSha256())
                        && sameDigest(
                                runtime
                                        .orangePiConfigurationMcuPayloadSha256(),
                                configuration.mcuPayloadSha256());
        if (!applicationExact || !orangePiExact) {
            throw new TargetApiException(
                    422,
                    "DEVICE.CONFIGURATION_NOT_APPLIED",
                    "设备尚未由香橙派精确确认当前最新配置，不能开始投递");
        }
    }

    static void requireTrustedDeploymentRuntime(
            StartDeliveryDeviceRepository.DeploymentRuntimeRow runtime,
            StartDeliveryDeviceRepository.ConfigurationRow configuration,
            LocalDateTime now) {
        boolean trustedSource =
                runtime.trustedRuntimeEdgeEventId() != null
                        && "DEVICE_RUNTIME_SNAPSHOT".equals(
                                runtime.trustedRuntimeEdgeEventType())
                        && runtime.trustedRuntimeSequence() != null
                        && runtime.trustedRuntimeSequence() > 0
                        && runtime.trustedRuntimeReceivedAt() != null;
        if (!trustedSource
                || !"ONLINE".equals(runtime.edgeConnectionStatus())) {
            throw new TargetApiException(
                    422,
                    "DEVICE.DEPLOYMENT_UNAVAILABLE",
                    "设备当前没有 OneNet 在线事实和可信运行快照");
        }
        if (!"HEALTHY".equals(runtime.localStorageState())
                || !"OK".equals(runtime.localStorageHealth())) {
            throw new TargetApiException(
                    422,
                    "DEVICE.DEPLOYMENT_UNAVAILABLE",
                    "香橙派本地存储当前不能可靠保存整场投递事实");
        }
        if (!"SAFE".equals(runtime.safetyStatus())) {
            throw safetyLocked(
                    "设备当前存在阻断新作业的可信安全状态");
        }
    }

    static void requirePortConfigured(
            StartDeliveryDeviceRepository.PortConfigurationRow port) {
        if (!port.businessEnabled()
                || port.unitPriceYuanPerKg() == null
                || port.unitPriceYuanPerKg().signum() <= 0) {
            throw portUnavailable("当前投口未启用或没有有效单价");
        }
    }

    static void requireTrustedPortRuntime(
            StartDeliveryDeviceRepository.PortRuntimeRow port,
            StartDeliveryDeviceRepository.PortConfigurationRow
                    configuration,
            StartDeliveryDeviceRepository.DeploymentRuntimeRow deployment) {
        boolean sameTrustedSnapshot =
                port.trustedRuntimeEdgeEventId() != null
                        && "DEVICE_RUNTIME_SNAPSHOT".equals(
                                port.trustedRuntimeEdgeEventType())
                        && port.trustedRuntimeSequence() != null
                        && port.trustedRuntimeSequence() > 0
                        && Objects.equals(
                                port.trustedRuntimeEdgeEventId(),
                                deployment.trustedRuntimeEdgeEventId())
                        && Objects.equals(
                                port.trustedRuntimeSequence(),
                                deployment.trustedRuntimeSequence());
        if (!sameTrustedSnapshot) {
            throw portUnavailable(
                    "当前投口缺少与整机一致的香橙派可信运行快照");
        }
        if (port.pendingDeliveryResultSessionId() != null) {
            throw portUnavailable(
                    "当前投口仍在等待上一场投递的唯一完成结果");
        }
        boolean unsafe = !"SAFE".equals(port.safetyStatus())
                || !"DEENERGIZED".equals(
                        port.cleanLockPowerState())
                || !"OK".equals(port.cleanSolenoidHealth())
                || !"NORMAL".equals(port.smokeState())
                || !"OK".equals(port.smokeSensorHealth())
                || port.runtimeFaultBitmap() == null
                || port.runtimeFaultBitmap() != 0
                || KNOWN_ACTUATOR_FAILURES.contains(
                        port.deliveryDoorActuatorHealth());
        if (unsafe) {
            throw safetyLocked(
                    "当前投口的香橙派可信快照显示存在物理安全阻断");
        }
        boolean weightReady = DeviceRuntimeWeightPolicy.isStartEligible(
                port.weightSensorHealth(),
                port.weightMeasurementStatus(),
                port.weightValueAvailable(),
                port.reportedWeightGrams(),
                port.weightValueKind(),
                port.calibrationVersion(),
                configuration.calibrationVersion());
        if (!weightReady) {
            throw portUnavailable(
                    "当前投口没有可用于开始前校验的可信稳定称重事实");
        }
    }

    static TargetApiException deploymentUnavailable() {
        return new TargetApiException(
                422,
                "DEVICE.DEPLOYMENT_UNAVAILABLE",
                "设备部署当前不可用于开始投递");
    }

    static TargetApiException configurationNotApplied() {
        return new TargetApiException(
                422,
                "DEVICE.CONFIGURATION_NOT_APPLIED",
                "设备还没有精确应用当前最新配置");
    }

    static TargetApiException portUnavailable(String detail) {
        return new TargetApiException(
                422,
                "DEVICE.PORT_UNAVAILABLE",
                detail);
    }

    private static TargetApiException safetyLocked(String detail) {
        return new TargetApiException(
                422,
                "DEVICE.SAFETY_LOCKED",
                detail);
    }

    private static boolean sameDigest(byte[] left, byte[] right) {
        return left != null
                && right != null
                && left.length == 32
                && right.length == 32
                && MessageDigest.isEqual(left, right);
    }
}
