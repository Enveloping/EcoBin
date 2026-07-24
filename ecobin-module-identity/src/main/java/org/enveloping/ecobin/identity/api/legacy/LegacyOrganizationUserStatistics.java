package org.enveloping.ecobin.identity.api.legacy;

import java.math.BigDecimal;

/**
 * 旧统计页需要的 sys_user 聚合；F-03 迁移资金/统计事实后删除。
 */
public record LegacyOrganizationUserStatistics(
        long memberCount,
        long todayMemberCount,
        long disabledMemberCount,
        BigDecimal balanceTotal,
        BigDecimal pendingBalanceTotal) {
}
