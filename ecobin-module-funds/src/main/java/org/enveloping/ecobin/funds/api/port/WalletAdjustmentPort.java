package org.enveloping.ecobin.funds.api.port;

import org.enveloping.ecobin.funds.api.command.AdjustWalletCommand;
import org.enveloping.ecobin.funds.api.result.WalletAdjustmentResult;

public interface WalletAdjustmentPort {

    WalletAdjustmentResult adjust(AdjustWalletCommand command);
}
