package org.enveloping.ecobin.funds.infrastructure.legacy;

import org.enveloping.ecobin.identity.api.command.OrganizationUserRegistrationCommand;
import org.enveloping.ecobin.identity.api.port.OrganizationUserRegistrationParticipant;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/**
 * F-03/F-06 前的过渡实现。
 *
 * <p>旧库把零余额钱包内嵌在刚插入的 {@code sys_user} 行，并由数据库默认值建立。本实现
 * 消费当次 FK 引用以证明参与边界；目标钱包表落地后必须替换为 funds 自有持久化。</p>
 */
@Component
public class LegacyEmbeddedWalletRegistrationParticipant
        implements OrganizationUserRegistrationParticipant {

    @Override
    @Transactional(propagation = Propagation.REQUIRED)
    public void initializeWallet(OrganizationUserRegistrationCommand command) {
        command.walletOwnerRef().writeForeignKeyTo((tenantKey, organizationKey, userKey) -> {
            if (tenantKey != organizationKey) {
                throw new IllegalStateException("legacy tenant and organization keys must match");
            }
            if (userKey <= 0) {
                throw new IllegalStateException("organization user must already be persisted");
            }
        });
    }
}
