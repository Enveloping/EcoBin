package org.enveloping.ecobin.business.dto;

import lombok.Data;
import org.enveloping.ecobin.identity.api.legacy.LegacyOrganizationUserSnapshot;

import java.math.BigDecimal;

/**
 * C 端钱包视图：仅暴露余额信息，不带出 openid 等用户敏感字段。
 */
@Data
public class WalletVO {

    /** 可用余额 */
    private BigDecimal balance;

    /** 待审核余额（提现申请中冻结） */
    private BigDecimal pendingBalance;

    public static WalletVO from(LegacyOrganizationUserSnapshot user) {
        WalletVO vo = new WalletVO();
        vo.setBalance(user.balance() != null ? user.balance() : BigDecimal.ZERO);
        vo.setPendingBalance(user.pendingBalance() != null ? user.pendingBalance() : BigDecimal.ZERO);
        return vo;
    }
}
