package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.result.WechatPhoneNumber;

public interface WechatPhoneNumberPort {

    WechatPhoneNumber exchangePhoneNumberByCredentialReference(
            String appid,
            String secretReference,
            String phoneCode);
}
