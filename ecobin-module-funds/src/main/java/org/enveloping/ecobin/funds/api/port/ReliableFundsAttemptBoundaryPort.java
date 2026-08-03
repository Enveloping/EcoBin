package org.enveloping.ecobin.funds.api.port;

import java.util.UUID;

/** 在资金短事务提交后、真正调用外部渠道前记录“调用可能已开始”。 */
public interface ReliableFundsAttemptBoundaryPort {

    void markExternalCallMayHaveStarted(UUID attemptUid);
}
