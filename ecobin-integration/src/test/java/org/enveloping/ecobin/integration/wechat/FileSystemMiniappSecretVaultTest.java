package org.enveloping.ecobin.integration.wechat;

import org.enveloping.ecobin.identity.api.error.MiniappSecretVaultException;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.mock.env.MockEnvironment;

import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class FileSystemMiniappSecretVaultTest {

    @TempDir
    Path temporaryDirectory;

    @Test
    void storesOneImmutableSecretVersionPerOperation() throws Exception {
        FileSystemMiniappSecretVault vault =
                new FileSystemMiniappSecretVault(
                        temporaryDirectory.toString(),
                        new MockEnvironment());
        UUID operationUid = UUID.randomUUID();
        String secret = "fake-app-secret-for-vault-test";

        String reference = vault.store(
                operationUid, sha256(secret), secret);

        assertEquals(secret, vault.read(reference));
        assertEquals(
                reference,
                vault.store(operationUid, sha256(secret), secret));
        MiniappSecretVaultException conflict = assertThrows(
                MiniappSecretVaultException.class,
                () -> vault.store(
                        operationUid,
                        sha256("different-secret"),
                        "different-secret"));
        assertEquals(
                MiniappSecretVaultException.Reason.IDEMPOTENCY_CONFLICT,
                conflict.reason());
    }

    @Test
    void readsLegacyEnvironmentStyleReferencesWithoutExposingPaths() {
        MockEnvironment environment = new MockEnvironment()
                .withProperty(
                        "ECOBIN_TEST_MINIAPP_SECRET",
                        "legacy-configured-secret");
        FileSystemMiniappSecretVault vault =
                new FileSystemMiniappSecretVault(
                        temporaryDirectory.toString(),
                        environment);

        assertEquals(
                "legacy-configured-secret",
                vault.read("env:ECOBIN_TEST_MINIAPP_SECRET"));
    }

    private static String sha256(String value) throws Exception {
        return HexFormat.of().formatHex(
                MessageDigest.getInstance("SHA-256").digest(
                        value.getBytes(StandardCharsets.UTF_8)));
    }
}
