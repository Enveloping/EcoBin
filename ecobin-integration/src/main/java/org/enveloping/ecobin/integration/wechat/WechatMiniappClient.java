package org.enveloping.ecobin.integration.wechat;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.enveloping.ecobin.identity.api.error.WechatExchangeException;
import org.enveloping.ecobin.identity.api.port.WechatPhoneNumberPort;
import org.enveloping.ecobin.identity.api.port.WechatSessionPort;
import org.enveloping.ecobin.identity.api.result.WechatPhoneNumber;
import org.enveloping.ecobin.identity.api.result.WechatSession;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.web.client.RestClientException;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.JsonNode;

import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
/**
 * 微信小程序 API 客户端
 */
@Slf4j
@Component
@ConditionalOnProperty(
        prefix = "ecobin.external",
        name = "mode",
        havingValue = "real")
@RequiredArgsConstructor
public class WechatMiniappClient
        implements WechatSessionPort, WechatPhoneNumberPort {

    private final WechatConfig wechatConfig;
    private final RestTemplate restTemplate;
    private final MiniappSecretResolver secretResolver;

    /**
     * Spring 注入的 Jackson 3 ObjectMapper，用于手动解析微信 code2session 响应。
     * 微信该接口返回 JSON 但 Content-Type 为 {@code text/plain}，无法走 RestTemplate
     * 按 content-type 匹配的消息转换器，故以 String 取回后用此 mapper 解析。
     * Jackson 3 默认不对未知字段（如 session_key）报错。
     */
    private final ObjectMapper objectMapper;

    private static final String CODE2SESSION_URL =
            "https://api.weixin.qq.com/sns/jscode2session?appid={appid}&secret={secret}&js_code={code}&grant_type=authorization_code";
    private static final String STABLE_TOKEN_URL =
            "https://api.weixin.qq.com/cgi-bin/stable_token";
    private static final String PHONE_NUMBER_URL =
            "https://api.weixin.qq.com/wxa/business/getuserphonenumber?access_token={accessToken}";

    private final Map<String, AccessToken> accessTokens =
            new ConcurrentHashMap<>();

    /**
     * 用临时 code 换取 session_key 和 openid（使用全局默认 appid/secret）。
     *
     * @param code 前端 wx.login() 获取的临时凭证
     * @return 微信会话响应
     */
    public WechatSession code2session(String code) {
        return exchange(wechatConfig.getAppid(), wechatConfig.getSecret(), code);
    }

    /**
     * 用临时 code 换取 session_key 和 openid（多租户：使用指定租户的 appid/secret）。
     *
     * @param appid  租户小程序 AppID
     * @param secret 租户小程序 Secret（已解密的明文）
     * @param code   前端 wx.login() 获取的临时凭证
     * @return 微信会话响应
     */
    @Override
    public WechatSession exchange(String appid, String secret, String code) {
        String body;
        try {
            body = restTemplate.getForObject(
                    CODE2SESSION_URL, String.class, appid, secret, code);
        } catch (RestClientException exception) {
            throw unavailable("微信登录服务暂不可用", exception);
        }
        if (body == null || body.isBlank()) {
            throw unavailable("微信登录服务返回为空", null);
        }

        WechatSessionResponse response;
        try {
            response = objectMapper.readValue(body, WechatSessionResponse.class);
        } catch (Exception e) {
            throw unavailable("微信登录服务响应无法解析", e);
        }

        if (!response.isSuccess()) {
            log.warn("微信 code2session 拒绝, errcode={}", response.getErrcode());
            if (response.getErrcode() != null
                    && (response.getErrcode() == 40029
                    || response.getErrcode() == 40226)) {
                throw new WechatExchangeException(
                        WechatExchangeException.Reason.INVALID_CODE,
                        "微信登录动态码无效");
            }
            throw unavailable("微信登录服务暂不可用", null);
        }

        return new WechatSession(response.getOpenid(), response.getUnionid());
    }

    @Override
    public WechatSession exchangeByCredentialReference(
            String appid,
            String secretReference,
            String code) {
        return exchange(appid, secretResolver.resolve(secretReference), code);
    }

    @Override
    public WechatPhoneNumber exchangePhoneNumberByCredentialReference(
            String appid,
            String secretReference,
            String phoneCode) {
        String secret = secretResolver.resolve(secretReference);
        AccessToken token = accessToken(appid, secretReference, secret, false);
        JsonNode response = requestPhoneNumber(token.value(), phoneCode);
        int errorCode = response.path("errcode").asInt(0);
        if (errorCode == 40001 || errorCode == 42001) {
            accessTokens.remove(tokenKey(appid, secretReference));
            token = accessToken(appid, secretReference, secret, true);
            response = requestPhoneNumber(token.value(), phoneCode);
            errorCode = response.path("errcode").asInt(0);
        }
        if (errorCode != 0) {
            log.warn("微信手机号动态码拒绝, errcode={}", errorCode);
            if (errorCode == 40029) {
                throw new WechatExchangeException(
                        WechatExchangeException.Reason.INVALID_CODE,
                        "微信手机号动态码无效");
            }
            throw unavailable("微信手机号服务暂不可用", null);
        }
        JsonNode phoneInfo = response.path("phone_info");
        String pureNumber = phoneInfo.path("purePhoneNumber").asText(null);
        if (pureNumber == null || pureNumber.isBlank()) {
            pureNumber = phoneInfo.path("phoneNumber").asText(null);
        }
        if (pureNumber == null || pureNumber.isBlank()) {
            throw unavailable("微信手机号服务未返回号码", null);
        }
        return new WechatPhoneNumber(
                pureNumber,
                phoneInfo.path("countryCode").asText(null));
    }

    private AccessToken accessToken(
            String appid,
            String secretReference,
            String secret,
            boolean forceRefresh) {
        String key = tokenKey(appid, secretReference);
        AccessToken cached = accessTokens.get(key);
        if (!forceRefresh && cached != null && cached.usable()) {
            return cached;
        }
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        String body;
        try {
            body = restTemplate.postForObject(
                    STABLE_TOKEN_URL,
                    new HttpEntity<>(Map.of(
                            "grant_type", "client_credential",
                            "appid", appid,
                            "secret", secret,
                            "force_refresh", forceRefresh), headers),
                    String.class);
        } catch (RestClientException exception) {
            throw unavailable("微信访问令牌服务暂不可用", exception);
        }
        JsonNode response = parse(body, "微信访问令牌服务响应无法解析");
        int errorCode = response.path("errcode").asInt(0);
        String value = response.path("access_token").asText(null);
        long expiresIn = response.path("expires_in").asLong(0);
        if (errorCode != 0 || value == null || expiresIn <= 0) {
            log.warn("微信访问令牌获取失败, errcode={}", errorCode);
            throw unavailable("微信访问令牌服务暂不可用", null);
        }
        AccessToken created = new AccessToken(
                value,
                Instant.now().plusSeconds(Math.max(30, expiresIn - 120)));
        accessTokens.put(key, created);
        return created;
    }

    private JsonNode requestPhoneNumber(
            String accessToken,
            String phoneCode) {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        String body;
        try {
            body = restTemplate.postForObject(
                    PHONE_NUMBER_URL,
                    new HttpEntity<>(Map.of("code", phoneCode), headers),
                    String.class,
                    accessToken);
        } catch (RestClientException exception) {
            throw unavailable("微信手机号服务暂不可用", exception);
        }
        return parse(body, "微信手机号服务响应无法解析");
    }

    private JsonNode parse(String body, String message) {
        if (body == null || body.isBlank()) {
            throw unavailable(message, null);
        }
        try {
            return objectMapper.readTree(body);
        } catch (Exception exception) {
            throw unavailable(message, exception);
        }
    }

    private static String tokenKey(
            String appid,
            String secretReference) {
        return appid + "\0" + secretReference;
    }

    private static WechatExchangeException unavailable(
            String message,
            Throwable cause) {
        return new WechatExchangeException(
                WechatExchangeException.Reason.SERVICE_UNAVAILABLE,
                message,
                cause);
    }

    private record AccessToken(String value, Instant usableUntil) {
        private boolean usable() {
            return usableUntil.isAfter(Instant.now());
        }
    }
}
