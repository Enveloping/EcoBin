package org.enveloping.ecobin.device.api.result;

import java.util.List;

/**
 * device 所有事实形成的单个投口只读快照。
 */
public record DeliveryDevicePortOptionSnapshot(
        int portNo,
        String displayName,
        String unitPriceYuanPerKg,
        String fullnessMode,
        List<String> blockers) {

    public DeliveryDevicePortOptionSnapshot {
        if (portNo < 1 || portNo > 6) {
            throw new IllegalArgumentException(
                    "portNo must be between 1 and 6");
        }
        blockers = List.copyOf(blockers);
    }
}
