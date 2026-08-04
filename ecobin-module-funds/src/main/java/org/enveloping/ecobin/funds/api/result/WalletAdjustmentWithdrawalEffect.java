package org.enveloping.ecobin.funds.api.result;

public enum WalletAdjustmentWithdrawalEffect {
    NONE,
    PAUSED_BEFORE_CHANNEL,
    RISK_MARKED_AFTER_CHANNEL,
    RESUMED_BEFORE_CHANNEL,
    PAUSE_CLEARED_TASK_STILL_WAITING,
    PAUSE_CLEARED_TASK_NOT_WAKEABLE
}
