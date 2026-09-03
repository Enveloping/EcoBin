package org.enveloping.ecobin.integration.fake;

import org.enveloping.ecobin.device.api.port.BusinessReleaseArtifactStoragePort;
import org.enveloping.ecobin.integration.cos.BusinessReleaseArtifactProperties;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.regex.Pattern;

final class FakeBusinessReleaseArtifactStorage
        implements BusinessReleaseArtifactStoragePort {

    private static final Pattern OBJECT_KEY = Pattern.compile(
            "^edge-runtime/releases/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}"
                    + "-[89ab][0-9a-f]{3}-[0-9a-f]{12}/"
                    + "(?:package\\.tar\\.gz|package\\.sig)$");
    private final Path root;

    FakeBusinessReleaseArtifactStorage(
            BusinessReleaseArtifactProperties properties) {
        this.root = Path.of(properties.getFakeDirectory())
                .toAbsolutePath().normalize();
    }

    @Override
    public Readiness readiness() {
        try {
            Files.createDirectories(root);
            if (Files.isDirectory(root, LinkOption.NOFOLLOW_LINKS)) {
                return new Readiness(true, "本地演练制品目录可用");
            }
        } catch (IOException ignored) {
            // Returned below as an operator-readable readiness failure.
        }
        return new Readiness(false, "本地演练制品目录不可写");
    }

    @Override
    public void storeImmutable(
            String objectKey,
            Path source,
            String sha256,
            long size) {
        Path destination = resolve(objectKey);
        try {
            Files.createDirectories(destination.getParent());
            Files.copy(source, destination);
            if (Files.size(destination) != size) {
                throw new IllegalStateException("本地演练制品写入后大小不一致");
            }
        } catch (java.nio.file.FileAlreadyExistsException exception) {
            if (sameFile(destination, sha256, size)) {
                return;
            }
            throw new IllegalStateException(
                    "业务发布对象已存在，但内容与本次上传不一致，禁止覆盖",
                    exception);
        } catch (IOException exception) {
            throw new IllegalStateException("无法保存本地演练制品", exception);
        }
    }

    @Override
    public void download(String objectKey, Path target) {
        Path source = resolve(objectKey);
        try {
            Files.copy(source, target, StandardCopyOption.COPY_ATTRIBUTES);
        } catch (IOException exception) {
            throw new IllegalStateException("无法读取本地演练制品", exception);
        }
    }

    private Path resolve(String objectKey) {
        if (objectKey == null || !OBJECT_KEY.matcher(objectKey).matches()) {
            throw new IllegalArgumentException("业务发布对象路径不符合固定规则");
        }
        Path resolved = root.resolve(objectKey.replace('/', java.io.File.separatorChar))
                .normalize();
        if (!resolved.startsWith(root)) {
            throw new IllegalArgumentException("业务发布对象路径越界");
        }
        return resolved;
    }

    private static boolean sameFile(Path path, String sha256, long size) {
        try {
            if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS)
                    || Files.size(path) != size) {
                return false;
            }
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            try (var input = Files.newInputStream(path)) {
                byte[] buffer = new byte[1024 * 1024];
                int count;
                while ((count = input.read(buffer)) >= 0) {
                    if (count > 0) {
                        digest.update(buffer, 0, count);
                    }
                }
            }
            return sha256.equals(HexFormat.of().formatHex(digest.digest()));
        } catch (Exception exception) {
            return false;
        }
    }
}
