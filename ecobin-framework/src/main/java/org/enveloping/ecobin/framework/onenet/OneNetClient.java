package org.enveloping.ecobin.framework.onenet;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.enveloping.ecobin.framework.cos.CosStsCredential;
import org.enveloping.ecobin.framework.cos.CosTokenClient;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 中国移动 OneNet 物联网平台客户端（设备命令下行 = 物模型服务调用）。
 * <p>
 * 凭证（productId + accessKey）未到位前为占位实现：仅记录指令意图、不发起真实请求、不阻塞业务主流程。
 * 凭证齐全（{@link OneNetProperties#isConfigured()}）后才按标准 OneNET token 鉴权调用「设备服务调用」API。
 * <p>
 * ⚠ 下行链路<strong>本地不测试</strong>（无设备、无下发凭证），鉴权/端点细节联调时校验。
 *
 * @see OneNetTokenGenerator token 算法
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class OneNetClient {

    private final OneNetProperties properties;
    private final RestTemplate restTemplate;
    private final CosTokenClient cosTokenClient;

    /** OneNet 字符串字段上限 512，STS sessionToken 实测约 640，需拆段下发（物模型 §3.4）。 */
    private static final int ONENET_STRING_MAX = 512;

    /**
     * 下发「开投递投口」指令（物模型服务 {@code openDeliveryDoor}），COS 临时密钥搭车下发。
     * <p>
     * 投递为「上传后建单」：照片位置由<strong>设备</strong>决定（设备自生成 token、自定对象 key 直传），
     * 故本命令<strong>只下发凭证</strong>，不下发照片 key。
     *
     * @param devSn      设备序列号
     * @param doorIndex  投口号
     * @param wasteType1 一级分类
     * @param wasteType2 二级分类
     */
    public void openDeliveryDoor(String devSn, Integer doorIndex,
                                 Integer wasteType1, Integer wasteType2) {
        Map<String, Object> input = new LinkedHashMap<>();
        input.put("doorIndex", doorIndex);
        // 物模型 openDeliveryDoor 的入参均为必填，OneNet 服务调用会校验"required value"。
        // 分类未指定时下发 0（= 缺省/不区分，设备侧仍按投口配置兜底），避免传 null 触发 10415。
        input.put("wasteType1", wasteType1 != null ? wasteType1 : 0);
        input.put("wasteType2", wasteType2 != null ? wasteType2 : 0);
        input.put("cosToken", baseCosToken(devSn, doorIndex));
        invokeService(devSn, "openDeliveryDoor", input);
    }

    /**
     * 下发「开清运门」指令（物模型服务 {@code openCleanDoor}），COS 临时密钥搭车下发。
     * <p>
     * 清运「开门即建单」：照片 key 由后端按 {@code {sn}/{doorIndex}/{cleanOrderId}/<slot>.jpg} 确定性生成并
     * 开门即预存订单 URL；本命令<strong>不下发照片 key</strong>，设备据下发的 {@code cleanOrderId} 自行按同一约定拼 key 直传。
     *
     * @param devSn        设备序列号
     * @param doorIndex    投口号（物理控制）
     * @param cleanOrderId 清运订单ID（设备 {@code cleanGross}/{@code cleanTare} 原样带回；并据此自拼照片 key）
     */
    public void openCleanDoor(String devSn, Integer doorIndex, Long cleanOrderId) {
        Map<String, Object> input = new LinkedHashMap<>();
        input.put("doorIndex", doorIndex);
        input.put("cleanOrderId", cleanOrderId);
        input.put("cosToken", baseCosToken(devSn, doorIndex));
        invokeService(devSn, "openCleanDoor", input);
    }

    /**
     * 调用 OneNet「设备服务调用」API（async）。凭证缺失时走占位日志。
     */
    private void invokeService(String devSn, String identifier, Map<String, Object> input) {
        if (!properties.isConfigured()) {
            log.info("[OneNet·占位] 服务调用 {} devSn={}, input={}（凭证未配置，跳过真实下发）", identifier, devSn, input);
            return;
        }
        try {
            Map<String, Object> body = new LinkedHashMap<>();
            body.put("product_id", properties.getProductId());
            body.put("device_name", devSn);
            body.put("identifier", identifier);
            body.put("params", input);

            String token = OneNetTokenGenerator.generate(
                    properties.getVersion(),
                    "products/" + properties.getProductId(),
                    properties.getAccessKey(),
                    properties.getTokenTtlSeconds());

            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);
            headers.set(HttpHeaders.AUTHORIZATION, token);

            String url = properties.getBaseUrl() + properties.getInvokeServicePath();
            String resp = restTemplate.postForObject(url, new HttpEntity<>(body, headers), String.class);
            log.info("[OneNet] 服务调用 {} devSn={} 已下发，resp={}", identifier, devSn, resp);
        } catch (Exception e) {
            // 下发失败不阻塞主流程（开门建单等已完成），仅记录
            log.error("[OneNet] 服务调用 {} devSn={} 下发失败", identifier, devSn, e);
        }
    }

    /**
     * 取 COS 临时密钥组装为<strong>仅凭证</strong>的 {@code cosToken} 结构（投递/清运通用，物模型 §3.4）。
     * <p>
     * 照片 key 不下发：投递由设备自定位置、清运由设备据 {@code cleanOrderId} 按约定自拼。
     * {@code sessionToken} 实测约 640 > OneNet 512 上限，按 512 拆 {@code sessionToken1/2}，固件按序拼接还原。
     */
    private Map<String, Object> baseCosToken(String devSn, Integer doorIndex) {
        CosStsCredential cred = cosTokenClient.getTempCredentials(devSn, doorIndex);
        String sessionToken = cred.getSessionToken() == null ? "" : cred.getSessionToken();
        String part1 = sessionToken.length() > ONENET_STRING_MAX ? sessionToken.substring(0, ONENET_STRING_MAX) : sessionToken;
        String part2 = sessionToken.length() > ONENET_STRING_MAX ? sessionToken.substring(ONENET_STRING_MAX) : "";

        Map<String, Object> cosToken = new LinkedHashMap<>();
        cosToken.put("tmpSecretId", cred.getTmpSecretId());
        cosToken.put("tmpSecretKey", cred.getTmpSecretKey());
        cosToken.put("sessionToken1", part1);
        cosToken.put("sessionToken2", part2);
        cosToken.put("bucket", cred.getBucket());
        cosToken.put("region", cred.getRegion());
        cosToken.put("baseUrl", cred.getBaseUrl());
        return cosToken;
    }
}
