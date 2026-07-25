package org.enveloping.ecobin.recycling.api.legacy;

/**
 * operations 旧概览所需的投递聚合。
 */
public record LegacyDeliveryStatistics(
        long todayCount,
        double todayWeight,
        long todayMemberCount,
        long monthCount,
        double monthWeight,
        double monthMoney) {
}
