package org.enveloping.ecobin.funds.api.port;

import java.util.UUID;

/** 在资金短事务提交后、真正调用外部渠道前记录“调用可能已开始”。 */
public interface ReliableFundsAttemptBoundaryPort {

    /**
     * 同一可靠任务截至当前尝试是否已经越过外调边界。
     *
     * <p>一旦越过该边界，即使尝试没有留下渠道响应，也只能查询原单，不能再次提交
     * 具有副作用的创建请求；这也覆盖当前尝试本身被重复进入的情况。</p>
     */
    boolean taskExternalCallMayHaveStarted(
            UUID taskUid,
            UUID currentAttemptUid);

    void markExternalCallMayHaveStarted(UUID attemptUid);
}
