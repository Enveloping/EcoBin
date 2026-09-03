package org.enveloping.ecobin.device.api.port;

import java.nio.file.Path;

/** Private, immutable storage owned by the business-release control plane. */
public interface BusinessReleaseArtifactStoragePort {

    Readiness readiness();

    void storeImmutable(
            String objectKey,
            Path source,
            String sha256,
            long size);

    void download(String objectKey, Path target);

    record Readiness(boolean available, String message) {
    }
}
