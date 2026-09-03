package org.enveloping.ecobin.integration.cos;

import org.enveloping.ecobin.device.api.port.BusinessReleaseSigningKeyPort;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
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
        return new Readiness(true, "业务发布验签公钥目录可用");
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
}
