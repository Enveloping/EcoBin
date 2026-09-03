package org.enveloping.ecobin.integration.cos;

import com.qcloud.cos.COSClient;
import com.qcloud.cos.exception.CosServiceException;
import com.qcloud.cos.model.BucketVersioningConfiguration;
import com.qcloud.cos.model.ObjectMetadata;
import com.qcloud.cos.model.PutObjectRequest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.ArgumentCaptor;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class CosBusinessReleaseArtifactStorageTest {

    private static final String BUCKET = "private-release-1250000000";
    private static final String OBJECT_KEY =
            "edge-runtime/releases/11111111-1111-4111-8111-111111111111/"
                    + "package.tar.gz";
    private static final String SHA256 = "a".repeat(64);

    @TempDir
    Path temporary;

    @Test
    void usesAtomicNoOverwriteHeaderWhenBucketNeverEnabledVersioning()
            throws Exception {
        COSClient client = mock(COSClient.class);
        when(client.getBucketVersioningConfiguration(BUCKET))
                .thenReturn(new BucketVersioningConfiguration(
                        BucketVersioningConfiguration.OFF));
        when(client.doesObjectExist(BUCKET, OBJECT_KEY)).thenReturn(false);
        when(client.getObjectMetadata(BUCKET, OBJECT_KEY))
                .thenReturn(metadata(7, SHA256));
        Path source = source();
        CosBusinessReleaseArtifactStorage storage = storage(client);

        storage.storeImmutable(OBJECT_KEY, source, SHA256, 7);

        ArgumentCaptor<PutObjectRequest> request =
                ArgumentCaptor.forClass(PutObjectRequest.class);
        verify(client).putObject(request.capture());
        assertThat(request.getValue().getMetadata().getRawMetadataValue(
                "x-cos-forbid-overwrite")).isEqualTo("true");
        assertThat(request.getValue().getMetadata().getUserMetaDataOf("sha256"))
                .isEqualTo(SHA256);
    }

    @Test
    void resolvesConcurrentExactRetryAfterCosRejectsOverwrite()
            throws Exception {
        COSClient client = mock(COSClient.class);
        when(client.getBucketVersioningConfiguration(BUCKET))
                .thenReturn(new BucketVersioningConfiguration(
                        BucketVersioningConfiguration.OFF));
        when(client.doesObjectExist(BUCKET, OBJECT_KEY)).thenReturn(false);
        CosServiceException conflict = new CosServiceException("already exists");
        conflict.setStatusCode(409);
        when(client.putObject(any(PutObjectRequest.class))).thenThrow(conflict);
        when(client.getObjectMetadata(BUCKET, OBJECT_KEY))
                .thenReturn(metadata(7, SHA256));

        storage(client).storeImmutable(OBJECT_KEY, source(), SHA256, 7);

        verify(client).getObjectMetadata(BUCKET, OBJECT_KEY);
    }

    @Test
    void rejectsBucketThatEverEnabledVersioningBeforeWriting()
            throws Exception {
        COSClient client = mock(COSClient.class);
        when(client.getBucketVersioningConfiguration(BUCKET))
                .thenReturn(new BucketVersioningConfiguration(
                        BucketVersioningConfiguration.SUSPENDED));

        assertThatThrownBy(() -> storage(client).storeImmutable(
                OBJECT_KEY, source(), SHA256, 7))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("从未启用版本控制");
        verify(client, never()).putObject(any(PutObjectRequest.class));
    }

    @Test
    void readinessChecksThatBucketPermissionsCanReadVersioningState() {
        COSClient client = mock(COSClient.class);
        when(client.getBucketVersioningConfiguration(BUCKET))
                .thenThrow(new CosServiceException("access denied"));

        var readiness = storage(client).readiness();

        assertThat(readiness.available()).isFalse();
        assertThat(readiness.message()).contains("访问权限");
    }

    private CosBusinessReleaseArtifactStorage storage(COSClient client) {
        BusinessReleaseArtifactProperties properties =
                new BusinessReleaseArtifactProperties();
        properties.setSecretId("secret-id");
        properties.setSecretKey("secret-key");
        properties.setRegion("ap-guangzhou");
        properties.setBucketName(BUCKET);
        CosProperties photoProperties = new CosProperties();
        photoProperties.setBucketName("public-photo-1250000000");
        return new CosBusinessReleaseArtifactStorage(
                properties, photoProperties, client);
    }

    private Path source() throws Exception {
        Path source = temporary.resolve("package.tar.gz");
        Files.writeString(source, "package", StandardCharsets.UTF_8);
        return source;
    }

    private static ObjectMetadata metadata(long size, String sha256) {
        ObjectMetadata metadata = new ObjectMetadata();
        metadata.setContentLength(size);
        metadata.addUserMetadata("sha256", sha256);
        return metadata;
    }
}
