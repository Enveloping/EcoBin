package org.enveloping.ecobin.integration.fake;

import org.enveloping.ecobin.device.api.port.CosUploadCredentialPort;
import org.enveloping.ecobin.device.api.result.CosUploadCredential;

import java.time.Instant;

/**
 * 只返回不可用于真实 COS 的保留域名和假凭证。
 */
public final class FakeCosUploadCredentialAdapter
        implements CosUploadCredentialPort {

    @Override
    public CosUploadCredential issue(String deviceSn, Integer doorIndex) {
        long start = Instant.now().getEpochSecond();
        return new CosUploadCredential(
                "FAKE_ONLY_TMP_SECRET_ID",
                "FAKE_ONLY_TMP_SECRET_KEY",
                "FAKE_ONLY_SESSION_TOKEN",
                start,
                start + 300,
                "fake-only-bucket",
                "fake-only-region",
                "https://cos.invalid");
    }
}
