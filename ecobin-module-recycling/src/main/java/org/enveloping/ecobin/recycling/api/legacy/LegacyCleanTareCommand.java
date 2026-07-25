package org.enveloping.ecobin.recycling.api.legacy;

import java.math.BigDecimal;

/**
 * integration/HTTP 适配器提交给旧清运去皮行为的不可变命令。
 */
public record LegacyCleanTareCommand(
        String sn,
        Long cleanOrderId,
        BigDecimal weight) {
}
