package org.enveloping.ecobin.funds.api.port;

import java.time.LocalDateTime;
import java.util.UUID;

/**
 * funds 提交领域事实后，同事务刷新 operations 告警与可靠任务的窄边界。
 */
public interface FundsOperationalControlPort {

    void observePayoutLiquidityPause(
            long merchantProfileId,
            UUID pausedEventUid,
            LocalDateTime observedAt);

    void resolvePayoutLiquidityPause(
            long merchantProfileId,
            UUID pausedEventUid,
            LocalDateTime resolvedAt);

    int wakePayoutTasks(LocalDateTime wakeAt);
}
