package org.enveloping.ecobin.device.api.legacy;

import java.math.BigDecimal;

/**
 * recycling 旧流程所需的最小投口快照，不暴露 device Entity。
 */
public record LegacyDoorSnapshot(
        LegacyDoorId id,
        LegacyDeviceId deviceId,
        Long tenantId,
        Integer doorIndex,
        Integer wasteType1,
        Integer wasteType2,
        BigDecimal price,
        Integer enabled) {
}
