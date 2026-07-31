package org.enveloping.ecobin.integration.wechat;

import org.enveloping.ecobin.identity.api.error.MiniappSecretVaultException;
import org.enveloping.ecobin.identity.api.port.MiniappSecretVaultPort;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermission;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Pattern;

/**
 * Local/development secret vault. Production can replace this port with a
 * managed secret provider without changing identity-domain persistence.
 */
@Component
final class FileSystemMiniappSecretVault implements MiniappSecretVaultPort {

    private static final String LOCAL_PREFIX = "local-miniapp-secret:";
    private static final String ENV_PREFIX = "env:";
    private static final Pattern ENV_NAME =
            Pattern.compile("^ECOBIN_[A-Z0-9_]{1,120}$");
    private static final Set<PosixFilePermission> OWNER_ONLY = Set.of(
            PosixFilePermission.OWNER_READ,
            PosixFilePermission.OWNER_WRITE);

    private final Path directory;
    private final Environment environment;

    FileSystemMiniappSecretVault(
            @Value("${ecobin.identity.miniapp-secret-store.directory:"
                    + "./.ecobin/miniapp-secrets}")
            String directory,
            Environment environment) {
        this.directory = Path.of(directory).toAbsolutePath().normalize();
        this.environment = environment;
    }

    @Override
    public synchronized String store(
            UUID operationUid,
            String secretSha256,
            String appSecret) {
        if (operationUid == null
                || secretSha256 == null
                || !secretSha256.matches("^[0-9a-f]{64}$")
                || appSecret == null
                || appSecret.isBlank()) {
            throw unavailable("小程序密钥存储请求无效", null);
        }
        String reference = LOCAL_PREFIX + operationUid;
        Path target = secretPath(operationUid);
        try {
            Files.createDirectories(directory);
            if (Files.exists(target)) {
                requireSameSecret(target, secretSha256);
                return reference;
            }
            Path temporary = Files.createTempFile(
                    directory, operationUid + "-", ".tmp");
            try {
                Files.writeString(
                        temporary,
                        appSecret,
                        StandardCharsets.UTF_8);
                restrictPermissions(temporary);
                try {
                    Files.move(temporary, target);
                } catch (FileAlreadyExistsException concurrentWrite) {
                    requireSameSecret(target, secretSha256);
                    return reference;
                }
            } finally {
                Files.deleteIfExists(temporary);
            }
            requireSameSecret(target, secretSha256);
            return reference;
        } catch (MiniappSecretVaultException exception) {
            throw exception;
        } catch (IOException exception) {
            throw unavailable("小程序密钥暂时无法保存", exception);
        }
    }

    @Override
    public String read(String secretReference) {
        if (secretReference == null || secretReference.isBlank()) {
            throw unavailable("小程序密钥引用缺失", null);
        }
        if (secretReference.startsWith(ENV_PREFIX)) {
            return readEnvironment(secretReference);
        }
        if (!secretReference.startsWith(LOCAL_PREFIX)) {
            throw unavailable("小程序密钥引用类型不受支持", null);
        }
        String rawUid = secretReference.substring(LOCAL_PREFIX.length());
        final UUID uid;
        try {
            uid = UUID.fromString(rawUid);
        } catch (IllegalArgumentException exception) {
            throw unavailable("小程序密钥引用无效", exception);
        }
        try {
            String value = Files.readString(
                    secretPath(uid), StandardCharsets.UTF_8);
            if (value.isBlank()) {
                throw unavailable("小程序密钥暂不可用", null);
            }
            return value;
        } catch (MiniappSecretVaultException exception) {
            throw exception;
        } catch (IOException exception) {
            throw unavailable("小程序密钥暂不可用", exception);
        }
    }

    private String readEnvironment(String secretReference) {
        String variable = secretReference.substring(ENV_PREFIX.length());
        if (!ENV_NAME.matcher(variable).matches()) {
            throw unavailable("小程序密钥环境引用格式无效", null);
        }
        String value = System.getenv(variable);
        if (value == null || value.isBlank()) {
            value = environment.getProperty(variable);
        }
        if (value == null || value.isBlank()) {
            throw unavailable("小程序密钥暂不可用", null);
        }
        return value;
    }

    private Path secretPath(UUID operationUid) {
        Path target = directory.resolve(operationUid + ".secret").normalize();
        if (!target.getParent().equals(directory)) {
            throw unavailable("小程序密钥引用无效", null);
        }
        return target;
    }

    private static void requireSameSecret(
            Path target,
            String expectedSha256) throws IOException {
        String actual = sha256(Files.readAllBytes(target));
        if (!MessageDigest.isEqual(
                expectedSha256.getBytes(StandardCharsets.US_ASCII),
                actual.getBytes(StandardCharsets.US_ASCII))) {
            throw new MiniappSecretVaultException(
                    MiniappSecretVaultException.Reason.IDEMPOTENCY_CONFLICT,
                    "相同操作标识已绑定到不同的小程序密钥");
        }
    }

    private static void restrictPermissions(Path path) {
        try {
            Files.setPosixFilePermissions(path, OWNER_ONLY);
        } catch (UnsupportedOperationException | IOException ignored) {
            // Windows ACLs inherit from the already user-controlled .ecobin
            // directory; POSIX runtimes are narrowed explicitly.
        }
    }

    private static String sha256(byte[] value) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(value));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }

    private static MiniappSecretVaultException unavailable(
            String message,
            Throwable cause) {
        return new MiniappSecretVaultException(
                MiniappSecretVaultException.Reason.UNAVAILABLE,
                message,
                cause);
    }
}
