package org.enveloping.ecobin.integration.fake;

import org.enveloping.ecobin.integration.cos.BusinessReleaseArtifactProperties;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class FakeBusinessReleaseArtifactStorageTest {

    private static final String OBJECT_KEY =
            "edge-runtime/releases/11111111-1111-4111-8111-111111111111/"
                    + "package.tar.gz";

    @TempDir
    Path temporary;

    @Test
    void acceptsAnExactRetryButNeverOverwritesDifferentContent()
            throws Exception {
        BusinessReleaseArtifactProperties properties =
                new BusinessReleaseArtifactProperties();
        properties.setFakeDirectory(temporary.resolve("private").toString());
        FakeBusinessReleaseArtifactStorage storage =
                new FakeBusinessReleaseArtifactStorage(properties);
        Path first = temporary.resolve("first.tar.gz");
        Files.writeString(first, "first", StandardCharsets.UTF_8);
        String firstSha = digest(first);

        storage.storeImmutable(OBJECT_KEY, first, firstSha, Files.size(first));
        storage.storeImmutable(OBJECT_KEY, first, firstSha, Files.size(first));

        Path downloaded = temporary.resolve("downloaded.tar.gz");
        storage.download(OBJECT_KEY, downloaded);
        assertThat(Files.readString(downloaded)).isEqualTo("first");

        Path changed = temporary.resolve("changed.tar.gz");
        Files.writeString(changed, "other", StandardCharsets.UTF_8);
        assertThatThrownBy(() -> storage.storeImmutable(
                OBJECT_KEY, changed, digest(changed), Files.size(changed)))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("禁止覆盖");
    }

    @Test
    void rejectsAnyPathOutsideTheBackendGeneratedReleaseDirectory()
            throws Exception {
        BusinessReleaseArtifactProperties properties =
                new BusinessReleaseArtifactProperties();
        properties.setFakeDirectory(temporary.resolve("private").toString());
        FakeBusinessReleaseArtifactStorage storage =
                new FakeBusinessReleaseArtifactStorage(properties);
        Path source = temporary.resolve("package.tar.gz");
        Files.writeString(source, "package", StandardCharsets.UTF_8);

        assertThatThrownBy(() -> storage.storeImmutable(
                "../package.tar.gz", source, digest(source), Files.size(source)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("固定规则");
    }

    private static String digest(Path path) throws Exception {
        return HexFormat.of().formatHex(
                MessageDigest.getInstance("SHA-256")
                        .digest(Files.readAllBytes(path)));
    }
}
