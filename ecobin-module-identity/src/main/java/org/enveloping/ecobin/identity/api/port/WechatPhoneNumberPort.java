package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.result.WechatPhoneNumber;

public interface WechatPhoneNumberPort {

    WechatPhoneNumber exchangePhoneNumber(
            String appid,
            String appSecret,
            String phoneCode);
}
