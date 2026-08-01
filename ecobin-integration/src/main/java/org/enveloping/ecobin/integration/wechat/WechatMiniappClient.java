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
import org.springframework.web.client.HttpStatusCodeException;
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
        long exchangeStarted = System.nanoTime();
        log.info(
                "WECHAT_MINIAPP_DIAGNOSTIC stage=PHONE_BINDING "
                        + "outcome=STARTED appId={}",
                appid);
        long secretStarted = System.nanoTime();
        final String secret;
        try {
            secret = secretResolver.resolve(secretReference);
        } catch (WechatExchangeException exception) {
            log.warn(
                    "WECHAT_MINIAPP_DIAGNOSTIC stage=SECRET_RESOLUTION "
                            + "outcome=FAILED appId={} reason={} "
                            + "durationMs={} exception={}",
                    appid,
                    exception.reason(),
                    elapsedMillis(secretStarted),
                    exceptionType(exception));
            throw exception;
        }
        log.info(
                "WECHAT_MINIAPP_DIAGNOSTIC stage=SECRET_RESOLUTION "
                        + "outcome=SUCCESS appId={} durationMs={}",
                appid,
                elapsedMillis(secretStarted));

        AccessToken token = accessToken(appid, secretReference, secret, false);
        JsonNode response = requestPhoneNumber(
                appid,
                token.value(),
                phoneCode,
                "INITIAL");
        int errorCode = response.path("errcode").asInt(0);
        if (errorCode == 40001 || errorCode == 42001) {
            log.info(
                    "WECHAT_MINIAPP_DIAGNOSTIC stage=PHONE_BINDING "
                            + "outcome=TOKEN_REFRESH_REQUIRED appId={} "
                            + "errcode={}",
                    appid,
                    errorCode);
            accessTokens.remove(tokenKey(appid, secretReference));
            token = accessToken(appid, secretReference, secret, true);
            response = requestPhoneNumber(
                    appid,
                    token.value(),
                    phoneCode,
                    "AFTER_TOKEN_REFRESH");
            errorCode = response.path("errcode").asInt(0);
        }
        if (errorCode != 0) {
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
            log.warn(
                    "WECHAT_MINIAPP_DIAGNOSTIC "
                            + "stage=PHONE_INFO_VALIDATION "
                            + "outcome=PHONE_NUMBER_MISSING appId={} "
                            + "phoneInfoPresent={} totalDurationMs={}",
                    appid,
                    !phoneInfo.isMissingNode() && !phoneInfo.isNull(),
                    elapsedMillis(exchangeStarted));
            throw unavailable("微信手机号服务未返回号码", null);
        }
        log.info(
                "WECHAT_MINIAPP_DIAGNOSTIC stage=PHONE_BINDING "
                        + "outcome=SUCCESS appId={} countryCodePresent={} "
                        + "totalDurationMs={}",
                appid,
                !phoneInfo.path("countryCode").asText("").isBlank(),
                elapsedMillis(exchangeStarted));
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
            log.info(
                    "WECHAT_MINIAPP_DIAGNOSTIC stage=ACCESS_TOKEN_CACHE "
                            + "outcome=HIT appId={} usableForSeconds={}",
                    appid,
                    Math.max(
                            0,
                            cached.usableUntil().getEpochSecond()
                                    - Instant.now().getEpochSecond()));
            return cached;
        }
        String cacheReason = forceRefresh
                ? "FORCE_REFRESH"
                : cached == null ? "ABSENT" : "EXPIRED";
        log.info(
                "WECHAT_MINIAPP_DIAGNOSTIC stage=ACCESS_TOKEN_CACHE "
                        + "outcome=MISS appId={} reason={}",
                appid,
                cacheReason);
        long started = System.nanoTime();
        String body;
        try {
            body = restTemplate.postForObject(
                    STABLE_TOKEN_URL,
                    fixedLengthJson(Map.of(
                            "grant_type", "client_credential",
                            "appid", appid,
                            "secret", secret,
                            "force_refresh", forceRefresh)),
                    String.class);
        } catch (HttpStatusCodeException exception) {
            HttpRejection rejection = httpRejection(
                    exception,
                    secret);
            log.warn(
                    "WECHAT_MINIAPP_DIAGNOSTIC stage=ACCESS_TOKEN_HTTP "
                            + "outcome=HTTP_REJECTED appId={} "
                            + "forceRefresh={} httpStatus={} errcode={} "
                            + "errmsg={} responseBody={} responseLength={} "
                            + "durationMs={}",
                    appid,
                    forceRefresh,
                    exception.getStatusCode().value(),
                    rejection.errorCode(),
                    rejection.errorMessage(),
                    rejection.bodyState(),
                    rejection.responseLength(),
                    elapsedMillis(started));
            throw unavailable("微信访问令牌服务暂不可用", exception);
        } catch (RestClientException exception) {
            log.warn(
                    "WECHAT_MINIAPP_DIAGNOSTIC stage=ACCESS_TOKEN_HTTP "
                            + "outcome=TRANSPORT_FAILURE appId={} "
                            + "forceRefresh={} durationMs={} exception={} "
                            + "rootCause={}",
                    appid,
                    forceRefresh,
                    elapsedMillis(started),
                    exceptionType(exception),
                    rootCauseType(exception));
            throw unavailable("微信访问令牌服务暂不可用", exception);
        }
        JsonNode response = parse(
                body,
                "微信访问令牌服务响应无法解析",
                "ACCESS_TOKEN_RESPONSE",
                appid,
                started);
        int errorCode = response.path("errcode").asInt(0);
        String value = response.path("access_token").asText(null);
        long expiresIn = response.path("expires_in").asLong(0);
        if (errorCode != 0 || value == null || expiresIn <= 0) {
            log.warn(
                    "WECHAT_MINIAPP_DIAGNOSTIC "
                            + "stage=ACCESS_TOKEN_RESPONSE "
                            + "outcome=REJECTED appId={} forceRefresh={} "
                            + "errcode={} errmsg={} accessTokenPresent={} "
                            + "expiresIn={} durationMs={}",
                    appid,
                    forceRefresh,
                    errorCode,
                    safeWechatMessage(response, secret),
                    value != null && !value.isBlank(),
                    expiresIn,
                    elapsedMillis(started));
            throw unavailable("微信访问令牌服务暂不可用", null);
        }
        log.info(
                "WECHAT_MINIAPP_DIAGNOSTIC stage=ACCESS_TOKEN_RESPONSE "
                        + "outcome=SUCCESS appId={} forceRefresh={} "
                        + "expiresIn={} durationMs={}",
                appid,
                forceRefresh,
                expiresIn,
                elapsedMillis(started));
        AccessToken created = new AccessToken(
                value,
                Instant.now().plusSeconds(Math.max(30, expiresIn - 120)));
        accessTokens.put(key, created);
        return created;
    }

    private JsonNode requestPhoneNumber(
            String appid,
            String accessToken,
            String phoneCode,
            String attempt) {
        long started = System.nanoTime();
        String body;
        try {
            body = restTemplate.postForObject(
                    PHONE_NUMBER_URL,
                    fixedLengthJson(Map.of("code", phoneCode)),
                    String.class,
                    accessToken);
        } catch (HttpStatusCodeException exception) {
            HttpRejection rejection = httpRejection(
                    exception,
                    accessToken,
                    phoneCode);
            log.warn(
                    "WECHAT_MINIAPP_DIAGNOSTIC stage=PHONE_NUMBER_HTTP "
                            + "outcome=HTTP_REJECTED appId={} attempt={} "
                            + "httpStatus={} errcode={} errmsg={} "
                            + "responseBody={} responseLength={} "
                            + "durationMs={}",
                    appid,
                    attempt,
                    exception.getStatusCode().value(),
                    rejection.errorCode(),
                    rejection.errorMessage(),
                    rejection.bodyState(),
                    rejection.responseLength(),
                    elapsedMillis(started));
            throw unavailable("微信手机号服务暂不可用", exception);
        } catch (RestClientException exception) {
            log.warn(
                    "WECHAT_MINIAPP_DIAGNOSTIC stage=PHONE_NUMBER_HTTP "
                            + "outcome=TRANSPORT_FAILURE appId={} attempt={} "
                            + "durationMs={} exception={} rootCause={}",
                    appid,
                    attempt,
                    elapsedMillis(started),
                    exceptionType(exception),
                    rootCauseType(exception));
            throw unavailable("微信手机号服务暂不可用", exception);
        }
        JsonNode response = parse(
                body,
                "微信手机号服务响应无法解析",
                "PHONE_NUMBER_RESPONSE",
                appid,
                started);
        int errorCode = response.path("errcode").asInt(0);
        boolean phoneInfoPresent = response.hasNonNull("phone_info");
        if (errorCode == 0) {
            log.info(
                    "WECHAT_MINIAPP_DIAGNOSTIC "
                            + "stage=PHONE_NUMBER_RESPONSE "
                            + "outcome=SUCCESS appId={} attempt={} "
                            + "phoneInfoPresent={} durationMs={}",
                    appid,
                    attempt,
                    phoneInfoPresent,
                    elapsedMillis(started));
        } else {
            log.warn(
                    "WECHAT_MINIAPP_DIAGNOSTIC "
                            + "stage=PHONE_NUMBER_RESPONSE "
                            + "outcome=REJECTED appId={} attempt={} "
                            + "errcode={} errmsg={} phoneInfoPresent={} "
                            + "durationMs={}",
                    appid,
                    attempt,
                    errorCode,
                    safeWechatMessage(
                            response,
                            accessToken,
                            phoneCode),
                    phoneInfoPresent,
                    elapsedMillis(started));
        }
        return response;
    }

    private HttpEntity<byte[]> fixedLengthJson(Map<String, ?> payload) {
        final byte[] body;
        try {
            body = objectMapper.writeValueAsBytes(payload);
        } catch (Exception exception) {
            throw unavailable(
                    "微信请求正文无法序列化",
                    exception);
        }
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        headers.setContentLength(body.length);
        return new HttpEntity<>(body, headers);
    }

    private JsonNode parse(
            String body,
            String message,
            String stage,
            String appid,
            long started) {
        if (body == null || body.isBlank()) {
            log.warn(
                    "WECHAT_MINIAPP_DIAGNOSTIC stage={} "
                            + "outcome=EMPTY_RESPONSE appId={} "
                            + "durationMs={}",
                    stage,
                    appid,
                    elapsedMillis(started));
            throw unavailable(message, null);
        }
        try {
            return objectMapper.readTree(body);
        } catch (Exception exception) {
            log.warn(
                    "WECHAT_MINIAPP_DIAGNOSTIC stage={} "
                            + "outcome=PARSE_FAILURE appId={} "
                            + "responseLength={} durationMs={} exception={}",
                    stage,
                    appid,
                    body.length(),
                    elapsedMillis(started),
                    exceptionType(exception));
            throw unavailable(message, exception);
        }
    }

    private static String safeWechatMessage(
            JsonNode response,
            String... sensitiveValues) {
        String message = response.path("errmsg").asText("");
        if (message.isBlank()) {
            return "<none>";
        }
        for (String sensitiveValue : sensitiveValues) {
            if (sensitiveValue != null && !sensitiveValue.isBlank()) {
                message = message.replace(
                        sensitiveValue,
                        "<redacted>");
            }
        }
        message = message
                .replaceAll(
                        "(?i)\\b(access[_ -]?token|token|"
                                + "app[_ -]?secret|secret|"
                                + "phone[_ -]?code|code)"
                                + "\\s*([=:])\\s*[^\\s,;]+",
                        "$1$2<redacted>")
                .replaceAll("[\\r\\n\\t]", " ")
                .replaceAll("\\p{Cntrl}", "?")
                .replaceAll(" {2,}", " ")
                .trim();
        return message.length() <= 256
                ? message
                : message.substring(0, 256) + "...";
    }

    private HttpRejection httpRejection(
            HttpStatusCodeException exception,
            String... sensitiveValues) {
        String body = exception.getResponseBodyAsString();
        int responseLength = body == null ? 0 : body.length();
        if (body == null || body.isBlank()) {
            return new HttpRejection(
                    "<none>",
                    "<none>",
                    "EMPTY",
                    responseLength);
        }
        try {
            JsonNode response = objectMapper.readTree(body);
            String errorCode = response.path("errcode").asText("");
            return new HttpRejection(
                    errorCode.isBlank() ? "<none>" : errorCode,
                    safeWechatMessage(response, sensitiveValues),
                    "PARSED",
                    responseLength);
        } catch (Exception ignored) {
            return new HttpRejection(
                    "<unavailable>",
                    "<unavailable>",
                    "UNPARSEABLE",
                    responseLength);
        }
    }

    private static long elapsedMillis(long started) {
        return Math.max(
                0,
                (System.nanoTime() - started) / 1_000_000);
    }

    private static String exceptionType(Throwable exception) {
        return exception == null
                ? "<none>"
                : exception.getClass().getSimpleName();
    }

    private static String rootCauseType(Throwable exception) {
        Throwable root = exception;
        while (root != null && root.getCause() != null) {
            root = root.getCause();
        }
        return exceptionType(root);
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

    private record HttpRejection(
            String errorCode,
            String errorMessage,
            String bodyState,
            int responseLength) {
    }
}
