package org.enveloping.ecobin.funds.api.port;

import org.enveloping.ecobin.funds.api.command.DeliveryWalletQualificationQuery;
import org.enveloping.ecobin.funds.api.result.DeliveryWalletQualification;

/**
 * GET 投递选项和会话展示使用的钱包资格查询端口。
 *
 * <p>该快照不授权开门；POST 开始投递必须使用
 * {@link StartDeliveryWalletQualificationPort} 的加锁复核。</p>
 */
public interface DeliveryWalletQualificationQueryPort {

    DeliveryWalletQualification current(
            DeliveryWalletQualificationQuery query);
}
