package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedPlatformDeviceAssetFactEvent;

/** 接收永久机构分配前产生的平台级设备故障和安全事实。 */
public interface TrustedPlatformDeviceAssetFactPort {

    TrustedDeviceEventApplyResult apply(
            TrustedPlatformDeviceAssetFactEvent event);
}
