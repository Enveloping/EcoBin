package org.enveloping.ecobin.device.service.impl;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.enveloping.ecobin.device.service.DeviceCommandService;
import org.enveloping.ecobin.framework.onenet.OneNetClient;
import org.springframework.stereotype.Service;

/**
 * 设备下行指令服务实现。
 * <p>
 * 清运门下发已接入 {@link OneNetClient}（凭证未到位时为占位日志）；投口下发暂仍为占位，
 * 待 OneNet 凭证到位后一并切换为真实下发。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class DeviceCommandServiceImpl implements DeviceCommandService {

    private final OneNetClient oneNetClient;

    @Override
    public void sendOpenDoor(String deviceSn, Integer doorIndex, String deliveryToken) {
        // 占位：当前无下行链路，仅记录意图，不影响开投口主流程
        log.info("[设备指令·占位] 开投口 sn={}, doorIndex={}, deliveryToken={}", deviceSn, doorIndex, deliveryToken);
    }

    @Override
    public void sendOpenCleanDoor(String deviceSn, Integer doorIndex, String bagNo) {
        // 经 OneNet 下发；凭证未配置时 OneNetClient 内部记占位日志，不阻塞开清运门主流程
        oneNetClient.openCleanDoor(deviceSn, doorIndex, bagNo);
    }
}
