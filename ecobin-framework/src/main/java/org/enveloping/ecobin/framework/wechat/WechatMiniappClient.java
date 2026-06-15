package org.enveloping.ecobin.framework.wechat;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;
import tools.jackson.databind.ObjectMapper;

/**
 * 微信小程序 API 客户端
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class WechatMiniappClient {

    private final WechatConfig wechatConfig;
    private final RestTemplate restTemplate;

    /**
     * Spring 注入的 Jackson 3 ObjectMapper，用于手动解析微信 code2session 响应。
     * 微信该接口返回 JSON 但 Content-Type 为 {@code text/plain}，无法走 RestTemplate
     * 按 content-type 匹配的消息转换器，故以 String 取回后用此 mapper 解析。
     * Jackson 3 默认不对未知字段（如 session_key）报错。
     */
    private final ObjectMapper objectMapper;

    private static final String CODE2SESSION_URL =
            "https://api.weixin.qq.com/sns/jscode2session?appid={appid}&secret={secret}&js_code={code}&grant_type=authorization_code";

    /**
     * 用临时 code 换取 session_key 和 openid（使用全局默认 appid/secret）。
     *
     * @param code 前端 wx.login() 获取的临时凭证
     * @return 微信会话响应
     */
    public WechatSessionResponse code2session(String code) {
        return code2session(wechatConfig.getAppid(), wechatConfig.getSecret(), code);
    }

    /**
     * 用临时 code 换取 session_key 和 openid（多租户：使用指定租户的 appid/secret）。
     *
     * @param appid  租户小程序 AppID
     * @param secret 租户小程序 Secret（已解密的明文）
     * @param code   前端 wx.login() 获取的临时凭证
     * @return 微信会话响应
     */
    public WechatSessionResponse code2session(String appid, String secret, String code) {
        log.debug("微信 code2session 请求, appid={}, code={}", appid, code);

        // 微信返回 JSON 但 Content-Type=text/plain，按 String 取回再手动解析，规避转换器按类型匹配的限制
        String body = restTemplate.getForObject(CODE2SESSION_URL, String.class, appid, secret, code);
        if (body == null || body.isBlank()) {
            log.error("微信 code2session 返回为空");
            throw new RuntimeException("微信登录失败: 响应为空");
        }

        WechatSessionResponse response;
        try {
            response = objectMapper.readValue(body, WechatSessionResponse.class);
        } catch (Exception e) {
            log.error("微信 code2session 响应解析失败, body={}", body, e);
            throw new RuntimeException("微信登录失败: 响应解析失败");
        }

        if (!response.isSuccess()) {
            log.error("微信 code2session 失败, errcode={}, errmsg={}", response.getErrcode(), response.getErrmsg());
            throw new RuntimeException("微信登录失败: " + response.getErrmsg());
        }

        log.debug("微信 code2session 成功, openid={}", response.getOpenid());
        return response;
    }
}
