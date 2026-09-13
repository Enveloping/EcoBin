package org.enveloping.ecobin.recycling.application.clean;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;

import java.util.Collection;
import java.util.List;

/**
 * 清运选项查询和清运创建共同使用的稳定阻断原因。
 */
enum CleanReadinessBlocker {

    CLEAN_CONFIGURATION_UNAVAILABLE(
            422,
            "CLEAN.CONFIGURATION_UNAVAILABLE",
            "机构还没有可用于清运的当前规则"),
    CONFIGURATION_NOT_APPLIED(
            422,
            "DEVICE.CONFIGURATION_NOT_APPLIED",
            "设备当前配置尚未完整应用"),
    EDGE_OFFLINE(
            422,
            "DEVICE.OFFLINE",
            "OneNet 当前未确认设备在线，不能创建清运任务"),
    DEVICE_SOFTWARE_NOT_ACCEPTING(
            422,
            "DEVICE.SOFTWARE_NOT_ACCEPTING",
            "设备正在维护，或软件状态尚未确认，暂时不能创建新的清运任务"),
    DEVICE_BUSY(
            409,
            "DEVICE.DEVICE_BUSY",
            "设备正在执行其他物理操作"),
    PORT_DISABLED(
            422,
            "DEVICE.CLEANING_UNAVAILABLE",
            "当前投口未启用清运业务"),
    CLEAN_OPERATION_ACTIVE(
            409,
            "CLEAN.PORT_OPERATION_ACTIVE",
            "当前投口已有未结束清运操作"),
    CLEAN_BAG_RECOVERY_REQUIRED(
            409,
            "CLEAN.BAG_RECOVERY_REQUIRED",
            "上次清运中断后尚未确认设备内实际袋，需人工处理后才能开始新业务"),
    PORT_WORK_ACTIVE(
            409,
            "DEVICE.PORT_WORK_ACTIVE",
            "当前投口正在执行检测或基准重测，请稍后重试");

    private final int status;
    private final String problemCode;
    private final String message;

    CleanReadinessBlocker(
            int status,
            String problemCode,
            String message) {
        this.status = status;
        this.problemCode = problemCode;
        this.message = message;
    }

    TargetApiException problem() {
        return new TargetApiException(status, problemCode, message);
    }

    static List<String> codes(
            Collection<CleanReadinessBlocker> blockers) {
        return blockers.stream().map(Enum::name).toList();
    }
}
