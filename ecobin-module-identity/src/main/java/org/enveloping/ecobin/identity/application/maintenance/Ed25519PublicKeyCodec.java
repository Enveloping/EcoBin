package org.enveloping.ecobin.identity.application.maintenance;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Arrays;
import java.util.Base64;

final class Ed25519PublicKeyCodec {

    private static final String ALGORITHM = "ssh-ed25519";
    private static final byte[] ALGORITHM_BYTES =
            ALGORITHM.getBytes(StandardCharsets.US_ASCII);

    private Ed25519PublicKeyCodec() {
    }

    static ParsedPublicKey parse(String value) {
        if (value == null || !value.startsWith(ALGORITHM + " ")) {
            throw invalid();
        }
        String encoded = value.substring(ALGORITHM.length() + 1);
        if (encoded.isEmpty()
                || encoded.indexOf(' ') >= 0
                || encoded.indexOf('\t') >= 0
                || encoded.indexOf('\r') >= 0
                || encoded.indexOf('\n') >= 0) {
            throw invalid();
        }

        byte[] blob;
        try {
            blob = Base64.getDecoder().decode(encoded);
        } catch (IllegalArgumentException malformed) {
            throw invalid();
        }
        if (!Base64.getEncoder().encodeToString(blob).equals(encoded)) {
            throw invalid();
        }

        ByteBuffer buffer = ByteBuffer.wrap(blob);
        byte[] algorithm = readString(buffer);
        byte[] publicKey = readString(buffer);
        if (!Arrays.equals(algorithm, ALGORITHM_BYTES)
                || publicKey.length != 32
                || buffer.hasRemaining()) {
            throw invalid();
        }

        String canonical = ALGORITHM + " " + encoded;
        return new ParsedPublicKey(canonical, sha256Fingerprint(blob));
    }

    private static byte[] readString(ByteBuffer buffer) {
        if (buffer.remaining() < Integer.BYTES) {
            throw invalid();
        }
        int length = buffer.getInt();
        if (length < 0 || length > buffer.remaining()) {
            throw invalid();
        }
        byte[] value = new byte[length];
        buffer.get(value);
        return value;
    }

    private static String sha256Fingerprint(byte[] blob) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(blob);
            return "SHA256:" + Base64.getEncoder()
                    .withoutPadding()
                    .encodeToString(digest);
        } catch (NoSuchAlgorithmException unavailable) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable", unavailable);
        }
    }

    private static TargetApiException invalid() {
        return new TargetApiException(
                400,
                "IDENTITY.MAINTENANCE_SSH_PUBLIC_KEY_INVALID",
                "仅支持不带注释的规范 ssh-ed25519 公钥");
    }

    record ParsedPublicKey(
            String canonicalPublicKey,
            String fingerprintSha256) {
    }
}
