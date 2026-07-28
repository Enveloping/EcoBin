package org.enveloping.ecobin.identity.application.miniapp;

import org.enveloping.ecobin.identity.api.error.WechatExchangeException;
import org.enveloping.ecobin.identity.api.port.WechatPhoneNumberPort;
import org.enveloping.ecobin.identity.api.result.WechatPhoneNumber;
import org.enveloping.ecobin.identity.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.web.v1.miniapp.MiniappModels.PhoneBindingResult;
import org.springframework.stereotype.Service;

import java.util.Map;
import java.util.UUID;
import java.util.regex.Pattern;

@Service
public class TargetMiniappPhoneBindingService {

    private static final Pattern E164 = Pattern.compile("^\\+[1-9][0-9]{1,14}$");

    private final WechatPhoneNumberPort wechatPhoneNumberPort;
    private final TargetMiniappPhoneBindingTransactionService transactionService;

    public TargetMiniappPhoneBindingService(
            WechatPhoneNumberPort wechatPhoneNumberPort,
            TargetMiniappPhoneBindingTransactionService transactionService) {
        this.wechatPhoneNumberPort = wechatPhoneNumberPort;
        this.transactionService = transactionService;
    }

    public PhoneBindingResult bind(
            UUID operationUid,
            TargetMiniappActor actor,
            String wechatPhoneCode) {
        WechatPhoneNumber phone;
        try {
            phone = wechatPhoneNumberPort
                    .exchangePhoneNumberByCredentialReference(
                            actor.appId(),
                            actor.secretReference(),
                            wechatPhoneCode);
        } catch (WechatExchangeException exception) {
            if (exception.reason()
                    == WechatExchangeException.Reason.INVALID_CODE) {
                throw new TargetApiException(
                        422,
                        "WECHAT.PHONE_CODE_INVALID",
                        "微信手机号动态码无效，请重新授权");
            }
            throw new TargetApiException(
                    503,
                    "WECHAT.PHONE_SERVICE_UNAVAILABLE",
                    "微信手机号服务暂不可用，请稍后重试",
                    true,
                    Map.of());
        }
        return transactionService.bind(
                operationUid, actor, normalize(phone));
    }

    private static String normalize(WechatPhoneNumber phone) {
        String number = phone.phoneNumber().trim()
                .replace(" ", "")
                .replace("-", "");
        if (!number.startsWith("+")) {
            String countryCode = phone.countryCode() == null
                    ? "" : phone.countryCode().trim().replace("+", "");
            if (!countryCode.matches("^[1-9][0-9]{0,2}$")
                    || !number.matches("^[0-9]{2,15}$")) {
                throw invalidPhone();
            }
            number = "+" + countryCode + number;
        }
        if (!E164.matcher(number).matches()) {
            throw invalidPhone();
        }
        return number;
    }

    private static TargetApiException invalidPhone() {
        return new TargetApiException(
                422,
                "WECHAT.PHONE_NUMBER_INVALID",
                "微信返回的手机号格式无法识别");
    }
}
