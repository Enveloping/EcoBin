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

    /**
     * 以设备上报的二维码应用结果收口一条平台级同步任务。实现方必须再次核对原命令、
     * 设备和 URL 摘要；APPLIED 才完成，FAILED 必须保留为可见阻断，不能把 OneNet
     * 送达冒充为串口屏显示。
     */
    default void applyDeviceEntryUrlApplicationResult(
            UUID commandUid,
            String hardwareSn,
            String deviceEntryUrlSha256,
            String status,
            String faultCode) {
        throw new UnsupportedOperationException(
                "device entry URL application proof is not implemented");
    }
}
