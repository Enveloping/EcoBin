package org.enveloping.ecobin.device.service.impl;

import lombok.extern.slf4j.Slf4j;
import org.enveloping.ecobin.device.service.DeviceCommandService;
import org.springframework.stereotype.Service;

/**
 * 设备下行指令服务占位实现。
 * <p>
 * TODO 对接设备下行通道（MQTT/长连接/网关回调）后替换为真实下发。
 */
@Slf4j
@Service
public class DeviceCommandServiceImpl implements DeviceCommandService {

    @Override
    public void sendOpenDoor(String deviceSn, Integer doorIndex, String deliveryToken) {
        // 占位：当前无下行链路，仅记录意图，不影响开投口主流程
        log.info("[设备指令·占位] 开投口 sn={}, doorIndex={}, deliveryToken={}", deviceSn, doorIndex, deliveryToken);
    }
}
