package org.enveloping.ecobin.funds.api.result;

/**
 * 本次钱包变动对活动提现产生的效果。
 */
public enum WithdrawalBalanceEffect {
    UNCHANGED,
    PAUSED_BEFORE_CHANNEL,
    RISK_MARKED_AFTER_CHANNEL,
    PAUSE_CLEARED
}
