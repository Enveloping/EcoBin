package org.enveloping.ecobin.identity.api.port;

/** Generates a WeChat mini-program code for a server-selected AppID. */
public interface WechatMiniProgramCodePort {

    byte[] generate(
            String appId,
            String appSecret,
            String page,
            String scene);
}
