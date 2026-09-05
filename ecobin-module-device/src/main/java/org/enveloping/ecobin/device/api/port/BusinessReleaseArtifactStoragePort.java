package org.enveloping.ecobin.device.api.port;

import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;

/** Private, immutable storage owned by the business-release control plane. */
public interface BusinessReleaseArtifactStoragePort {

    Readiness readiness();

    void storeImmutable(
            String objectKey,
            Path source,
            String sha256,
            long size);

    void download(String objectKey, Path target);

    default DownloadAuthorization issueReadAuthorization(
            String objectKey,
            Duration validity) {
        throw new UnsupportedOperationException(
                "business release download authorization is unavailable");
    }

    record Readiness(boolean available, String message) {
    }

    record DownloadAuthorization(String url, Instant expiresAt) {
        public DownloadAuthorization {
            if (url == null || url.isBlank() || expiresAt == null) {
                throw new IllegalArgumentException(
                        "download authorization must be complete");
            }
        }
    }
}
