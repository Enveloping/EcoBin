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

    static void requireAssetAvailable(
            StartDeliveryDeviceRepository.AssetRow asset,
            StartDeliveryDeviceRepository.SubjectStatusRow tenant,
            StartDeliveryDeviceRepository.SubjectStatusRow organization,
            long tenantId,
            long organizationId,
            String deviceCode,
            int portNo) {
        boolean valid = Objects.equals(asset.tenantId(), tenantId)
                && Objects.equals(asset.organizationId(), organizationId)
                && tenant.id() == tenantId
                && organization.id() == organizationId
                && "ENABLED".equals(tenant.status())
                && "ENABLED".equals(organization.status())
                && Objects.equals(
                        asset.devicePublicCode(),
                        deviceCode)
                && "NORMAL".equals(asset.lifecycleStatus())
                && "PASSED".equals(asset.acceptanceStatus())
                && portNo > 0
                && portNo <= asset.expectedPortCount();
        if (!valid) {
            throw assetUnavailable();
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

    static void requireSoftwareAdmission(
            StartDeliveryDeviceRepository.AssetRow asset) {
        if (!"PERMANENT_V1".equals(
                asset.managementArchitectureGeneration())) {
            return;
        }
        if ("ACCEPTING".equals(asset.businessAdmissionStatus())) {
            return;
        }

        String message = "PAUSED".equals(asset.businessAdmissionStatus())
                ? "设备正在维护或业务程序尚未准备好，暂时不能开始新的投递，请稍后再试或联系管理员"
                : "平台尚未确认设备业务程序是否可用，暂时不能开始新的投递，请稍后再试或联系管理员";
        throw new TargetApiException(
                422,
                "DEVICE.SOFTWARE_NOT_ACCEPTING",
                message);
    }

    static void requirePortConfigured(
            StartDeliveryDeviceRepository.PortConfigurationRow port) {
        if (!port.businessEnabled()
                || port.unitPriceYuanPerKg() == null
                || port.unitPriceYuanPerKg().signum() <= 0) {
            throw portUnavailable("当前投口未启用或没有有效单价");
        }
    }

    static void requireConfigurationApplied(
            StartDeliveryDeviceRepository.ConfigurationRow configuration) {
        if (!configuration.applicationApplied()
                || !configuration.runtimeApplied()) {
            throw configurationNotApplied();
        }
    }

    static TargetApiException assetUnavailable() {
        return new TargetApiException(
                422,
                "DEVICE.ASSET_UNAVAILABLE",
                "设备当前不可用于开始投递");
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
