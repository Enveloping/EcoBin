package org.enveloping.ecobin.integration.wechatpay;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import tools.jackson.databind.ObjectMapper;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.PrivateKey;
import java.security.Signature;
import java.time.Instant;
import java.util.Base64;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;

class WechatPayApiV3ClientPublicKeyTest {

    private static final String PUBLIC_KEY_ID =
            "PUB_KEY_ID_0116571234562024052000123400000000";

    @TempDir
    Path temporaryDirectory;

    @Test
    void verifiesNotificationsOnlyWithTheConfiguredPublicKeyId()
            throws Exception {
        KeyPair merchant = keyPair();
        KeyPair wechatPay = keyPair();
        WechatPayApiV3Client client = client(merchant, wechatPay, null);

        String timestamp = Long.toString(Instant.now().getEpochSecond());
        String nonce = "notification-nonce";
        String body = "{\"id\":\"notification-1\"}";
        String signature = sign(
                timestamp + "\n" + nonce + "\n" + body + "\n",
                wechatPay.getPrivate());

        assertDoesNotThrow(() -> client.verifyNotification(
                PUBLIC_KEY_ID,
                timestamp,
                nonce,
                signature,
                body));
        assertThrows(SecurityException.class,
                () -> client.verifyNotification(
                        "PUB_KEY_ID_DIFFERENT",
                        timestamp,
                        nonce,
                        signature,
                        body));
    }

    @Test
    void sendsThePublicKeyIdAndVerifiesSynchronousResponses()
            throws Exception {
        KeyPair merchant = keyPair();
        KeyPair wechatPay = keyPair();
        String timestamp = Long.toString(Instant.now().getEpochSecond());
        String nonce = "response-nonce";
        String body = "{\"status\":\"ok\"}";
        String responseSignature = sign(
                timestamp + "\n" + nonce + "\n" + body + "\n",
                wechatPay.getPrivate());
        AtomicReference<String> requestPublicKeyId =
                new AtomicReference<>();

        HttpServer server = HttpServer.create(
                new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/v3/public-key-probe", exchange -> {
            requestPublicKeyId.set(
                    exchange.getRequestHeaders().getFirst(
                            "Wechatpay-Serial"));
            exchange.getResponseHeaders().add(
                    "Wechatpay-Serial", PUBLIC_KEY_ID);
            exchange.getResponseHeaders().add(
                    "Wechatpay-Timestamp", timestamp);
            exchange.getResponseHeaders().add(
                    "Wechatpay-Nonce", nonce);
            exchange.getResponseHeaders().add(
                    "Wechatpay-Signature", responseSignature);
            byte[] response = body.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, response.length);
            try (var output = exchange.getResponseBody()) {
                output.write(response);
            }
        });
        server.start();
        try {
            WechatPayApiV3Client client = client(
                    merchant,
                    wechatPay,
                    "http://127.0.0.1:" + server.getAddress().getPort());

            assertEquals(
                    "ok",
                    client.get("/v3/public-key-probe")
                            .path("status")
                            .asText());
            assertEquals(PUBLIC_KEY_ID, requestPublicKeyId.get());
        } finally {
            server.stop(0);
        }
    }

    private WechatPayApiV3Client client(
            KeyPair merchant,
            KeyPair wechatPay,
            String baseUrl) throws Exception {
        Path merchantPrivateKey = temporaryDirectory.resolve(
                "apiclient_key-" + System.nanoTime() + ".pem");
        Path publicKey = temporaryDirectory.resolve(
                "pub_key-" + System.nanoTime() + ".pem");
        writePem(
                merchantPrivateKey,
                "PRIVATE KEY",
                merchant.getPrivate().getEncoded());
        writePem(
                publicKey,
                "PUBLIC KEY",
                wechatPay.getPublic().getEncoded());

        WechatPayProperties properties = new WechatPayProperties();
        if (baseUrl != null) {
            properties.setBaseUrl(baseUrl);
        }
        properties.setMchid("1900000109");
        properties.setMerchantSerialNumber("MERCHANT_SERIAL");
        properties.setMerchantPrivateKeyPath(
                merchantPrivateKey.toString());
        properties.setApiV3Key(
                "0123456789abcdef0123456789abcdef");
        properties.setPublicKeyId(PUBLIC_KEY_ID);
        properties.setPublicKeyPath(publicKey.toString());
        properties.setNotifyBaseUrl("https://pay-notify.ecobin.cn");
        return new WechatPayApiV3Client(properties, new ObjectMapper());
    }

    private static KeyPair keyPair() throws Exception {
        KeyPairGenerator generator = KeyPairGenerator.getInstance("RSA");
        generator.initialize(2048);
        return generator.generateKeyPair();
    }

    private static String sign(String message, PrivateKey privateKey)
            throws Exception {
        Signature signer = Signature.getInstance("SHA256withRSA");
        signer.initSign(privateKey);
        signer.update(message.getBytes(StandardCharsets.UTF_8));
        return Base64.getEncoder().encodeToString(signer.sign());
    }

    private static void writePem(
            Path path,
            String type,
            byte[] encoded) throws Exception {
        String body = Base64.getMimeEncoder(64, new byte[]{'\n'})
                .encodeToString(encoded);
        Files.writeString(
                path,
                "-----BEGIN " + type + "-----\n"
                        + body
                        + "\n-----END " + type + "-----\n",
                StandardCharsets.US_ASCII);
    }
}
