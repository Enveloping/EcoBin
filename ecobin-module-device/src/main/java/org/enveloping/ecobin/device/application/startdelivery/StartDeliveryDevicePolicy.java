package org.enveloping.ecobin.device.application.startdelivery;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;

import java.util.List;
import java.util.Objects;

final class StartDeliveryDevicePolicy {

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

    static void requireOnenetOnline(
            StartDeliveryDeviceRepository.TransportPresenceRow presence) {
        if (presence == null
                || !"ONLINE".equals(
                        presence.onenetConnectionStatus())) {
            throw new TargetApiException(
                    422,
                    "DEVICE.OFFLINE",
                    "OneNet 当前未确认设备在线，不能创建投递任务");
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

}
