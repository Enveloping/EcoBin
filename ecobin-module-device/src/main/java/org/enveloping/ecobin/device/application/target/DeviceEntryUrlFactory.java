package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.identity.api.value.MiniappEntryBaseUrl;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Arrays;
import java.util.HexFormat;

/** Builds the exact public URL shared by Web, OneNet and the fixed-frame MCU. */
@Component
public final class DeviceEntryUrlFactory {

    public static final int MAXIMUM_ASCII_BYTES = 192;
    private static final String MAXIMUM_DEVICE_CODE =
            "Dv_" + "A".repeat(61);

    private final String baseUrl;
    private final byte[] baseUrlSha256;

    public DeviceEntryUrlFactory(
            @Value("${ecobin.miniapp.device-entry-base-url}")
            String baseUrl) {
        if (!MiniappEntryBaseUrl.isValid(baseUrl)) {
            throw new IllegalArgumentException(
                    "ecobin.miniapp.device-entry-base-url must be a valid HTTPS base URL without deviceCode");
        }
        this.baseUrl = baseUrl;
        requireFixedFrameUrl(
                MiniappEntryBaseUrl.appendDeviceCode(
                        baseUrl, MAXIMUM_DEVICE_CODE));
        this.baseUrlSha256 = sha256(ascii(baseUrl));
    }

    public Entry create(String deviceCode) {
        String url = MiniappEntryBaseUrl.appendDeviceCode(
                baseUrl, deviceCode);
        byte[] ascii = requireFixedFrameUrl(url);
        byte[] digest = sha256(ascii);
        return new Entry(
                url,
                HexFormat.of().formatHex(digest),
                digest);
    }

    public byte[] baseUrlSha256() {
        return Arrays.copyOf(baseUrlSha256, baseUrlSha256.length);
    }

    public String baseUrlSha256Hex() {
        return HexFormat.of().formatHex(baseUrlSha256);
    }

    private static byte[] requireFixedFrameUrl(String value) {
        byte[] encoded = ascii(value);
        if (!value.startsWith("https://")
                || encoded.length < 1
                || encoded.length > MAXIMUM_ASCII_BYTES) {
            throw new IllegalArgumentException(
                    "complete device entry URL must fit the 192-byte fixed-frame field");
        }
        for (byte current : encoded) {
            int unsigned = Byte.toUnsignedInt(current);
            if (unsigned < 0x21 || unsigned > 0x7e
                    || unsigned == '"' || unsigned == '\\') {
                throw new IllegalArgumentException(
                    "complete device entry URL must contain safe printable ASCII only");
            }
        }
        return encoded;
    }

    private static byte[] ascii(String value) {
        if (value == null
                || !StandardCharsets.US_ASCII.newEncoder()
                .canEncode(value)) {
            throw new IllegalArgumentException(
                    "device entry URL must be ASCII");
        }
        return value.getBytes(StandardCharsets.US_ASCII);
    }

    private static byte[] sha256(byte[] value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(value);
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable", exception);
        }
    }

    public record Entry(
            String url,
            String sha256Hex,
            byte[] sha256) {

        public Entry {
            if (url == null || sha256Hex == null
                    || sha256 == null || sha256.length != 32) {
                throw new IllegalArgumentException(
                        "device entry URL value is incomplete");
            }
            sha256 = Arrays.copyOf(sha256, sha256.length);
        }

        @Override
        public byte[] sha256() {
            return Arrays.copyOf(sha256, sha256.length);
        }
    }
}
