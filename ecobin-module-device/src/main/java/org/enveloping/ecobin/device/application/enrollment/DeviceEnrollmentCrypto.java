package org.enveloping.ecobin.device.application.enrollment;

import tools.jackson.databind.ObjectMapper;

import javax.crypto.Cipher;
import javax.crypto.KeyAgreement;
import javax.crypto.Mac;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import java.io.ByteArrayOutputStream;
import java.math.BigInteger;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.KeyFactory;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.MessageDigest;
import java.security.PublicKey;
import java.security.SecureRandom;
import java.security.Signature;
import java.security.spec.X509EncodedKeySpec;
import java.util.Arrays;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/** Cross-language enrollment cryptography with one canonical transcript. */
public final class DeviceEnrollmentCrypto {

    private static final byte[] ED25519_SPKI_PREFIX = hex(
            "302a300506032b6570032100");
    private static final byte[] X25519_SPKI_PREFIX = hex(
            "302a300506032b656e032100");
    private static final byte[] TRANSCRIPT_DOMAIN =
            "ecobin-device-enrollment-v1\0"
                    .getBytes(StandardCharsets.US_ASCII);
    private static final byte[] RESPONSE_DOMAIN =
            "ecobin-device-enrollment-response-v1\0"
                    .getBytes(StandardCharsets.US_ASCII);
    private static final byte[] LEGACY_PROOF_DOMAIN =
            "ecobin-legacy-onenet-proof-v1\0"
                    .getBytes(StandardCharsets.US_ASCII);
    private static final char[] CROCKFORD =
            "0123456789ABCDEFGHJKMNPQRSTVWXYZ".toCharArray();

    private DeviceEnrollmentCrypto() {
    }

    public static byte[] canonicalRequest(
            ObjectMapper mapper,
            CanonicalEnrollmentRequest request) {
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("schemaVersion", 1);
        value.put("enrollmentUid", request.enrollmentUid().toString());
        value.put("challengeUid", request.challengeUid().toString());
        value.put("enrollmentKeyId", request.enrollmentKeyId());
        value.put("enrollmentMode", request.enrollmentMode());
        value.put("hardwareSn", request.hardwareSn());
        value.put("identityPublicKey", request.identityPublicKey());
        value.put("responseWrapPublicKey", request.responseWrapPublicKey());
        value.put("tunnelPublicKey", request.tunnelPublicKey());
        value.put("sshHostPublicKey", request.sshHostPublicKey());
        return mapper.writeValueAsBytes(value);
    }

    public static byte[] transcript(byte[] challengeNonce, byte[] canonical) {
        ByteArrayOutputStream output = new ByteArrayOutputStream(
                TRANSCRIPT_DOMAIN.length + challengeNonce.length
                        + canonical.length);
        output.writeBytes(TRANSCRIPT_DOMAIN);
        output.writeBytes(challengeNonce);
        output.writeBytes(canonical);
        return output.toByteArray();
    }

    public static byte[] hmacSha256(byte[] key, byte[] value) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(key, "HmacSHA256"));
            return mac.doFinal(value);
        } catch (GeneralSecurityException exception) {
            throw new IllegalStateException("HMAC-SHA256 is unavailable", exception);
        }
    }

    public static byte[] legacyProof(
            String oneNetDeviceSecret,
            byte[] transcript) {
        if (oneNetDeviceSecret == null || oneNetDeviceSecret.isBlank()) {
            throw new IllegalArgumentException(
                    "OneNet device secret must not be blank");
        }
        return hmacSha256(
                oneNetDeviceSecret.getBytes(StandardCharsets.UTF_8),
                concat(LEGACY_PROOF_DOMAIN, transcript));
    }

    public static boolean verifyEd25519(
            String publicKey,
            byte[] value,
            byte[] signature) {
        if (signature.length != 64) {
            return false;
        }
        try {
            Signature verifier = Signature.getInstance("Ed25519");
            verifier.initVerify(ed25519PublicKey(publicKey));
            verifier.update(value);
            return verifier.verify(signature);
        } catch (GeneralSecurityException exception) {
            throw new IllegalArgumentException(
                    "invalid Ed25519 enrollment public key", exception);
        }
    }

    public static PublicKey ed25519PublicKey(String canonicalOpenSshKey) {
        byte[] raw = ed25519RawPublicKey(canonicalOpenSshKey);
        try {
            return KeyFactory.getInstance("Ed25519").generatePublic(
                    new X509EncodedKeySpec(concat(ED25519_SPKI_PREFIX, raw)));
        } catch (GeneralSecurityException exception) {
            throw new IllegalArgumentException("invalid Ed25519 public key", exception);
        }
    }

    public static byte[] ed25519RawPublicKey(String canonicalOpenSshKey) {
        if (canonicalOpenSshKey == null
                || !canonicalOpenSshKey.matches(
                "^ssh-ed25519 [A-Za-z0-9+/]{68}$")) {
            throw new IllegalArgumentException(
                    "public key must be canonical ssh-ed25519");
        }
        byte[] blob;
        try {
            blob = Base64.getDecoder().decode(
                    canonicalOpenSshKey.substring("ssh-ed25519 ".length()));
        } catch (IllegalArgumentException exception) {
            throw new IllegalArgumentException("invalid Ed25519 base64", exception);
        }
        ByteBuffer buffer = ByteBuffer.wrap(blob);
        byte[] algorithm = sized(buffer, 11);
        byte[] raw = sized(buffer, 32);
        if (buffer.hasRemaining()
                || !Arrays.equals(
                algorithm,
                "ssh-ed25519".getBytes(StandardCharsets.US_ASCII))) {
            throw new IllegalArgumentException("invalid Ed25519 SSH blob");
        }
        return raw;
    }

    public static byte[] sshFingerprint(String canonicalOpenSshKey) {
        String encoded = canonicalOpenSshKey.substring(
                "ssh-ed25519 ".length());
        return sha256(Base64.getDecoder().decode(encoded));
    }

    public static String deriveHardwareSn(String identityPublicKey) {
        byte[] digest = sha256(ed25519RawPublicKey(identityPublicKey));
        BigInteger value = new BigInteger(1, digest)
                .shiftRight((digest.length * 8) - 130);
        char[] encoded = new char[26];
        for (int index = encoded.length - 1; index >= 0; index--) {
            encoded[index] = CROCKFORD[value.and(
                    BigInteger.valueOf(31)).intValue()];
            value = value.shiftRight(5);
        }
        return "ECM0-" + new String(encoded);
    }

    public static EncryptedResponse encryptResponse(
            ObjectMapper mapper,
            UUID enrollmentUid,
            String hardwareSn,
            byte[] recipientRawPublicKey,
            Object plaintext,
            byte[] challengeNonce,
            SecureRandom random) {
        if (recipientRawPublicKey.length != 32) {
            throw new IllegalArgumentException(
                    "X25519 response public key must contain 32 bytes");
        }
        try {
            KeyPairGenerator generator = KeyPairGenerator.getInstance("X25519");
            KeyPair ephemeral = generator.generateKeyPair();
            PublicKey recipient = KeyFactory.getInstance("X25519")
                    .generatePublic(new X509EncodedKeySpec(
                            concat(X25519_SPKI_PREFIX, recipientRawPublicKey)));
            KeyAgreement agreement = KeyAgreement.getInstance("X25519");
            agreement.init(ephemeral.getPrivate());
            agreement.doPhase(recipient, true);
            byte[] sharedSecret = agreement.generateSecret();
            byte[] info = concat(
                    RESPONSE_DOMAIN,
                    enrollmentUid.toString().getBytes(StandardCharsets.US_ASCII));
            byte[] key = hkdfSha256(sharedSecret, challengeNonce, info, 32);
            Arrays.fill(sharedSecret, (byte) 0);

            Map<String, Object> aadValue = new LinkedHashMap<>();
            aadValue.put("schemaVersion", 1);
            aadValue.put("enrollmentUid", enrollmentUid.toString());
            aadValue.put("hardwareSn", hardwareSn);
            byte[] aad = mapper.writeValueAsBytes(aadValue);
            byte[] nonce = new byte[12];
            random.nextBytes(nonce);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(
                    Cipher.ENCRYPT_MODE,
                    new SecretKeySpec(key, "AES"),
                    new GCMParameterSpec(128, nonce));
            cipher.updateAAD(aad);
            byte[] ciphertext = cipher.doFinal(
                    mapper.writeValueAsBytes(plaintext));
            Arrays.fill(key, (byte) 0);

            byte[] encodedPublic = ephemeral.getPublic().getEncoded();
            byte[] ephemeralRaw = Arrays.copyOfRange(
                    encodedPublic, encodedPublic.length - 32,
                    encodedPublic.length);
            Map<String, Object> envelope = new LinkedHashMap<>();
            envelope.put("schemaVersion", 1);
            envelope.put("algorithm", "X25519-HKDF-SHA256-AES-256-GCM");
            envelope.put("ephemeralPublicKey",
                    Base64.getEncoder().encodeToString(ephemeralRaw));
            envelope.put("nonce", Base64.getEncoder().encodeToString(nonce));
            envelope.put("aadSha256",
                    java.util.HexFormat.of().formatHex(sha256(aad)));
            envelope.put("ciphertext",
                    Base64.getEncoder().encodeToString(ciphertext));
            return new EncryptedResponse(
                    mapper.writeValueAsBytes(envelope), nonce);
        } catch (GeneralSecurityException exception) {
            throw new IllegalStateException(
                    "device enrollment response encryption failed", exception);
        }
    }

    public static byte[] decodeBase64(String value, int exactLength) {
        byte[] decoded;
        try {
            decoded = Base64.getDecoder().decode(value);
        } catch (IllegalArgumentException exception) {
            throw new IllegalArgumentException("invalid base64 value", exception);
        }
        if (decoded.length != exactLength) {
            throw new IllegalArgumentException(
                    "base64 value has an invalid decoded length");
        }
        return decoded;
    }

    public static byte[] sha256(byte[] value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(value);
        } catch (GeneralSecurityException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }

    private static byte[] hkdfSha256(
            byte[] input,
            byte[] salt,
            byte[] info,
            int length) throws GeneralSecurityException {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(salt, "HmacSHA256"));
        byte[] pseudoRandomKey = mac.doFinal(input);
        ByteArrayOutputStream output = new ByteArrayOutputStream(length);
        byte[] previous = new byte[0];
        int counter = 1;
        while (output.size() < length) {
            mac.init(new SecretKeySpec(pseudoRandomKey, "HmacSHA256"));
            mac.update(previous);
            mac.update(info);
            mac.update((byte) counter++);
            previous = mac.doFinal();
            output.writeBytes(previous);
        }
        Arrays.fill(pseudoRandomKey, (byte) 0);
        return Arrays.copyOf(output.toByteArray(), length);
    }

    private static byte[] sized(ByteBuffer buffer, int expected) {
        if (buffer.remaining() < 4) {
            throw new IllegalArgumentException("truncated SSH key blob");
        }
        int length = buffer.getInt();
        if (length != expected || buffer.remaining() < length) {
            throw new IllegalArgumentException("invalid SSH key blob field");
        }
        byte[] value = new byte[length];
        buffer.get(value);
        return value;
    }

    private static byte[] concat(byte[]... values) {
        int length = Arrays.stream(values).mapToInt(value -> value.length).sum();
        byte[] result = new byte[length];
        int offset = 0;
        for (byte[] value : values) {
            System.arraycopy(value, 0, result, offset, value.length);
            offset += value.length;
        }
        return result;
    }

    private static byte[] hex(String value) {
        return java.util.HexFormat.of().parseHex(value);
    }

    public record CanonicalEnrollmentRequest(
            UUID enrollmentUid,
            UUID challengeUid,
            String enrollmentKeyId,
            String enrollmentMode,
            String hardwareSn,
            String identityPublicKey,
            String responseWrapPublicKey,
            String tunnelPublicKey,
            String sshHostPublicKey) {
    }

    public record EncryptedResponse(byte[] envelope, byte[] nonce) {

        public EncryptedResponse {
            envelope = envelope.clone();
            nonce = nonce.clone();
        }
    }
}
