package org.enveloping.ecobin.funds.api.port;

import org.enveloping.ecobin.funds.api.command.ApplyDeliveryRevisionDeltaCommand;
import org.enveloping.ecobin.funds.api.result.AppliedDeliveryWalletDelta;

/**
 * 参加 recycling 投递审核或纠错事务的钱包差额端口。
 */
public interface ApplyDeliveryRevisionDeltaPort {

    AppliedDeliveryWalletDelta applyDeliveryRevisionDelta(
            ApplyDeliveryRevisionDeltaCommand command);
}
