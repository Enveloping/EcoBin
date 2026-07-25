package org.enveloping.ecobin.recycling.api.legacy;

import java.math.BigDecimal;

/**
 * integration/HTTP 适配器提交给旧清运毛重行为的不可变命令。
 */
public record LegacyCleanGrossCommand(
        String sn,
        Long cleanOrderId,
        BigDecimal weight,
        String photoOpenOutside,
        String photoOpenInside,
        String photoCloseOutside,
        String photoCloseInside) {
}
