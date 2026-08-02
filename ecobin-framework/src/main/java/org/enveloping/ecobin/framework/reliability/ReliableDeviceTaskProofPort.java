package org.enveloping.ecobin.framework.reliability;

import java.util.UUID;

/**
 * Mutation Seam used by a trusted device fact to terminate its reliable
 * command task in the same authoritative business transaction.
 */
public interface ReliableDeviceTaskProofPort {

    void completeFromTrustedProof(
            String taskType,
            String targetType,
            String targetStableKey);

    /**
     * 设备已经可靠报告某条命令的任一规范受理或终止阶段后，结束该命令对应的云端发送
     * 职责。后续业务完成仍由更强的完成事实推进。
     */
    void completeDispatchFromTrustedCommandObservation(UUID commandUid);
}
