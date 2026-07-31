package org.enveloping.ecobin.integration.wechat;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import org.enveloping.ecobin.identity.api.error.WechatExchangeException;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.RestTemplate;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class WechatMiniappClientDiagnosticsTest {

    private static final String APP_ID = "wx1234567890abcdef";
    private static final String APP_SECRET =
            "app-secret-must-not-be-logged";
    private static final String SECRET_REFERENCE =
            "local-miniapp-secret:00000000-0000-4000-8000-000000000001";
    private static final String ACCESS_TOKEN =
            "access-token-must-not-be-logged";
    private static final String PHONE_CODE =
            "phone-code-must-not-be-logged";

    @Test
    void recordsWechatStageAndErrorWithoutCredentialValues() {
        WechatConfig config = new WechatConfig();
        RestTemplate restTemplate = new DiagnosticRestTemplate();
        MiniappSecretResolver secretResolver = reference -> APP_SECRET;
        WechatMiniappClient client = new WechatMiniappClient(
                config,
                restTemplate,
                secretResolver,
                new ObjectMapper());

        Logger logger = (Logger) LoggerFactory.getLogger(
                WechatMiniappClient.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try {
            assertThrows(
                    WechatExchangeException.class,
                    () -> client.exchangePhoneNumberByCredentialReference(
                            APP_ID,
                            SECRET_REFERENCE,
                            PHONE_CODE));
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }

        String logs = appender.list.stream()
                .map(ILoggingEvent::getFormattedMessage)
                .collect(Collectors.joining("\n"));
        assertTrue(logs.contains(
                "stage=PHONE_BINDING outcome=STARTED"));
        assertTrue(logs.contains(
                "stage=SECRET_RESOLUTION outcome=SUCCESS"));
        assertTrue(logs.contains(
                "stage=ACCESS_TOKEN_CACHE outcome=MISS"));
        assertTrue(logs.contains(
                "stage=ACCESS_TOKEN_RESPONSE outcome=SUCCESS"));
        assertTrue(logs.contains(
                "stage=PHONE_NUMBER_RESPONSE outcome=REJECTED"));
        assertTrue(logs.contains("appId=" + APP_ID));
        assertTrue(logs.contains("errcode=40013"));
        assertTrue(logs.contains("errmsg=invalid appid"));
        assertFalse(logs.contains(APP_SECRET));
        assertFalse(logs.contains(SECRET_REFERENCE));
        assertFalse(logs.contains(ACCESS_TOKEN));
        assertFalse(logs.contains(PHONE_CODE));
    }

    @Test
    void recordsWechatHttpRejectionPayloadWithoutCredentialValues() {
        WechatConfig config = new WechatConfig();
        MiniappSecretResolver secretResolver = reference -> APP_SECRET;
        WechatMiniappClient client = new WechatMiniappClient(
                config,
                new RejectedAccessTokenRestTemplate(),
                secretResolver,
                new ObjectMapper());

        Logger logger = (Logger) LoggerFactory.getLogger(
                WechatMiniappClient.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try {
            assertThrows(
                    WechatExchangeException.class,
                    () -> client.exchangePhoneNumberByCredentialReference(
                            APP_ID,
                            SECRET_REFERENCE,
                            PHONE_CODE));
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }

        String logs = appender.list.stream()
                .map(ILoggingEvent::getFormattedMessage)
                .collect(Collectors.joining("\n"));
        assertTrue(logs.contains(
                "stage=ACCESS_TOKEN_HTTP outcome=HTTP_REJECTED"));
        assertTrue(logs.contains("httpStatus=400"));
        assertTrue(logs.contains("errcode=40164"));
        assertTrue(logs.contains("errmsg=invalid ip"));
        assertFalse(logs.contains(APP_SECRET));
        assertFalse(logs.contains(SECRET_REFERENCE));
        assertFalse(logs.contains(PHONE_CODE));
    }

    @Test
    void sendsFixedLengthJsonToBothWechatPostEndpoints() {
        FixedLengthInspectingRestTemplate restTemplate =
                new FixedLengthInspectingRestTemplate();
        WechatMiniappClient client = new WechatMiniappClient(
                new WechatConfig(),
                restTemplate,
                reference -> APP_SECRET,
                new ObjectMapper());

        client.exchangePhoneNumberByCredentialReference(
                APP_ID,
                SECRET_REFERENCE,
                PHONE_CODE);

        assertEquals(2, restTemplate.requests.size());
        restTemplate.requests.forEach(
                WechatMiniappClientDiagnosticsTest::assertFixedLengthJson);
    }

    private static void assertFixedLengthJson(HttpEntity<?> request) {
        byte[] body = assertInstanceOf(
                byte[].class,
                request.getBody());
        assertTrue(body.length > 0);
        assertEquals(
                body.length,
                request.getHeaders().getContentLength());
        assertEquals(
                MediaType.APPLICATION_JSON,
                request.getHeaders().getContentType());
    }

    private static final class DiagnosticRestTemplate
            extends RestTemplate {

        @Override
        public <T> T postForObject(
                String url,
                Object request,
                Class<T> responseType,
                Object... uriVariables) {
            String body;
            if (url.contains("stable_token")) {
                body = """
                        {
                          "access_token": "%s",
                          "expires_in": 7200
                        }
                        """.formatted(ACCESS_TOKEN);
            } else {
                body = """
                        {
                          "errcode": 40013,
                          "errmsg": "invalid appid; secret=%s; code=%s; token=%s"
                        }
                        """.formatted(
                        APP_SECRET,
                        PHONE_CODE,
                        ACCESS_TOKEN);
            }
            return responseType.cast(body);
        }
    }

    private static final class RejectedAccessTokenRestTemplate
            extends RestTemplate {

        @Override
        public <T> T postForObject(
                String url,
                Object request,
                Class<T> responseType,
                Object... uriVariables) {
            String body = """
                    {
                      "errcode": 40164,
                      "errmsg": "invalid ip; secret=%s"
                    }
                    """.formatted(APP_SECRET);
            throw HttpClientErrorException.create(
                    HttpStatus.BAD_REQUEST,
                    "Bad Request",
                    HttpHeaders.EMPTY,
                    body.getBytes(StandardCharsets.UTF_8),
                    StandardCharsets.UTF_8);
        }
    }

    private static final class FixedLengthInspectingRestTemplate
            extends RestTemplate {

        private final List<HttpEntity<?>> requests = new ArrayList<>();

        @Override
        public <T> T postForObject(
                String url,
                Object request,
                Class<T> responseType,
                Object... uriVariables) {
            requests.add(assertInstanceOf(HttpEntity.class, request));
            String body = url.contains("stable_token")
                    ? """
                    {
                      "access_token": "%s",
                      "expires_in": 7200
                    }
                    """.formatted(ACCESS_TOKEN)
                    : """
                    {
                      "errcode": 0,
                      "phone_info": {
                        "purePhoneNumber": "13800138000",
                        "countryCode": "86"
                      }
                    }
                    """;
            return responseType.cast(body);
        }
    }
}
