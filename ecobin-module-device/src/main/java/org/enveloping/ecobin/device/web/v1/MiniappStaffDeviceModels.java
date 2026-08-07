package org.enveloping.ecobin.device.web.v1;

import java.time.Instant;
import java.util.List;

public final class MiniappStaffDeviceModels {

    private MiniappStaffDeviceModels() { }

    public record DeviceSummary(
            String deviceCode,
            String displayName,
            String address,
            String edgeConnectionStatus,
            String mcuLinkStatus,
            String safetyStatus,
            int portCount,
            Instant lastHeartbeatAt) { }

    public record PortSummary(
            int portNo,
            String displayName,
            boolean enabled,
            String deliveryDoorState,
            String weightSensorHealth,
            String infraredSensorHealth,
            String smokeState,
            String safetyStatus,
            Instant lastObservedAt) { }

    public record DeviceDetail(
            DeviceSummary device,
            List<PortSummary> ports,
            Instant asOf) { }
}
