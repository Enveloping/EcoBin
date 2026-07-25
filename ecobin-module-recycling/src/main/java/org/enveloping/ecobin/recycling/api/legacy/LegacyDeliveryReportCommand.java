package org.enveloping.ecobin.recycling.api.legacy;

import java.math.BigDecimal;

/**
 * integration/HTTP 适配器提交给旧投递行为的不可变命令。
 */
public record LegacyDeliveryReportCommand(
        String sn,
        Integer doorIndex,
        String messageId,
        BigDecimal weight,
        String photoOpenOutside,
        String photoOpenInside,
        String photoCloseOutside,
        String photoCloseInside) {
}
