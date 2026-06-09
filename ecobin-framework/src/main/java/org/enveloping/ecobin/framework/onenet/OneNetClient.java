package org.enveloping.ecobin.framework.onenet;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

/**
 * 中国移动 OneNet 物联网平台客户端（设备命令下行）。
 * <p>
 * 凭证未到位前为占位实现：仅记录指令意图，不发起真实请求，不阻塞业务主流程。
 * 待 {@link OneNetProperties} 配置齐全后，在 {@code TODO} 处补充真实的 OneNet 命令下发调用。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class OneNetClient {

    private final OneNetProperties properties;
    @SuppressWarnings("unused") // 预留：真实下发时用于调用 OneNet API
    private final RestTemplate restTemplate;

    /**
     * 下发「开清运门」指令。
     *
     * @param devSn     设备序列号
     * @param doorIndex 投口号
     * @param bagNo     本次清运绑定的新垃圾袋编号
     */
    public void openCleanDoor(String devSn, Integer doorIndex, String bagNo) {
        if (!properties.isConfigured()) {
            log.info("[OneNet·占位] 开清运门 devSn={}, doorIndex={}, bagNo={}（凭证未配置，跳过真实下发）", devSn, doorIndex, bagNo);
            return;
        }
        // TODO 凭证到位后接入 OneNet 命令下发：用 properties.baseUrl/productId/accessKey + restTemplate 调用 OneNet API
        log.info("[OneNet] 开清运门 devSn={}, doorIndex={}, bagNo={}", devSn, doorIndex, bagNo);
    }
}
