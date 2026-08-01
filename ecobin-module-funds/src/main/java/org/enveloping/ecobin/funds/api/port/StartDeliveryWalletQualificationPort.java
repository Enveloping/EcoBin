package org.enveloping.ecobin.funds.api.port;

import org.enveloping.ecobin.funds.api.command.StartDeliveryWalletQualificationCommand;
import org.enveloping.ecobin.funds.api.result.QualifiedDeliveryWallet;

/**
 * 开始投递复合事务中的 funds 同步参与端口。
 */
public interface StartDeliveryWalletQualificationPort {

    QualifiedDeliveryWallet lockAndRequireEligible(
            StartDeliveryWalletQualificationCommand command);
}
