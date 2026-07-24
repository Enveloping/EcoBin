package org.enveloping.ecobin.identity.api.result;

/**
 * 微信登录成功后 identity 实际需要的规范化结果。
 */
public record WechatSession(String openid, String unionid) {
}
