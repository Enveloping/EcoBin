package org.enveloping.ecobin.device.service.impl;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.enveloping.ecobin.device.service.DeviceCommandService;
import org.enveloping.ecobin.framework.onenet.OneNetClient;
import org.springframework.stereotype.Service;

/**
 * 设备下行指令服务实现。
 * <p>
 * 开投口 / 开清运门均经 {@link OneNetClient} 走 OneNet 物模型服务调用下发；下发凭证未到位时
 * {@link OneNetClient} 内部记占位日志、不阻塞主流程。下行链路本地不测试，联调时校验。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class DeviceCommandServiceImpl implements DeviceCommandService {

    private final OneNetClient oneNetClient;

    @Override
    public void sendOpenDoor(String deviceSn, Integer doorIndex, String deliveryToken) {
        // 经 OneNet 下发 openDeliveryDoor；分类缺省（null）由设备按投口配置兜底（物模型 §3.1）
        oneNetClient.openDeliveryDoor(deviceSn, doorIndex, deliveryToken, null, null);
    }

    @Override
    public void sendOpenCleanDoor(String deviceSn, Integer doorIndex, Long cleanOrderId) {
        // 经 OneNet 下发；凭证未配置时 OneNetClient 内部记占位日志，不阻塞开清运门主流程
        oneNetClient.openCleanDoor(deviceSn, doorIndex, cleanOrderId);
    }
}
