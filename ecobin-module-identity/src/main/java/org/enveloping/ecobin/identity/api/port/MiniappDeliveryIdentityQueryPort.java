package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.result.CurrentMiniappDeliveryIdentity;

/**
 * GET 投递选项、会话和当前用户投递订单查询使用的小程序身份快照。
 *
 * <p>该端口加入调用方已有的只读事务，但不加数据库锁。它不能用于授权开始
 * 新投递；POST 开始投递仍必须调用
 * {@link StartDeliveryIdentityParticipationPort} 进行事务内实时复核。</p>
 */
public interface MiniappDeliveryIdentityQueryPort {

    CurrentMiniappDeliveryIdentity current();
}
