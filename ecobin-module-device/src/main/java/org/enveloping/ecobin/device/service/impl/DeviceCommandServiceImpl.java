package org.enveloping.ecobin.device.service.impl;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.enveloping.ecobin.device.api.port.DeviceCommandGateway;
import org.enveloping.ecobin.device.service.DeviceCommandService;
import org.springframework.stereotype.Service;

/**
 * 设备下行指令服务实现。
 * <p>
 * 开投口 / 开清运门经 device 定义的外部网关下发；OneNet 协议实现位于 integration。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class DeviceCommandServiceImpl implements DeviceCommandService {

    private final DeviceCommandGateway deviceCommandGateway;

    @Override
    public void sendOpenDoor(String deviceSn, Integer doorIndex) {
        // 经 OneNet 下发 openDeliveryDoor（仅含 COS 凭证）；分类不随开门下发，由后端建单时按投口配置兜底（物模型 §3.1）
        deviceCommandGateway.openDeliveryDoor(deviceSn, doorIndex);
    }

    @Override
    public void sendOpenCleanDoor(String deviceSn, Integer doorIndex, Long cleanOrderId) {
        // 经 OneNet 下发；凭证未配置时 OneNetClient 内部记占位日志，不阻塞开清运门主流程
        deviceCommandGateway.openCleanDoor(deviceSn, doorIndex, cleanOrderId);
    }
}
