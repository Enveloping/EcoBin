package org.enveloping.ecobin.identity.api.legacy;

import java.math.BigDecimal;

/**
 * 旧余额嵌在 sys_user 时的窄过渡端口；F-03 必须由 funds 正式事实替换。
 */
public interface LegacyOrganizationUserFinancePort {

    LegacyOrganizationUserSnapshot findAccount(LegacyOrganizationUserId userId);

    void addBalance(LegacyOrganizationUserId userId, BigDecimal amount);

    boolean freezeForWithdraw(LegacyOrganizationUserId userId, BigDecimal amount);

    void settlePending(LegacyOrganizationUserId userId, BigDecimal amount);

    void refundPending(LegacyOrganizationUserId userId, BigDecimal amount);

    LegacyOrganizationUserStatistics statistics();
}
