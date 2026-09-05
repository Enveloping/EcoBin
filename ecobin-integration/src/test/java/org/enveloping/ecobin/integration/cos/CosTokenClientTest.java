package org.enveloping.ecobin.integration.cos;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class CosTokenClientTest {

    @Test
    void routesPhotosToPublicPhotoBucketAndFirmwareToPrivateUpdateBucket() {
        CosProperties photos = photoProperties();
        BusinessReleaseArtifactProperties updates = updateProperties();
        CosTokenClient client = new CosTokenClient(photos, updates);

        CosTokenClient.GrantTarget photo = client.grantTarget(false);
        CosTokenClient.GrantTarget firmware = client.grantTarget(true);

        assertThat(photo.bucketName()).isEqualTo("photo-1250000000");
        assertThat(photo.baseUrl()).isEqualTo(
                "https://photo-1250000000.cos.ap-beijing.myqcloud.com");
        assertThat(photo.secretId()).isEqualTo("photo-secret-id");

        assertThat(firmware.bucketName()).isEqualTo(
                "update-package-1250000000");
        assertThat(firmware.baseUrl()).isEqualTo(
                "https://update-package-1250000000.cos.ap-beijing.myqcloud.com");
        assertThat(firmware.secretId()).isEqualTo("update-secret-id");
    }

    @Test
    void refusesFirmwareGrantWhenPrivateDownloadLocationIsMissing() {
        BusinessReleaseArtifactProperties updates = updateProperties();
        updates.setDownloadBaseUrl("");
        CosTokenClient client = new CosTokenClient(
                photoProperties(), updates);

        assertThatThrownBy(() -> client.grantTarget(true))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("私有更新包 COS");
    }

    private static CosProperties photoProperties() {
        CosProperties properties = new CosProperties();
        properties.setSecretId("photo-secret-id");
        properties.setSecretKey("photo-secret-key");
        properties.setRegion("ap-beijing");
        properties.setBucketName("photo-1250000000");
        properties.setBaseUrl(
                "https://photo-1250000000.cos.ap-beijing.myqcloud.com");
        properties.setDurationSeconds(1800);
        return properties;
    }

    private static BusinessReleaseArtifactProperties updateProperties() {
        BusinessReleaseArtifactProperties properties =
                new BusinessReleaseArtifactProperties();
        properties.setSecretId("update-secret-id");
        properties.setSecretKey("update-secret-key");
        properties.setRegion("ap-beijing");
        properties.setBucketName("update-package-1250000000");
        properties.setDownloadBaseUrl(
                "https://update-package-1250000000.cos.ap-beijing.myqcloud.com");
        return properties;
    }
}
