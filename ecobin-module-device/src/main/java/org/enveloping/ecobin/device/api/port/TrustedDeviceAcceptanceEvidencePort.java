package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.DeviceAcceptanceEvidenceApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceAcceptanceEvent;

/** 写入真实设备通过 OneNet 提交的自动机器验收证据。 */
public interface TrustedDeviceAcceptanceEvidencePort {

    DeviceAcceptanceEvidenceApplyResult apply(
            TrustedDeviceAcceptanceEvent event);
}
