package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.result.WechatSession;

/**
 * 微信小程序登录所需的外部会话交换能力。
 *
 * <p>identity 定义业务所需结果，第三方协议与 SDK 由 integration 适配。</p>
 */
public interface WechatSessionPort {

    WechatSession exchange(String appid, String secret, String code);
}
