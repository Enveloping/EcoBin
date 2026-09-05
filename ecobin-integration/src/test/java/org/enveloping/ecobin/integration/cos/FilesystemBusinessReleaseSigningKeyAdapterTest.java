package org.enveloping.ecobin.integration.cos;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyPairGenerator;
import java.util.Base64;

import static org.assertj.core.api.Assertions.assertThat;

class FilesystemBusinessReleaseSigningKeyAdapterTest {

    @TempDir
    Path temporaryDirectory;

    @Test
    void readinessRequiresAtLeastOneValidEd25519PublicKey() throws Exception {
        BusinessReleaseArtifactProperties properties =
                new BusinessReleaseArtifactProperties();
        properties.setSigningPublicKeysDirectory(temporaryDirectory.toString());
        FilesystemBusinessReleaseSigningKeyAdapter adapter =
                new FilesystemBusinessReleaseSigningKeyAdapter(properties);

        assertThat(adapter.readiness().available()).isFalse();

        Files.writeString(
                temporaryDirectory.resolve("business_2026.pem"),
                "-----BEGIN PRIVATE KEY-----\ninvalid\n-----END PRIVATE KEY-----\n",
                StandardCharsets.US_ASCII);
        assertThat(adapter.readiness().available()).isFalse();

        byte[] validPublicKey = publicKeyPem();
        Files.write(
                temporaryDirectory.resolve("business_2026.pem"),
                validPublicKey);
        assertThat(adapter.readiness().available()).isTrue();
        assertThat(adapter.loadEd25519PublicKey("business_2026"))
                .isEqualTo(validPublicKey);
    }

    @Test
    void readinessRejectsUnexpectedEntriesBesideTrustedKeys() throws Exception {
        BusinessReleaseArtifactProperties properties =
                new BusinessReleaseArtifactProperties();
        properties.setSigningPublicKeysDirectory(temporaryDirectory.toString());
        FilesystemBusinessReleaseSigningKeyAdapter adapter =
                new FilesystemBusinessReleaseSigningKeyAdapter(properties);

        Files.write(
                temporaryDirectory.resolve("business_2026.pem"),
                publicKeyPem());
        Files.writeString(
                temporaryDirectory.resolve("notes.txt"),
                "not part of the trust store",
                StandardCharsets.UTF_8);

        assertThat(adapter.readiness().available()).isFalse();
    }

    private static byte[] publicKeyPem() throws Exception {
        byte[] encoded = KeyPairGenerator.getInstance("Ed25519")
                .generateKeyPair()
                .getPublic()
                .getEncoded();
        String body = Base64.getMimeEncoder(64, new byte[]{'\n'})
                .encodeToString(encoded);
        return ("-----BEGIN PUBLIC KEY-----\n"
                + body
                + "\n-----END PUBLIC KEY-----\n")
                .getBytes(StandardCharsets.US_ASCII);
    }
}
