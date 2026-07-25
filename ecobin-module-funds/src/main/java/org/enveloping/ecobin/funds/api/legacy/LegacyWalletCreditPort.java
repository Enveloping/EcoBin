package org.enveloping.ecobin.funds.api.legacy;

import org.enveloping.ecobin.identity.api.legacy.LegacyOrganizationUserId;

import java.math.BigDecimal;

/**
 * 旧投递审核向 funds 提交返现的窄同步端口。
 */
public interface LegacyWalletCreditPort {

    void credit(
            LegacyOrganizationUserId userId,
            Long tenantId,
            BigDecimal amount,
            Long deliveryOrderId);
}
