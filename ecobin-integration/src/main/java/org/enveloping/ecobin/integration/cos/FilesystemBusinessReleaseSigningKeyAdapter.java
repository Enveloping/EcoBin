package org.enveloping.ecobin.integration.cos;

import org.enveloping.ecobin.device.api.port.BusinessReleaseSigningKeyPort;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.security.KeyFactory;
import java.security.spec.X509EncodedKeySpec;
import java.util.Base64;
import java.util.List;
import java.util.regex.Pattern;

@Component
public class FilesystemBusinessReleaseSigningKeyAdapter
        implements BusinessReleaseSigningKeyPort {

    private static final Pattern KEY_ID = Pattern.compile(
            "^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$");
    private final BusinessReleaseArtifactProperties properties;

    public FilesystemBusinessReleaseSigningKeyAdapter(
            BusinessReleaseArtifactProperties properties) {
        this.properties = properties;
    }

    @Override
    public Readiness readiness() {
        String configured = properties.getSigningPublicKeysDirectory();
        if (configured == null || configured.isBlank()) {
            return new Readiness(false, "尚未配置业务发布验签公钥目录");
        }
        Path directory = Path.of(configured).toAbsolutePath().normalize();
        if (!Files.isDirectory(directory, LinkOption.NOFOLLOW_LINKS)) {
            return new Readiness(false, "业务发布验签公钥目录不可读");
        }
        try (var entries = Files.list(directory)) {
            List<Path> keys = entries.sorted().toList();
            if (keys.isEmpty()) {
                return new Readiness(false, "业务发布验签公钥目录为空");
            }
            for (Path key : keys) {
                String fileName = key.getFileName().toString();
                if (!fileName.endsWith(".pem")
                        || !KEY_ID.matcher(fileName.substring(
                                0, fileName.length() - 4)).matches()
                        || !Files.isRegularFile(
                                key, LinkOption.NOFOLLOW_LINKS)) {
                    return new Readiness(
                            false,
                            "业务发布验签公钥目录包含不允许的文件");
                }
                decodeEd25519PublicKey(Files.readAllBytes(key));
            }
            return new Readiness(
                    true,
                    "业务发布验签公钥目录可用，共 " + keys.size() + " 把公钥");
        } catch (Exception exception) {
            return new Readiness(false, "业务发布验签公钥目录包含无效或不可读的公钥");
        }
    }

    @Override
    public byte[] loadEd25519PublicKey(String signingKeyId) {
        if (signingKeyId == null
                || !KEY_ID.matcher(signingKeyId).matches()) {
            throw new IllegalArgumentException("签名密钥编号格式不正确");
        }
        Readiness readiness = readiness();
        if (!readiness.available()) {
            throw new IllegalStateException(readiness.message());
        }
        Path directory = Path.of(properties.getSigningPublicKeysDirectory())
                .toAbsolutePath().normalize();
        Path key = directory.resolve(signingKeyId + ".pem").normalize();
        if (!key.getParent().equals(directory)
                || !Files.isRegularFile(key, LinkOption.NOFOLLOW_LINKS)) {
            throw new IllegalArgumentException("找不到指定的业务发布验签公钥");
        }
        try {
            return Files.readAllBytes(key);
        } catch (IOException exception) {
            throw new IllegalStateException("业务发布验签公钥不可读", exception);
        }
    }

    private static void decodeEd25519PublicKey(byte[] pem) throws Exception {
        String text = new String(pem, StandardCharsets.US_ASCII).trim();
        if (!text.startsWith("-----BEGIN PUBLIC KEY-----")
                || !text.endsWith("-----END PUBLIC KEY-----")
                || text.contains("PRIVATE KEY")) {
            throw new IllegalArgumentException("not a public-key PEM");
        }
        String encoded = text
                .replace("-----BEGIN PUBLIC KEY-----", "")
                .replace("-----END PUBLIC KEY-----", "")
                .replaceAll("\\s", "");
        KeyFactory.getInstance("Ed25519").generatePublic(
                new X509EncodedKeySpec(Base64.getDecoder().decode(encoded)));
    }
}
