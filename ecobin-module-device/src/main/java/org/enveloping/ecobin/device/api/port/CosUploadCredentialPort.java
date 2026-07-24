package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.CosUploadCredential;

/**
 * 设备照片直传所需的临时凭证能力。
 */
public interface CosUploadCredentialPort {

    CosUploadCredential issue(String deviceSn, Integer doorIndex);
}
