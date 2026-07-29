package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.result.LockedDeliveryOrganizationUser;
import org.enveloping.ecobin.identity.api.result.LockedMiniappDeliveryScope;

/**
 * 开始投递复合事务中的 identity 同步参与端口。
 *
 * <p>调用方必须先锁定小程序作用域，在取得 recycling 配置 head 锁后，
 * 再锁定机构用户和当前登录会话。</p>
 */
public interface StartDeliveryIdentityParticipationPort {

    LockedMiniappDeliveryScope lockCurrentMiniappScope();

    LockedDeliveryOrganizationUser lockCurrentOrganizationUser(
            LockedMiniappDeliveryScope lockedScope);
}
