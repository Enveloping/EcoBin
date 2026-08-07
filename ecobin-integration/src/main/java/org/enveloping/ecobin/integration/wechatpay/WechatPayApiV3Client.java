package org.enveloping.ecobin.integration.wechatpay;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyFactory;
import java.security.PrivateKey;
import java.security.PublicKey;
import java.security.Signature;
import java.security.spec.PKCS8EncodedKeySpec;
import java.security.spec.X509EncodedKeySpec;
import java.time.Duration;
import java.time.Instant;
import java.util.Base64;
import java.util.UUID;

@Component
@ConditionalOnProperty(
        prefix = "ecobin.external", name = "mode", havingValue = "real")
public class WechatPayApiV3Client {

    private static final Duration NOTIFICATION_CLOCK_SKEW = Duration.ofMinutes(5);

    private final WechatPayProperties properties;
    private final ObjectMapper mapper;
    private final HttpClient http;
    private final PrivateKey merchantPrivateKey;
    private final PublicKey wechatPayPublicKey;
    private final String wechatPayPublicKeyId;

    public WechatPayApiV3Client(
            WechatPayProperties properties,
            ObjectMapper mapper) {
        if (!properties.isConfigured()) {
            throw new IllegalStateException(
                    "real external mode requires complete WeChat Pay APIv3 configuration");
        }
        this.properties = properties;
        this.mapper = mapper;
        this.merchantPrivateKey = readPrivateKey(
                Path.of(properties.getMerchantPrivateKeyPath()));
        this.wechatPayPublicKey = readPublicKey(
                Path.of(properties.getPublicKeyPath()));
        this.wechatPayPublicKeyId = properties.getPublicKeyId();
        this.http = HttpClient.newBuilder()
                .connectTimeout(Duration.ofMillis(
                        properties.getConnectTimeoutMillis()))
                .build();
    }

    public JsonNode get(String pathAndQuery) {
        return exchange("GET", pathAndQuery, null);
    }

    public JsonNode post(String pathAndQuery, JsonNode body) {
        return exchange("POST", pathAndQuery, body);
    }

    boolean usesMerchant(String mchid) {
        return properties.getMchid().equals(mchid);
    }

    public JsonNode exchange(
            String method, String pathAndQuery, JsonNode body) {
        try {
            String requestBody = body == null
                    ? "" : mapper.writeValueAsString(body);
            long timestamp = Instant.now().getEpochSecond();
            String nonce = UUID.randomUUID().toString().replace("-", "");
            String message = method + "\n" + pathAndQuery + "\n"
                    + timestamp + "\n" + nonce + "\n" + requestBody + "\n";
            String authorization = authorization(
                    timestamp, nonce, sign(message, merchantPrivateKey));
            HttpRequest.Builder builder = HttpRequest.newBuilder()
                    .uri(URI.create(stripTrailingSlash(properties.getBaseUrl())
                            + pathAndQuery))
                    .timeout(Duration.ofMillis(
                            properties.getRequestTimeoutMillis()))
                    .header("Accept", "application/json")
                    .header("Content-Type", "application/json")
                    .header("Authorization", authorization)
                    .header("Wechatpay-Serial", wechatPayPublicKeyId)
                    .header("User-Agent", "EcoBin-WeChatPay-APIv3/1.0");
            if ("GET".equals(method)) {
                builder.GET();
            } else {
                builder.method(method,
                        HttpRequest.BodyPublishers.ofString(
                                requestBody, StandardCharsets.UTF_8));
            }
            HttpResponse<String> response = http.send(
                    builder.build(),
                    HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            verifySignedMessage(
                    response.headers().firstValue("Wechatpay-Serial")
                            .orElseThrow(() -> new SecurityException(
                                    "WeChat Pay response lacks serial")),
                    response.headers().firstValue("Wechatpay-Timestamp")
                            .orElseThrow(() -> new SecurityException(
                                    "WeChat Pay response lacks timestamp")),
                    response.headers().firstValue("Wechatpay-Nonce")
                            .orElseThrow(() -> new SecurityException(
                                    "WeChat Pay response lacks nonce")),
                    response.headers().firstValue("Wechatpay-Signature")
                            .orElseThrow(() -> new SecurityException(
                                    "WeChat Pay response lacks signature")),
                    response.body(), false);
            JsonNode responseBody = response.body() == null
                    || response.body().isBlank()
                    ? mapper.createObjectNode()
                    : mapper.readTree(response.body());
            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                throw new WechatPayApiException(
                        response.statusCode(), text(responseBody, "code"),
                        text(responseBody, "message"));
            }
            return responseBody;
        } catch (WechatPayApiException failure) {
            throw failure;
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new WechatPayApiException(
                    599, "CLIENT_INTERRUPTED", "微信支付请求被中断");
        } catch (IOException failure) {
            throw new WechatPayApiException(
                    599, "NETWORK_ERROR", "微信支付请求结果未知");
        } catch (SecurityException failure) {
            throw new WechatPayApiException(
                    502, "SIGNATURE_ERROR", "微信支付响应验签失败");
        }
    }

    public void verifyNotification(
            String serial,
            String timestamp,
            String nonce,
            String signature,
            String rawBody) {
        verifySignedMessage(
                serial, timestamp, nonce, signature, rawBody, true);
    }

    public JsonNode decryptResource(JsonNode resource) {
        if (resource == null
                || !"AEAD_AES_256_GCM".equals(text(resource, "algorithm"))) {
            throw new SecurityException(
                    "unsupported WeChat Pay notification resource algorithm");
        }
        try {
            byte[] key = properties.getApiV3Key()
                    .getBytes(StandardCharsets.UTF_8);
            byte[] nonce = requiredText(resource, "nonce")
                    .getBytes(StandardCharsets.UTF_8);
            byte[] associated = text(resource, "associated_data") == null
                    ? new byte[0]
                    : text(resource, "associated_data")
                    .getBytes(StandardCharsets.UTF_8);
            byte[] ciphertext = Base64.getDecoder().decode(
                    requiredText(resource, "ciphertext"));
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, new SecretKeySpec(key, "AES"),
                    new GCMParameterSpec(128, nonce));
            cipher.updateAAD(associated);
            return mapper.readTree(new String(
                    cipher.doFinal(ciphertext), StandardCharsets.UTF_8));
        } catch (Exception failure) {
            throw new SecurityException(
                    "WeChat Pay notification resource decryption failed",
                    failure);
        }
    }

    private void verifySignedMessage(
            String serial,
            String timestamp,
            String nonce,
            String signature,
            String body,
            boolean enforceFreshness) {
        if (signature == null
                || signature.startsWith("WECHATPAY/SIGNTEST/")) {
            throw new SecurityException("WeChat Pay signature probe rejected");
        }
        if (!wechatPayPublicKeyId.equals(serial == null
                ? ""
                : serial.trim())) {
            throw new SecurityException(
                    "unknown WeChat Pay public key ID");
        }
        long seconds;
        try {
            seconds = Long.parseLong(timestamp);
        } catch (NumberFormatException failure) {
            throw new SecurityException("invalid WeChat Pay timestamp", failure);
        }
        if (enforceFreshness && Duration.between(
                Instant.ofEpochSecond(seconds), Instant.now()).abs()
                .compareTo(NOTIFICATION_CLOCK_SKEW) > 0) {
            throw new SecurityException("stale WeChat Pay notification");
        }
        String message = timestamp + "\n" + nonce + "\n" + body + "\n";
        try {
            Signature verifier = Signature.getInstance("SHA256withRSA");
            verifier.initVerify(wechatPayPublicKey);
            verifier.update(message.getBytes(StandardCharsets.UTF_8));
            if (!verifier.verify(Base64.getDecoder().decode(signature))) {
                throw new SecurityException("invalid WeChat Pay signature");
            }
        } catch (SecurityException failure) {
            throw failure;
        } catch (Exception failure) {
            throw new SecurityException(
                    "cannot verify WeChat Pay signature", failure);
        }
    }

    private String authorization(
            long timestamp, String nonce, String signature) {
        return "WECHATPAY2-SHA256-RSA2048 "
                + "mchid=\"" + properties.getMchid() + "\","
                + "nonce_str=\"" + nonce + "\","
                + "timestamp=\"" + timestamp + "\","
                + "serial_no=\"" + properties.getMerchantSerialNumber() + "\","
                + "signature=\"" + signature + "\"";
    }

    private static String sign(String message, PrivateKey privateKey) {
        try {
            Signature signer = Signature.getInstance("SHA256withRSA");
            signer.initSign(privateKey);
            signer.update(message.getBytes(StandardCharsets.UTF_8));
            return Base64.getEncoder().encodeToString(signer.sign());
        } catch (Exception failure) {
            throw new IllegalStateException(
                    "cannot sign WeChat Pay request", failure);
        }
    }

    private static PrivateKey readPrivateKey(Path path) {
        try {
            String pem = Files.readString(path, StandardCharsets.US_ASCII)
                    .replace("-----BEGIN PRIVATE KEY-----", "")
                    .replace("-----END PRIVATE KEY-----", "")
                    .replaceAll("\\s", "");
            return KeyFactory.getInstance("RSA").generatePrivate(
                    new PKCS8EncodedKeySpec(Base64.getDecoder().decode(pem)));
        } catch (Exception failure) {
            throw new IllegalStateException(
                    "cannot read WeChat Pay merchant private key", failure);
        }
    }

    private static PublicKey readPublicKey(Path path) {
        try {
            String pem = Files.readString(path, StandardCharsets.US_ASCII)
                    .replace("-----BEGIN PUBLIC KEY-----", "")
                    .replace("-----END PUBLIC KEY-----", "")
                    .replaceAll("\\s", "");
            return KeyFactory.getInstance("RSA").generatePublic(
                    new X509EncodedKeySpec(Base64.getDecoder().decode(pem)));
        } catch (Exception failure) {
            throw new IllegalStateException(
                    "cannot read WeChat Pay public key", failure);
        }
    }

    static String text(JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        return value == null || value.isNull() ? null : value.asText();
    }

    static String requiredText(JsonNode node, String field) {
        String value = text(node, field);
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(
                    "WeChat Pay payload lacks " + field);
        }
        return value;
    }

    private static String stripTrailingSlash(String value) {
        String result = value.trim();
        while (result.endsWith("/")) {
            result = result.substring(0, result.length() - 1);
        }
        return result;
    }
}
