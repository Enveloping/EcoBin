package org.enveloping.ecobin.device.application.legacy;

import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.device.api.legacy.LegacyDeviceAccessPort;
import org.enveloping.ecobin.device.api.legacy.LegacyDeviceId;
import org.enveloping.ecobin.device.api.legacy.LegacyDeviceSessionSnapshot;
import org.enveloping.ecobin.device.api.legacy.LegacyDeviceSnapshot;
import org.enveloping.ecobin.device.api.legacy.LegacyDoorId;
import org.enveloping.ecobin.device.api.legacy.LegacyDoorSnapshot;
import org.enveloping.ecobin.device.entity.Device;
import org.enveloping.ecobin.device.entity.DeviceSession;
import org.enveloping.ecobin.device.entity.Door;
import org.enveloping.ecobin.device.service.DeviceCommandService;
import org.enveloping.ecobin.device.service.DeviceService;
import org.enveloping.ecobin.device.service.DeviceSessionService;
import org.enveloping.ecobin.device.service.DoorService;
import org.springframework.stereotype.Component;

/**
 * 将 device 私有 Entity/Service 映射为 F-03 的窄公开快照。
 */
@Component
@RequiredArgsConstructor
public class LegacyDeviceAccessAdapter implements LegacyDeviceAccessPort {

    private final DeviceService deviceService;
    private final DoorService doorService;
    private final DeviceSessionService deviceSessionService;
    private final DeviceCommandService deviceCommandService;

    @Override
    public LegacyDoorSnapshot findDoor(LegacyDoorId doorId) {
        return snapshot(doorService.getById(doorId.value()));
    }

    @Override
    public LegacyDoorSnapshot findDoor(LegacyDeviceId deviceId, Integer doorIndex) {
        Door door = doorService.lambdaQuery()
                .eq(Door::getDeviceId, deviceId.value())
                .eq(Door::getDoorIndex, doorIndex)
                .one();
        return snapshot(door);
    }

    @Override
    public LegacyDeviceSnapshot findDevice(LegacyDeviceId deviceId) {
        return snapshot(deviceService.getById(deviceId.value()));
    }

    @Override
    public LegacyDeviceSnapshot findDeviceBySn(String sn) {
        return snapshot(deviceService.lambdaQuery().eq(Device::getSn, sn).one());
    }

    @Override
    public LegacyDeviceSessionSnapshot findActiveSession(LegacyDeviceId deviceId) {
        DeviceSession session = deviceSessionService.findActive(deviceId.value());
        return session == null ? null : new LegacyDeviceSessionSnapshot(
                session.getTenantId(), session.getUserId(), session.getLoginType());
    }

    @Override
    public void activateSession(
            LegacyDeviceId deviceId,
            Long tenantId,
            Long userId,
            Integer loginType) {
        deviceSessionService.activate(deviceId.value(), tenantId, userId, loginType);
    }

    @Override
    public void refreshSession(LegacyDeviceId deviceId) {
        deviceSessionService.refresh(deviceId.value());
    }

    @Override
    public void openDeliveryDoor(String deviceSn, Integer doorIndex) {
        deviceCommandService.sendOpenDoor(deviceSn, doorIndex);
    }

    @Override
    public void openCleanDoor(String deviceSn, Integer doorIndex, Long cleanOrderId) {
        deviceCommandService.sendOpenCleanDoor(deviceSn, doorIndex, cleanOrderId);
    }

    private static LegacyDeviceSnapshot snapshot(Device device) {
        return device == null ? null : new LegacyDeviceSnapshot(
                new LegacyDeviceId(device.getId()),
                device.getTenantId(),
                device.getSn(),
                device.getName());
    }

    private static LegacyDoorSnapshot snapshot(Door door) {
        return door == null ? null : new LegacyDoorSnapshot(
                new LegacyDoorId(door.getId()),
                new LegacyDeviceId(door.getDeviceId()),
                door.getTenantId(),
                door.getDoorIndex(),
                door.getWasteType1(),
                door.getWasteType2(),
                door.getPrice(),
                door.getEnabled());
    }
}
