package org.enveloping.ecobin.framework.idempotency;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

/** Canonical SHA-256 fingerprints used by the global operation ledger. */
public final class GlobalOperationDigests {

    private GlobalOperationDigests() { }

    /**
     * Hashes values using UTF-8 byte-length framing: {@code length:value;}.
     * Null is represented by the empty value.
     */
    public static String sha256(Object... canonicalValues) {
        if (canonicalValues == null) {
            throw new IllegalArgumentException(
                    "canonicalValues must not be null");
        }
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            for (Object canonicalValue : canonicalValues) {
                String value = canonicalValue == null
                        ? "" : canonicalValue.toString();
                byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
                digest.update(Integer.toString(bytes.length)
                        .getBytes(StandardCharsets.US_ASCII));
                digest.update((byte) ':');
                digest.update(bytes);
                digest.update((byte) ';');
            }
            return HexFormat.of().formatHex(digest.digest());
        } catch (NoSuchAlgorithmException unavailable) {
            throw new IllegalStateException(
                    "SHA-256 unavailable", unavailable);
        }
    }

    /** Existing I-004 platform-wide management-scope fingerprint. */
    public static String platformScope() {
        return sha256("PLATFORM", "", false);
    }
}
