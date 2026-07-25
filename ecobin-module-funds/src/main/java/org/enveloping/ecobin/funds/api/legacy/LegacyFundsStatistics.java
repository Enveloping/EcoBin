package org.enveloping.ecobin.funds.api.legacy;

import java.math.BigDecimal;

/**
 * operations 旧统计页所需的提现聚合。
 */
public record LegacyFundsStatistics(
        long withdrawCount,
        BigDecimal requestedAmount,
        BigDecimal approvedAmount) {
}
