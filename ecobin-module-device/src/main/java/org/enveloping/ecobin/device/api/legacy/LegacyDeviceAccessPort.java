package org.enveloping.ecobin.device.api.legacy;

/**
 * F-03 为旧 recycling 流程提供的窄同步设备端口。
 * 目标纵向切片接管后由正式 UID/命令端口替换。
 */
public interface LegacyDeviceAccessPort {

    LegacyDoorSnapshot findDoor(LegacyDoorId doorId);

    LegacyDoorSnapshot findDoor(LegacyDeviceId deviceId, Integer doorIndex);

    LegacyDeviceSnapshot findDevice(LegacyDeviceId deviceId);

    LegacyDeviceSnapshot findDeviceBySn(String sn);

    LegacyDeviceSessionSnapshot findActiveSession(LegacyDeviceId deviceId);

    void activateSession(
            LegacyDeviceId deviceId,
            Long tenantId,
            Long userId,
            Integer loginType);

    void refreshSession(LegacyDeviceId deviceId);

    void openDeliveryDoor(String deviceSn, Integer doorIndex);

    void openCleanDoor(String deviceSn, Integer doorIndex, Long cleanOrderId);
}
