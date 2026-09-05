package org.enveloping.ecobin.integration.cos;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Data
@Component
@ConfigurationProperties(prefix = "ecobin.device.business-release")
public class BusinessReleaseArtifactProperties {

    private String secretId;
    private String secretKey;
    private String region;
    private String bucketName;
    private String basePrefix = "edge-runtime/releases";
    private String downloadBaseUrl = "";
    private String fakeDirectory = java.nio.file.Path.of(
            System.getProperty("java.io.tmpdir"),
            "ecobin-business-release-artifacts").toString();
    private String signingPublicKeysDirectory = "";
    private boolean remoteDispatchEnabled = false;

    public boolean isCosConfigured() {
        return present(secretId)
                && present(secretKey)
                && present(region)
                && present(bucketName)
                && "edge-runtime/releases".equals(basePrefix);
    }

    public boolean isRemoteDispatchConfigured() {
        return isDownloadLocationConfigured();
    }

    public boolean isDownloadLocationConfigured() {
        return isCosConfigured()
                && present(downloadBaseUrl)
                && downloadBaseUrl.startsWith("https://")
                && !downloadBaseUrl.endsWith("/");
    }

    private static boolean present(String value) {
        return value != null && !value.isBlank();
    }
}
