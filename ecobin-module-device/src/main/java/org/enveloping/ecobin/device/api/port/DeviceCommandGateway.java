package org.enveloping.ecobin.device.api.port;

/**
 * device 模块需要的设备下行能力。
 */
public interface DeviceCommandGateway {

    void openDeliveryDoor(String deviceSn, Integer doorIndex);

    void openCleanDoor(String deviceSn, Integer doorIndex, Long cleanOrderId);
}
