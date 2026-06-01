package org.enveloping.ecobin.framework.wechat;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpMethod;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

/**
 * 微信小程序 API 客户端
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class WechatMiniappClient {

    private final WechatConfig wechatConfig;
    private final RestTemplate restTemplate;

    private static final String CODE2SESSION_URL =
            "https://api.weixin.qq.com/sns/jscode2session?appid={appid}&secret={secret}&js_code={code}&grant_type=authorization_code";

    /**
     * 用临时 code 换取 session_key 和 openid
     *
     * @param code 前端 wx.login() 获取的临时凭证
     * @return 微信会话响应
     */
    public WechatSessionResponse code2session(String code) {
        log.debug("微信 code2session 请求, code={}", code);

        WechatSessionResponse response = restTemplate.exchange(
                CODE2SESSION_URL,
                HttpMethod.GET,
                null,
                WechatSessionResponse.class,
                wechatConfig.getAppid(),
                wechatConfig.getSecret(),
                code
        ).getBody();

        if (response == null) {
            log.error("微信 code2session 返回为空");
            throw new RuntimeException("微信登录失败: 响应为空");
        }

        if (!response.isSuccess()) {
            log.error("微信 code2session 失败, errcode={}, errmsg={}", response.getErrcode(), response.getErrmsg());
            throw new RuntimeException("微信登录失败: " + response.getErrmsg());
        }

        log.debug("微信 code2session 成功, openid={}", response.getOpenid());
        return response;
    }
}
