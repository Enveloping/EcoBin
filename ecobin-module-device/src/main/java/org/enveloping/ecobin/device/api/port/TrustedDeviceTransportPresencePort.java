package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.DeviceTransportPresenceApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceTransportEvent;

public interface TrustedDeviceTransportPresencePort {

    DeviceTransportPresenceApplyResult apply(
            TrustedDeviceTransportEvent event);

    /**
     * @deprecated OneNet 下行错误不是设备离线的权威事实；仅返回当前生命周期投影。
     */
    @Deprecated(forRemoval = true)
    DeviceTransportPresenceApplyResult observeOutboundOffline(
            String hardwareSn);

    /**
     * Records that an already-authorized reliable device inbox message was
     * received. Callers must invoke this only after consuming the trusted
     * inbox reference and resolving the authoritative hardware identity.
     * 普通已鉴权消息不再改变在线状态；该入口仅返回当前生命周期投影。
     */
    @Deprecated(forRemoval = true)
    DeviceTransportPresenceApplyResult observeAuthenticatedMessage(
            String hardwareSn,
            long sourceInboxId);
}
