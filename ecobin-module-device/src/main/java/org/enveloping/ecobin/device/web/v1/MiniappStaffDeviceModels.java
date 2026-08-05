package org.enveloping.ecobin.device.web.v1;

import java.time.Instant;
import java.util.List;

public final class MiniappStaffDeviceModels {

    private MiniappStaffDeviceModels() { }

    public record DeploymentSummary(
            String deploymentCode,
            String displayName,
            String address,
            String lifecycleStatus,
            boolean businessEnabled,
            String edgeConnectionStatus,
            String mcuLinkStatus,
            String safetyStatus,
            int portCount,
            Instant lastHeartbeatAt) { }

    public record PortSummary(
            int portNo,
            String displayName,
            boolean businessEnabled,
            String deliveryDoorState,
            String weightSensorHealth,
            String infraredSensorHealth,
            String smokeState,
            String safetyStatus,
            Instant lastObservedAt) { }

    public record DeploymentDetail(
            DeploymentSummary deployment,
            List<PortSummary> ports,
            Instant asOf) { }
}
