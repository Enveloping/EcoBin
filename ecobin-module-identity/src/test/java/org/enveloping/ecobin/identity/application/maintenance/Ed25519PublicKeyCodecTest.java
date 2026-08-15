package org.enveloping.ecobin.identity.application.maintenance;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.junit.jupiter.api.Test;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Base64;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class Ed25519PublicKeyCodecTest {

    @Test
    void acceptsCanonicalEd25519KeyAndUsesOpenSshFingerprintFormat()
            throws Exception {
        byte[] blob = blob(3);
        String encoded = Base64.getEncoder().encodeToString(blob);
        String canonical = "ssh-ed25519 " + encoded;

        var parsed = Ed25519PublicKeyCodec.parse(canonical);

        assertEquals(canonical, parsed.canonicalPublicKey());
        assertEquals(
                "SHA256:" + Base64.getEncoder()
                        .withoutPadding()
                        .encodeToString(MessageDigest.getInstance("SHA-256")
                                .digest(blob)),
                parsed.fingerprintSha256());
    }

    @Test
    void rejectsCommentsCertificatesPrivateKeysAndMalformedBlobs() {
        String canonical = canonicalKey(7);
        String encodedRawKey = Base64.getEncoder().encodeToString(
                new byte[32]);

        for (String rejected : new String[]{
                canonical + " workstation-comment",
                " " + canonical,
                canonical + "\n",
                canonical.replace(
                        "ssh-ed25519",
                        "ssh-ed25519-cert-v01@openssh.com"),
                "-----BEGIN OPENSSH PRIVATE KEY-----",
                "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ==",
                "ssh-ed25519 " + encodedRawKey,
                canonical + "="
        }) {
            TargetApiException failure = assertThrows(
                    TargetApiException.class,
                    () -> Ed25519PublicKeyCodec.parse(rejected),
                    rejected);
            assertEquals(400, failure.status());
            assertEquals(
                    "IDENTITY.MAINTENANCE_SSH_PUBLIC_KEY_INVALID",
                    failure.code());
        }
    }

    static String canonicalKey(int seed) {
        return "ssh-ed25519 " + Base64.getEncoder()
                .encodeToString(blob(seed));
    }

    private static byte[] blob(int seed) {
        byte[] algorithm = "ssh-ed25519"
                .getBytes(StandardCharsets.US_ASCII);
        byte[] key = new byte[32];
        for (int index = 0; index < key.length; index++) {
            key[index] = (byte) (seed + index);
        }
        return ByteBuffer.allocate(
                        Integer.BYTES + algorithm.length
                                + Integer.BYTES + key.length)
                .putInt(algorithm.length)
                .put(algorithm)
                .putInt(key.length)
                .put(key)
                .array();
    }
}
