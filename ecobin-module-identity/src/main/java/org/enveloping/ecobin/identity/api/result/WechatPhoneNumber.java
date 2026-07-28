package org.enveloping.ecobin.identity.api.result;

public record WechatPhoneNumber(
        String phoneNumber,
        String countryCode) {

    public WechatPhoneNumber {
        if (phoneNumber == null || phoneNumber.isBlank()) {
            throw new IllegalArgumentException(
                    "phoneNumber must not be blank");
        }
    }
}
